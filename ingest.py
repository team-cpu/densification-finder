"""
Canton-wide ingestion.

Runs the cascade for every Aargau municipality and writes the result to
`results.sqlite`, so the interface can answer instantly instead of making the
user wait out a full pass.

No PostGIS. Geometry is only needed while computing the parcel/zone
intersections; once those are resolved the result is tabular, and a single-user
MVP gains nothing from a spatial database it would otherwise have to run. That
is a deliberate departure from the brief's Part 3.4.

Resumable: a municipality already present in the database is skipped unless
--force is given, so an interrupted run costs only the municipality it was on.

    .venv/bin/python ingest.py            # all municipalities with an AZ
    .venv/bin/python ingest.py --only 4117 4012
    .venv/bin/python ingest.py --force
"""
import glob
import json
import os
import re
import sqlite3
import sys
import time
import urllib.parse
import urllib.request

import paths
import land_cover as LC
import workflow as WF

HERE = paths.HERE
DATA = paths.DATA
DB = paths.DB

WFS = "https://geodienste.ch/db/av_0/deu"


def municipality_names():
    """The zone dataset has no municipality name — GDEBez is the zone's own
    designation. GWR carries the real name in GGDENAME."""
    names = {}
    import csv as _csv
    with open(os.path.join(DATA, "gwr", "gebaeude_batiment_edificio.csv"),
              newline="", encoding="utf-8", errors="replace") as fh:
        for row in _csv.DictReader(fh, delimiter="\t"):
            b = (row.get("GGDENR") or "").strip()
            if b.isdigit() and b not in names:
                names[b] = (row.get("GGDENAME") or "").strip()
    return {int(k): v for k, v in names.items()}


def municipalities_with_az(canton="AG"):
    """Municipalities publishing any utilization figure at all — not only an
    Ausnützungsziffer. A commune zoned entirely by Überbauungsziffer used to
    drop out here, before anything could report why."""
    import metrics as M

    cfg = M.CANTONS[canton]
    db = sqlite3.connect(glob.glob(os.path.join(DATA, cfg.dataset))[0])
    t = [r[0] for r in db.execute("SELECT table_name FROM gpkg_contents")][0]
    any_metric = " OR ".join(f"COALESCE({M.METRICS[k].column},0)>0" for k in cfg.metrics)
    rows = db.execute(
        f'SELECT {cfg.bfs_column}, COUNT(*) FROM "{t}" WHERE {any_metric} '
        f'GROUP BY {cfg.bfs_column} ORDER BY {cfg.bfs_column}'
    )
    names = municipality_names()
    return [(int(r[0]), names.get(int(r[0]), f"BFS {r[0]}"), r[1]) for r in rows]


def fetch_parcels(bfs, retries=3):
    """Pull one municipality's parcels. ~10 s and ~4 MB for a typical commune."""
    path = os.path.join(DATA, f"parcels_{bfs}.xml")
    if os.path.exists(path) and os.path.getsize(path) > 1000:
        return path

    flt = (
        '<fes:Filter xmlns:fes="http://www.opengis.net/fes/2.0">'
        "<fes:PropertyIsEqualTo><fes:ValueReference>BFSNr</fes:ValueReference>"
        f"<fes:Literal>{bfs}</fes:Literal></fes:PropertyIsEqualTo></fes:Filter>"
    )
    q = urllib.parse.urlencode(
        {
            "SERVICE": "WFS",
            "VERSION": "2.0.0",
            "REQUEST": "GetFeature",
            "TYPENAMES": "ms:RESF",
            "COUNT": "40000",
            "FILTER": flt,
        }
    )
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(f"{WFS}?{q}", timeout=300) as r:
                body = r.read()
            if b"<ms:RESF" not in body and b'numberReturned="0"' not in body:
                raise RuntimeError("unexpected WFS payload")
            open(path, "wb").write(body)
            return path
        except Exception:
            if attempt == retries - 1:
                raise
            time.sleep(5 * (attempt + 1))


