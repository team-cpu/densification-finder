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
import sqlite3
import sys
import time
import urllib.parse
import urllib.request

import paths

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
    ("zone_share", "REAL"), ("buildings", "INTEGER"), ("existing", "REAL"),
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
            )
            con.execute(f"ALTER TABLE {table} ADD COLUMN {name} {compatible}")


def schema(con):
    parcel_cols = _column_definitions(COLUMNS)
    run_cols = _column_definitions(RUN_COLUMNS)
    oereb_cols = _column_definitions(OEREB_COLUMNS)
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
        """
    )
    _add_missing_columns(con, "parcel_results", COLUMNS)
    _add_missing_columns(con, "runs", RUN_COLUMNS)
    _add_missing_columns(con, "oereb_cache", OEREB_COLUMNS)
    # Create indexes after widening so an index introduced alongside a column
    # never runs before that column exists on a legacy database.
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_delta ON parcel_results(delta DESC)"
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
        "zone_share": r["share"], "buildings": r["n"], "existing": r["existing"],
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
