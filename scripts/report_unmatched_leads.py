#!/usr/bin/env python3
"""Report saved leads the upcoming reseed would drop from the board.

`acquisition.leads()` inner-joins saved workflow rows back onto the parcel
result set, so a lead whose (bfs, parcel) is absent from the seed's
`parcel_results` — a municipality that has not been recomputed, or a parcel
that dropped out of the new data — has no card after the reseed even though
its row in `parcel_workflow` survives. This script lists exactly those leads,
with the contact progress entered against them, so they can be followed up
before the swap rather than discovered after it.

Both databases are opened read-only (`file:…?mode=ro`) and the seed is
attached to the live connection, also read-only; nothing writes to either
file. The report is a CSV at 0600, refused if the path already exists, and
the only output on stdout is the row count and the path — the CSV itself
holds personal contact data and belongs wherever the operator decides, not in
the repository, a ticket, or a chat.

Run it against copies of the volume and of the freshly computed seed before
setting DENSIFICATION_RESEED=1:

    python3 scripts/report_unmatched_leads.py \
        --live-db /path/to/volume-copy.sqlite \
        --seed-db /path/to/new-results.sqlite \
        --output /secure/path/unmatched-leads.csv
"""
import argparse
import csv
import os
import sqlite3
import sys
from pathlib import Path

#: Workflow columns without which the question cannot be asked.
REQUIRED_WORKFLOW_COLUMNS = ("bfs", "parcel", "saved")

#: Leading whitespace and control characters can hide a formula prefix from
#: a simple startswith check. Prefix risky cells with a quote for spreadsheet
#: readers while keeping the original value otherwise unchanged.
_FORMULA_PREFIXES = ("=", "+", "-", "@")
_FORMULA_LEADING = " \t\r\n\ufeff"
_FORMULA_CONTROL_PREFIXES = ("\t", "\r", "\n", "\ufeff")


class ReportError(Exception):
    """A clear, operator-facing failure (bad path, bad file, bad schema)."""


def _open_readonly(path, what):
    if not os.path.exists(path):
        raise ReportError(f"{what} database does not exist: {path}")
    uri = Path(path).resolve().as_uri() + "?mode=ro"
    try:
        return sqlite3.connect(uri, uri=True)
    except sqlite3.Error as exc:
        raise ReportError(f"cannot open {what} database read-only: {path}: {exc}")


#: Column listings per schema, as one fixed statement: the schema is bound as
#: the pragma's second argument because in this SQLite build a bare
#: `pragma_table_info(?)` — and even `seed.pragma_table_info(?)` — resolves
#: the table in `main`. No identifier is ever interpolated into SQL text in
#: this script — the only variables anywhere are bound parameters, and column
#: names travel back through the cursor, not through the query.
_TABLE_EXISTS = {
    "main": "SELECT 1 FROM main.sqlite_master "
    "WHERE type = 'table' AND name = ?",
    "seed": "SELECT 1 FROM seed.sqlite_master "
    "WHERE type = 'table' AND name = ?",
}
_COLUMN_LISTING = "SELECT name FROM pragma_table_info(?, ?)"


def _table_columns(con, schema, table):
    """Column names of `schema.table`, or None when the table is absent."""
    if schema not in _TABLE_EXISTS:
        raise ReportError(f"unknown attached schema: {schema!r}")
    if con.execute(_TABLE_EXISTS[schema], (table,)).fetchone() is None:
        return None
    return [name for (name,) in con.execute(_COLUMN_LISTING, (table, schema))]


def _csv_cell(value):
    if value is None:
        return ""
    if isinstance(value, str) and value and (
        value.startswith(_FORMULA_CONTROL_PREFIXES)
        or value.lstrip(_FORMULA_LEADING).startswith(_FORMULA_PREFIXES)
    ):
        return "'" + value
    return value


def collect_unmatched(live_db, seed_db):
    """Rows of saved leads in `live_db` whose parcel is not in the seed.

    Returns (header, rows). Opens both files strictly read-only and attaches
    the seed to the live connection so the anti-join runs in SQL; the keys
    and the anti-join mean no parcel data is loaded wholesale.
    """
    live = _open_readonly(live_db, "live")
    try:
        try:
            live.execute(
                "ATTACH DATABASE ? AS seed",
                (Path(seed_db).resolve().as_uri() + "?mode=ro",),
            )
        except sqlite3.Error as exc:
            raise ReportError(f"cannot attach seed database {seed_db}: {exc}")

        try:
            columns = _table_columns(live, "main", "parcel_workflow")
            if columns is None:
                raise ReportError(
                    f"live database has no parcel_workflow table: {live_db}"
                )
            missing = [c for c in REQUIRED_WORKFLOW_COLUMNS if c not in columns]
            if missing:
                raise ReportError(
                    f"live parcel_workflow is missing required columns "
                    f"{', '.join(missing)}: {live_db}"
                )
            seed_columns = _table_columns(live, "seed", "parcel_results")
            if seed_columns is None:
                raise ReportError(
                    f"seed database has no parcel_results table: {seed_db}"
                )
            for key in ("bfs", "parcel"):
                if key not in seed_columns:
                    raise ReportError(
                        f"seed parcel_results is missing required column "
                        f"'{key}': {seed_db}"
                    )

            # The query names no columns, so nothing dynamic enters the SQL:
            # `SELECT *` plus cursor.description, with the keys moved to the
            # front here in Python. The report then carries whatever contact
            # fields this release has — including none, on a very old volume.
            try:
                cursor = live.execute(
                    "SELECT * FROM parcel_workflow AS w "
                    "WHERE w.saved = 1 AND NOT EXISTS ("
                    "SELECT 1 FROM seed.parcel_results AS p "
                    "WHERE p.bfs = w.bfs AND p.parcel = w.parcel"
                    ") ORDER BY w.bfs, w.parcel"
                )
                found = cursor.fetchall()
            except sqlite3.DatabaseError as exc:
                raise ReportError(
                    f"query failed — is the live file a valid SQLite "
                    f"database? {exc}"
                )
            returned = [d[0] for d in cursor.description]
            ordered = [c for c in REQUIRED_WORKFLOW_COLUMNS if c in returned]
            ordered += [c for c in returned if c not in REQUIRED_WORKFLOW_COLUMNS]
            indexes = [returned.index(c) for c in ordered]
            rows = [tuple(row[i] for i in indexes) for row in found]
            return ordered, rows
        finally:
            live.execute("DETACH DATABASE seed")
    finally:
        live.close()


def write_report(header, rows, output):
    """Write the CSV exclusively at 0600; refuse to touch an existing path."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    try:
        fd = os.open(output, flags, 0o600)
    except FileExistsError:
        raise ReportError(f"output already exists, refusing to overwrite: {output}")
    except OSError as exc:
        raise ReportError(f"cannot open output {output}: {exc}")
    os.fchmod(fd, 0o600)  # umask must not widen a file holding PII
    try:
        with os.fdopen(fd, "w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(header)
            for row in rows:
                writer.writerow([_csv_cell(value) for value in row])
    except BaseException:
        os.unlink(output)  # a partial report is worse than none
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--live-db", required=True, help="copy of the volume database")
    parser.add_argument(
        "--seed-db", required=True, help="freshly computed results.sqlite to reseed from"
    )
    parser.add_argument("--output", required=True, help="CSV path; must not exist yet")
    args = parser.parse_args(argv)

    try:
        header, rows = collect_unmatched(args.live_db, args.seed_db)
        write_report(header, rows, args.output)
    except ReportError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"{len(rows)} unmatched saved lead(s) -> {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