# Column -> declared type. Kept as data so a database written by an older
# version is widened in place rather than needing a full re-ingest to be
# readable; the new columns simply stay NULL until the next run fills them.
COLUMNS = [
    ("bfs", "INTEGER NOT NULL"), ("municipality", "TEXT"), ("parcel", "TEXT NOT NULL"),
    ("egrid", "TEXT"), ("address", "TEXT"), ("built", "TEXT"), ("use_class", "TEXT"),
    ("zone", "TEXT"), ("az", "REAL"), ("metric", "TEXT"),
    ("sv_e", "REAL"), ("sv_n", "REAL"),
    ("unconvertible", "REAL"), ("e", "REAL"), ("n", "REAL"),
    ("area", "REAL"), ("buildable", "REAL"),
    ("zone_share", "REAL"), ("transport_share", "REAL"),
    ("buildings", "INTEGER"), ("existing", "REAL"),
    ("delta", "REAL"), ("heritage", "TEXT"), ("design_plan", "INTEGER"),
    ("calculated_at", "TEXT"),
]

RUN_COLUMNS = [
    ("bfs", "INTEGER PRIMARY KEY"), ("municipality", "TEXT"),
    ("parcels", "INTEGER"), ("assessed", "INTEGER"),
    ("candidates", "INTEGER"), ("no_az", "INTEGER"),
    ("seconds", "REAL"), ("finished_at", "TEXT"), ("reasons", "TEXT"),
]

OEREB_COLUMNS = [
    ("egrid", "TEXT PRIMARY KEY"), ("hard", "TEXT"), ("notable", "TEXT"),
    ("error", "TEXT"), ("checked_at", "TEXT"),
    # The rest of the extract, as JSON: the official zone split, the plans and
    # the BNO that govern this parcel, the cantonal legal bases, and the
    # responsible office. One blob rather than five tables — nothing queries
    # inside it, the detail view reads the whole thing for one parcel at a time,
    # and the shape is the cadastre's to change, not ours.
    ("details", "TEXT"),
]

# Kept outside ``parcel_results`` because that table is replaced whenever a
# municipality is recomputed.  These are user decisions, not calculated parcel
# attributes, and must survive a new data import.
WORKFLOW_COLUMNS = [
    ("bfs", "INTEGER NOT NULL"),
    ("parcel", "TEXT NOT NULL"),
    ("saved", "INTEGER NOT NULL DEFAULT 0"),
    ("hidden", "INTEGER NOT NULL DEFAULT 0"),
    ("owner_name", "TEXT NOT NULL DEFAULT ''"),
    (
        "contact_status",
        f"TEXT NOT NULL DEFAULT '{WF.DEFAULT_CONTACT_STATUS}'",
    ),
    ("updated_at", "TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP"),
    # The acquisition fields. Every one carries DEFAULT '' rather than being
    # left nullable: `_add_missing_columns` strips NOT NULL but keeps a quoted
    # default, and SQLite backfills existing rows from it, so a lead saved
    # before this release arrives as an empty string like every other. Absent
    # is one value here, not two.
    ("due_date", "TEXT NOT NULL DEFAULT ''"),
    ("last_contact", "TEXT NOT NULL DEFAULT ''"),
    ("next_step", "TEXT NOT NULL DEFAULT ''"),
    ("note", "TEXT NOT NULL DEFAULT ''"),
    ("contact_person", "TEXT NOT NULL DEFAULT ''"),
    ("phone", "TEXT NOT NULL DEFAULT ''"),
    ("email", "TEXT NOT NULL DEFAULT ''"),
]

# Saved screening filter presets. Outside `parcel_results` for the same reason
# `parcel_workflow` is: "Wohnzone, 800 m² potential, Bezirk Horgen" is a user
# decision — a research position worth returning to — not calculated data, and
# must survive a cascade recompute intact.
SAVED_SEARCH_COLUMNS = [
    ("name", "TEXT NOT NULL"),
    ("filters", "TEXT NOT NULL"),
    ("created_at", "TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP"),
]

