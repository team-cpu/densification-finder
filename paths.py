"""
Where the data and the results live.

Locally both sit next to the code, which is what every module assumed. Deployed,
they have to sit on a mounted volume instead: the source geodata is ~600 MB of
GeoPackage, XML and CSV, far too much for an image layer, and a SQLite file
inside the container would be discarded on every redeploy.

    DENSIFICATION_DATA   directory holding the cantonal downloads
    DENSIFICATION_DB     path to results.sqlite

Both default to the repository, so nothing changes for local work.
"""
import os
import re
import shutil
import sqlite3
import tempfile
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))

DATA = os.environ.get("DENSIFICATION_DATA") or os.path.join(HERE, "data")
DB = os.environ.get("DENSIFICATION_DB") or os.path.join(HERE, "results.sqlite")

#: The copy committed to git — ~20,600 assessed candidates. Used to seed an
#: empty volume so a first deploy shows a working list immediately, rather than
#: "no results yet" from an app that cannot recompute until the geodata has been
#: uploaded separately.
SEED_DB = os.path.join(HERE, "results.sqlite")


def on_persistent_disk():
    """Whether DB sits on a mounted volume rather than the container filesystem.

    Worth asking because the failure is silent: without a volume the app runs
    correctly and simply forgets every ÖREB answer on each redeploy, which looks
    like the cadastre being slow rather than like a misconfiguration. Saved leads
    and contact progress would disappear as well. The path alone cannot tell —
    DENSIFICATION_DB points at /data either way — so this reads the mount table.

    None when the question does not apply (no /proc, i.e. not on Linux).
    """
    if not os.path.exists("/proc/mounts"):
        return None
    target = os.path.dirname(os.path.abspath(DB))
    try:
        with open("/proc/mounts") as fh:
            mounts = {line.split()[1] for line in fh if len(line.split()) > 1}
    except OSError:
        return None
    return target in mounts


def ensure_db():
    """Copy the committed results into place if the configured path has none.

    Only ever creates; a populated file is left alone, so a recompute on the
    volume is never overwritten by the older copy baked into the image.

    "Has none" deliberately means empty-or-absent rather than absent. Any
    `sqlite3.connect()` on a missing path creates a zero-byte file as a side
    effect — a stray diagnostic is enough — and treating that as "already
    seeded" would leave the volume permanently broken with no way back except
    deleting it by hand.
    """
    if DB == SEED_DB or not os.path.exists(SEED_DB):
        return False
    populated = os.path.exists(DB) and os.path.getsize(DB) > 0
    # DENSIFICATION_RESEED=1 brings the image's parcel data onto the volume,
    # which is how a recompute done locally and committed reaches the
    # deployment. Without it, updating the hosted data would mean deleting a
    # file on a live volume by hand. Only the seed-owned tables are replaced:
    # the file also holds everything people entered, which a data refresh must
    # not touch. Set it for one deploy, then unset — each restart with it on
    # redoes the swap.
    if os.environ.get("DENSIFICATION_RESEED") == "1":
        if populated:
            _reseed_keeping_work_data()
        else:
            _copy_seed_atomically()
        return True
    if populated:
        return False
    _copy_seed_atomically()
    return True


#: Tables the committed seed owns: ingest.py recomputes them as a unit and a
#: requested reseed replaces them wholesale. Every other table in the live
#: database belongs to the people using the app — saved leads and contact
#: progress, the team and its invitations, settings, saved searches, shared
#: calculations, the mail outbox — and survives a reseed untouched.
SEED_TABLES = ("parcel_results", "runs")


def _reseed_keeping_work_data():
    """Swap in the image's parcel data, keeping everything people entered.

    The live database is snapshotted through SQLite's backup API (correct in any
    journal mode), the seed-owned tables are replaced inside the snapshot, and
    the snapshot then replaces the live file in one atomic rename: readers see
    the old complete database or the new one, never a mix, and a failure part
    way leaves the live file as it was.

    ÖREB answers cost a paid call each, so the volume's cache is kept and only
    answers the seed has and the volume lacks are added.
    """
    directory = os.path.dirname(DB) or "."
    fd, temporary = tempfile.mkstemp(
        prefix=".densification-reseed-", suffix=".sqlite", dir=directory
    )
    os.close(fd)
    try:
        # uri=True so the seed can be attached read-only below.
        snapshot = sqlite3.connect(Path(temporary).resolve().as_uri(), uri=True)
        try:
            live = sqlite3.connect(DB)
            try:
                live.backup(snapshot)
            finally:
                live.close()
            snapshot.isolation_level = None
            snapshot.execute(
                "ATTACH DATABASE ? AS seed",
                (Path(SEED_DB).resolve().as_uri() + "?mode=ro",),
            )
            snapshot.execute("BEGIN")
            for table in SEED_TABLES:
                _replace_with_seed_table(snapshot, table)
            _add_missing_seed_rows(snapshot, "oereb_cache")
            snapshot.execute("COMMIT")
            snapshot.execute("DETACH DATABASE seed")
        finally:
            snapshot.close()
        shutil.copymode(DB, temporary)
        os.replace(temporary, DB)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