# Organisation data belongs to the application, not to a parcel import.  It is
# kept in the same persistent SQLite file so Team/Einstellungen survive both a
# cascade recompute and a container redeploy.  The single profile row is keyed
# explicitly instead of relying on "first row wins" semantics.
ORGANISATION_PROFILE_COLUMNS = [
    ("id", "INTEGER PRIMARY KEY CHECK (id = 1)"),
    ("name", "TEXT NOT NULL DEFAULT ''"),
    ("legal_name", "TEXT NOT NULL DEFAULT ''"),
    ("street", "TEXT NOT NULL DEFAULT ''"),
    ("postcode", "TEXT NOT NULL DEFAULT ''"),
    ("city", "TEXT NOT NULL DEFAULT ''"),
    ("uid", "TEXT NOT NULL DEFAULT ''"),
    ("billing_email", "TEXT NOT NULL DEFAULT ''"),
    ("weekly_digest", "INTEGER NOT NULL DEFAULT 1"),
    ("due_reminders", "INTEGER NOT NULL DEFAULT 1"),
    ("enforce_2fa", "INTEGER NOT NULL DEFAULT 0"),
    ("shared_calculations", "INTEGER NOT NULL DEFAULT 1"),
    ("updated_at", "TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP"),
]

ORGANISATION_MEMBER_COLUMNS = [
    ("id", "INTEGER PRIMARY KEY AUTOINCREMENT"),
    ("name", "TEXT NOT NULL DEFAULT ''"),
    ("email", "TEXT NOT NULL UNIQUE"),
    ("role", "TEXT NOT NULL DEFAULT 'Bearbeiter'"),
    ("status", "TEXT NOT NULL DEFAULT 'pending'"),
    ("activity", "TEXT NOT NULL DEFAULT '—'"),
    ("is_self", "INTEGER NOT NULL DEFAULT 0"),
    ("created_at", "TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP"),
    ("updated_at", "TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP"),
]


def _column_definitions(columns):
    return ",\n            ".join(
        f"{name:<13} {declaration}" for name, declaration in columns
    )


def _add_missing_columns(con, table, columns):
    """Widen a table created by an older application release in place."""
    have = {row[1] for row in con.execute(f"PRAGMA table_info({table})")}
    for name, declaration in columns:
        if name not in have:
            # SQLite cannot add NOT NULL or PRIMARY KEY constraints to a
            # populated table. New application columns stay nullable until the
            # next ingestion fills them.
            compatible = declaration.replace(" NOT NULL", "").replace(
                " PRIMARY KEY", ""
            ).replace(" DEFAULT CURRENT_TIMESTAMP", "")
            con.execute(f"ALTER TABLE {table} ADD COLUMN {name} {compatible}")


def _workflow_statuses(con):
    """The status values the stored `parcel_workflow` CHECK constraint allows.

    An empty set means the table has no such constraint — either it predates
    the contact status entirely, or it was created by hand. Both are reasons to
    rebuild: an unconstrained column is not the schema this release declares.
    """
    row = con.execute(
        "SELECT sql FROM sqlite_master "
        "WHERE type = 'table' AND name = 'parcel_workflow'"
    ).fetchone()
    if not row or not row[0]:
        return set()
    clause = re.search(
        r"CHECK\s*\(\s*contact_status\s+IN\s*\((.*?)\)\s*\)", row[0], re.S | re.I
    )
    if not clause:
        return set()
    return set(re.findall(r"'([^']*)'", clause.group(1)))


def _rebuild_workflow_check(con):
    """Copy `parcel_workflow` when its status constraint is out of date.

    Compared as sets, so reordering `CONTACT_STATUS_LABELS` — which is also the
    board's column order — is never mistaken for a schema change.

    Returns whether the table was rebuilt.
    """
    if _workflow_statuses(con) == set(WF.CONTACT_STATUS_LABELS):
        return False

    have = {row[1] for row in con.execute("PRAGMA table_info(parcel_workflow)")}
    carried = [name for name, _ in WORKFLOW_COLUMNS if name in have]
    carried_cols = ", ".join(carried)
    # `_add_missing_columns` strips `DEFAULT CURRENT_TIMESTAMP` when it widens a
    # table (SQLite's ADD COLUMN only accepts a constant default), so a legacy
    # row that just gained `updated_at` there carries NULL, not a timestamp.
    # The rebuilt table declares the column NOT NULL, so that NULL has to be
    # replaced on the way across rather than copied straight through.
    select_exprs = ", ".join(
        f"COALESCE({name}, CURRENT_TIMESTAMP)" if name == "updated_at" else name
        for name in carried
    )
    statuses = ", ".join(f"'{status}'" for status in WF.CONTACT_STATUS_LABELS)
    before = con.execute("SELECT COUNT(*) FROM parcel_workflow").fetchone()[0]

    # SQLite runs DDL inside a transaction, but python's sqlite3 opens implicit
    # transactions for DML only — so CREATE and DROP would each commit on their
    # own and a failure halfway through would leave no table at all. Explicit
    # control for the duration of the copy, restored afterwards.
    previous = con.isolation_level
    con.isolation_level = None
    try:
        con.execute("BEGIN IMMEDIATE")
        try:
            con.execute(
                f"""
                CREATE TABLE parcel_workflow_new (
                    {_column_definitions(WORKFLOW_COLUMNS)},
                    CHECK (saved IN (0, 1)),
                    CHECK (hidden IN (0, 1)),
                    CHECK (contact_status IN ({statuses})),
                    PRIMARY KEY (bfs, parcel)
                )
                """
            )
            # Both halves name their columns. `SELECT *` would silently shift
            # every value one place the first time the two column orders
            # diverge, and the shift would be invisible until someone read a
            # phone number out of the note field.
            con.execute(
                f"INSERT INTO parcel_workflow_new ({carried_cols}) "
                f"SELECT {select_exprs} FROM parcel_workflow"
            )
            after = con.execute(
                "SELECT COUNT(*) FROM parcel_workflow_new"
            ).fetchone()[0]
            if after != before:
                raise RuntimeError(
                    "parcel_workflow rebuild would lose leads: "
                    f"{before} rows in, {after} rows out"
                )
            con.execute("DROP TABLE parcel_workflow")
            con.execute(
                "ALTER TABLE parcel_workflow_new RENAME TO parcel_workflow"
            )
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
    finally:
        con.isolation_level = previous
    return True