#: Identifier grammar this module is willing to place inside a SQL statement.
#: Every identifier that reaches a statement is either a fixed literal in a
#: statement dict below or a name that passed this pattern and got
#: double-quoted, so no SQL text here is built from unchecked input.
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _quote_ident(name):
    """Double-quote a SQLite identifier, refusing anything outside the grammar."""
    if not _IDENT.fullmatch(name):
        raise ValueError(f"refusing unsafe SQL identifier: {name!r}")
    return f'"{name}"'


#: `sqlite_master` lookups per attached schema, as fixed statements. The only
#: variable left is the table name, bound as a parameter.
_TABLE_EXISTS = {
    "main": "SELECT 1 FROM main.sqlite_master "
    "WHERE type = 'table' AND name = ?",
    "seed": "SELECT 1 FROM seed.sqlite_master "
    "WHERE type = 'table' AND name = ?",
}


def _has_table(con, schema, table):
    statement = _TABLE_EXISTS.get(schema)
    if statement is None:
        raise ValueError(f"unknown attached schema: {schema!r}")
    return con.execute(statement, (table,)).fetchone() is not None


#: Column listings per attached schema, as fixed statements. The schema is
#: bound as the pragma's second argument: in this SQLite build a bare
#: `pragma_table_info(?)` — and even `seed.pragma_table_info(?)` — resolves
#: the table in `main`, so only the two-argument form actually reads the
#: attached schema.
_TABLE_COLUMNS = {
    "main": "SELECT name FROM pragma_table_info(?, ?)",
    "seed": "SELECT name FROM pragma_table_info(?, ?)",
}


def _table_columns(con, schema, table):
    """Column names of `schema.table`, in table-definition order."""
    statement = _TABLE_COLUMNS.get(schema)
    if statement is None:
        raise ValueError(f"unknown attached schema: {schema!r}")
    return [row[0] for row in con.execute(statement, (table, schema))]


#: The swap statements per seed-owned table, fixed rather than formatted, so
#: the table name never travels inside built-up SQL text. A caller naming any
#: other table is turned away instead of interpolated.
_RESEED_STATEMENTS = {
    "parcel_results": (
        'DROP TABLE IF EXISTS main."parcel_results"',
        'INSERT INTO main."parcel_results" SELECT * FROM seed."parcel_results"',
    ),
    "runs": (
        'DROP TABLE IF EXISTS main."runs"',
        'INSERT INTO main."runs" SELECT * FROM seed."runs"',
    ),
}


def _replace_with_seed_table(con, table):
    """Give `main.table` exactly the seed's definition, indexes and rows."""
    statements = _RESEED_STATEMENTS.get(table)
    if statements is None:
        raise ValueError(f"not a seed-owned table: {table!r}")
    row = con.execute(
        "SELECT sql FROM seed.sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    if row is None:
        return  # an older seed without this table leaves the live one alone
    indexes = [
        sql for (sql,) in con.execute(
            "SELECT sql FROM seed.sqlite_master "
            "WHERE type='index' AND tbl_name=? AND sql IS NOT NULL",
            (table,),
        )
    ]
    con.execute(statements[0])
    # The CREATE text and the index statements come verbatim from the committed
    # seed's own sqlite_master — DDL this application's ingest wrote when the
    # seed was built, not anything a caller supplies — so they run as stored.
    con.execute(row[0])  # sqlite_master text is schema-free: it lands in main
    con.execute(statements[1])
    for sql in indexes:
        con.execute(sql)


def _add_missing_seed_rows(con, table):
    """Insert the seed's rows the live table lacks; existing rows win.

    Unlike the wholesale table swap, this merge must survive the two files
    having different column sets (the volume may not have been migrated yet),
    so the shared columns are named explicitly. The names come from each
    file's own schema listing and every one passes the `_quote_ident`
    whitelist before it enters the statement; nothing else in the text is
    variable.
    """
    if not (_has_table(con, "main", table) and _has_table(con, "seed", table)):
        return
    # Each file's listing comes from its own schema; only the shared columns
    # are named, and the bound-parameter pragma above means the table name
    # never appears inside this SQL text either.
    seed_columns = set(_table_columns(con, "seed", table))
    columns = ", ".join(
        _quote_ident(name)
        for name in _table_columns(con, "main", table)
        if name in seed_columns
    )
    target = _quote_ident(table)
    # The column set is the intersection of the two files' actual schemas, so
    # it cannot be fixed at write time; every identifier that enters the text
    # passes the _quote_ident whitelist above, and the table name is re-validated
    # here rather than interpolated unchecked.
    con.execute(
        "INSERT OR IGNORE INTO main." + target + " (" + columns + ") "  # nosec B608
        "SELECT " + columns + " FROM seed." + target
    )


def _copy_seed_atomically():
    """Replace the live database only after the seed copy is complete."""
    directory = os.path.dirname(DB) or "."
    os.makedirs(directory, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        prefix=".densification-seed-", suffix=".sqlite", dir=directory
    )
    os.close(fd)
    try:
        shutil.copy2(SEED_DB, temporary)
        # Same-directory replacement is atomic: readers see either the old
        # complete database or the new complete database, never a partial copy.
        os.replace(temporary, DB)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