def schema(con):
    parcel_cols = _column_definitions(COLUMNS)
    run_cols = _column_definitions(RUN_COLUMNS)
    oereb_cols = _column_definitions(OEREB_COLUMNS)
    workflow_cols = _column_definitions(WORKFLOW_COLUMNS)
    saved_search_cols = _column_definitions(SAVED_SEARCH_COLUMNS)
    organisation_profile_cols = _column_definitions(ORGANISATION_PROFILE_COLUMNS)
    organisation_member_cols = _column_definitions(ORGANISATION_MEMBER_COLUMNS)
    statuses = ", ".join(f"'{status}'" for status in WF.CONTACT_STATUS_LABELS)
    con.executescript(
        f"""
        CREATE TABLE IF NOT EXISTS parcel_results (
            {parcel_cols},
            PRIMARY KEY (bfs, parcel)
        );
        -- `reasons` is a JSON object mapping each reason to a parcel count.
        -- §3.5 step 1 asks for "not assessable" to be marked rather than
        -- skipped silently, and a lump count cannot say whether a commune is
        -- missing because it publishes no figure or because the figure it does
        -- publish cannot be converted into floor area.
        CREATE TABLE IF NOT EXISTS runs (
            {run_cols}
        );
        -- ÖREB answers, cached so the shortlist check costs one call per parcel
        -- ever rather than one per click. `hard` non-empty means excluded.
        CREATE TABLE IF NOT EXISTS oereb_cache (
            {oereb_cols}
        );
        -- Saved leads, hidden leads and owner-contact progress. Deliberately no
        -- foreign key: parcel_results is deleted and reinserted during a
        -- recompute, while these decisions must remain intact.
        CREATE TABLE IF NOT EXISTS parcel_workflow (
            {workflow_cols},
            CHECK (saved IN (0, 1)),
            CHECK (hidden IN (0, 1)),
            CHECK (contact_status IN ({statuses})),
            PRIMARY KEY (bfs, parcel)
        );
        -- Keyed by name, so saving under an existing name replaces it rather
        -- than accumulating near-duplicates silently. New table, no legacy
        -- rows to migrate — CREATE TABLE IF NOT EXISTS is all it needs.
        CREATE TABLE IF NOT EXISTS saved_searches (
            {saved_search_cols},
            PRIMARY KEY (name)
        );
        CREATE TABLE IF NOT EXISTS organisation_profile (
            {organisation_profile_cols},
            CHECK (weekly_digest IN (0, 1)),
            CHECK (due_reminders IN (0, 1)),
            CHECK (enforce_2fa IN (0, 1)),
            CHECK (shared_calculations IN (0, 1))
        );
        CREATE TABLE IF NOT EXISTS organisation_members (
            {organisation_member_cols},
            CHECK (role IN ('Inhaber', 'Bearbeiter', 'Leseweise')),
            CHECK (status IN ('active', 'pending')),
            CHECK (is_self IN (0, 1))
        );
        """
    )
    _add_missing_columns(con, "parcel_results", COLUMNS)
    _add_missing_columns(con, "runs", RUN_COLUMNS)
    _add_missing_columns(con, "oereb_cache", OEREB_COLUMNS)
    _add_missing_columns(con, "parcel_workflow", WORKFLOW_COLUMNS)
    _add_missing_columns(con, "saved_searches", SAVED_SEARCH_COLUMNS)
    _add_missing_columns(con, "organisation_profile", ORGANISATION_PROFILE_COLUMNS)
    _add_missing_columns(con, "organisation_members", ORGANISATION_MEMBER_COLUMNS)
    con.execute(
        "INSERT OR IGNORE INTO organisation_profile (id, name) VALUES (1, '')"
    )
    # After widening, because the copy carries whichever columns the widened
    # table has; before the indexes, because DROP TABLE takes its indexes with
    # it and the CREATE INDEX statements below put them back.
    _rebuild_workflow_check(con)
    # Create indexes after widening so an index introduced alongside a column
    # never runs before that column exists on a legacy database.
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_delta ON parcel_results(delta DESC)"
    )
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_workflow_saved ON parcel_workflow(saved)"
    )
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_workflow_hidden ON parcel_workflow(hidden)"
    )
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_org_member_status "
        "ON organisation_members(status)"
    )
    con.commit()


def geodata_available(canton="AG"):
    """Whether a full recompute is possible in this environment.

    The deployment carries `results.sqlite` but not the ~600 MB of source data,
    so the cascade cannot run there — only the ÖREB half of the pipeline can.
    Asked rather than attempted: globbing an absent dataset raises IndexError,
    which is what the Run button used to die on when hosted.
    """
    import metrics as M

    return bool(glob.glob(os.path.join(DATA, M.CANTONS[canton].dataset)))


def recompute(progress=None, built_after=None):
    """Re-run the cascade over every municipality and rewrite the results.

    Callable from the interface, so its Run button can do what the brief asks
    rather than only the ÖREB half. Deliberately does NOT re-fetch parcels from
    the WFS: that is ~10 s per municipality against 0.1 s to recompute one, and
    the cadastre does not change between two clicks. Deleting `data/parcels_*.xml`
    forces a fresh download on the next run.

    `built_after` is the year-built cutoff the brief lists among the filter
    inputs. It has to arrive here rather than being a display filter: the age
    rule runs inside the cascade (a parcel whose buildings are all certainly
    newer never becomes a row), so changing it means recomputing, and the stored
    table always reflects the cutoff of the last run.

    Measured: 1.7 s to build the engine, 16.6 s for all 163 municipalities.
    """
    con = sqlite3.connect(DB)
    schema(con)
    if not geodata_available():
        con.close()
        return None   # caller decides what to say; None ≠ "0 candidates"
    targets = municipalities_with_az()

    import cascade

    engine = cascade.Engine(built_after=built_after)
    names = [n for n, _ in COLUMNS]
    for i, (bfs, name, _) in enumerate(targets, 1):
        if not os.path.exists(os.path.join(DATA, f"parcels_{bfs}.xml")):
            continue  # never downloaded; leave whatever is already stored
        try:
            LC.fetch(bfs)
            res = engine.run(bfs)
        except Exception:
            continue
        con.execute("DELETE FROM parcel_results WHERE bfs=?", (bfs,))
        con.executemany(
            f"INSERT OR REPLACE INTO parcel_results ({','.join(names)}) "
            f"VALUES ({','.join(':' + n for n in names)})",
            [_row(bfs, name, r) for r in res["candidates"]],
        )
        con.execute(
            "INSERT OR REPLACE INTO runs "
            "(bfs, municipality, parcels, assessed, candidates, no_az, seconds, "
            "finished_at, reasons) VALUES (?,?,?,?,?,?,?,datetime('now'),?)",
            (bfs, name, res["parcels"], res["assessed"], len(res["candidates"]),
             res["no_az"], 0, json.dumps(res["unassessable"], ensure_ascii=False)),
        )
        if progress:
            progress(i / len(targets), f"{name} ({i}/{len(targets)})")
    con.commit()
    total = con.execute("SELECT COUNT(*) FROM parcel_results").fetchone()[0]
    con.close()
    return total


def _row(bfs, name, r):
    return {
        "bfs": bfs, "municipality": name, "parcel": r["parcel"], "egrid": r["egrid"],
        "address": r["address"], "built": r["built"], "use_class": r["use"],
        "zone": r["zone"], "az": r["az"], "metric": r["metric"],
        "sv_e": r["sv_east"], "sv_n": r["sv_north"],
        "unconvertible": r["unconvertible"], "e": r["east"], "n": r["north"],
        "area": r["area"], "buildable": r["buildable"],
        "zone_share": r["share"], "transport_share": r["transport_share"],
        "buildings": r["n"], "existing": r["existing"],
        "delta": r["delta"], "heritage": r["heritage"],
        "design_plan": int(r["design_plan"]),
        "calculated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


def main():
    args = sys.argv[1:]
    force = "--force" in args
    only = []
    if "--only" in args:
        only = [int(a) for a in args[args.index("--only") + 1:] if a.isdigit()]

    con = sqlite3.connect(DB)
    schema(con)
    done = {r[0] for r in con.execute("SELECT bfs FROM runs")}

    targets = municipalities_with_az()
    if only:
        targets = [t for t in targets if t[0] in only]

    todo = [t for t in targets if force or t[0] not in done]
    print(f"  {len(targets)} municipalities with an AZ | {len(todo)} to process")

    import cascade  # imported late: it loads ~100 MB of constraint layers

    engine = cascade.Engine()
    for i, (bfs, name, _) in enumerate(todo, 1):
        t0 = time.time()
        try:
            fetch_parcels(bfs)
            LC.fetch(bfs)
            res = engine.run(bfs)
        except Exception as exc:
            print(f"  [{i}/{len(todo)}] {bfs} {name[:22]:22} FAILED: {str(exc)[:60]}")
            continue

        con.execute("DELETE FROM parcel_results WHERE bfs=?", (bfs,))
        names = [n for n, _ in COLUMNS]
        con.executemany(
            f"INSERT OR REPLACE INTO parcel_results ({','.join(names)}) "
            f"VALUES ({','.join(':' + n for n in names)})",
            [
                _row(bfs, name, r)
                for r in res["candidates"]
            ],
        )
        con.execute(
            "INSERT OR REPLACE INTO runs "
            "(bfs, municipality, parcels, assessed, candidates, no_az, seconds, "
            "finished_at, reasons) VALUES (?,?,?,?,?,?,?,datetime('now'),?)",
            (bfs, name, res["parcels"], res["assessed"], len(res["candidates"]),
             res["no_az"], round(time.time() - t0, 1),
             json.dumps(res["unassessable"], ensure_ascii=False)),
        )
        con.commit()
        print(
            f"  [{i}/{len(todo)}] {bfs} {name[:22]:22} "
            f"{res['parcels']:>5} parcels -> {len(res['candidates']):>4} candidates "
            f"({time.time()-t0:.0f}s)"
        )

    tot = con.execute("SELECT COUNT(*) FROM parcel_results").fetchone()[0]
    muni = con.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
    print(f"\n  {tot:,} candidates across {muni} municipalities -> {DB}")


if __name__ == "__main__":
    main()
