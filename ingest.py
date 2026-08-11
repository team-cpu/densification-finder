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
import os
import sqlite3
import sys
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
DB = os.path.join(HERE, "results.sqlite")

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


def municipalities_with_az():
    db = sqlite3.connect(glob.glob(os.path.join(DATA, "are_bzbauzone_*.gpkg"))[0])
    t = [r[0] for r in db.execute("SELECT table_name FROM gpkg_contents")][0]
    rows = db.execute(
        f'SELECT GDENR, COUNT(*) FROM "{t}" WHERE AZmax>0 GROUP BY GDENR ORDER BY GDENR'
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
    ("zone", "TEXT"), ("az", "REAL"), ("area", "REAL"), ("buildable", "REAL"),
    ("zone_share", "REAL"), ("buildings", "INTEGER"), ("existing", "REAL"),
    ("delta", "REAL"), ("heritage", "TEXT"), ("design_plan", "INTEGER"),
    ("calculated_at", "TEXT"),
]


def schema(con):
    cols = ",\n            ".join(f"{n:<13} {t}" for n, t in COLUMNS)
    con.executescript(
        f"""
        CREATE TABLE IF NOT EXISTS parcel_results (
            {cols},
            PRIMARY KEY (bfs, parcel)
        );
        CREATE TABLE IF NOT EXISTS runs (
            bfs INTEGER PRIMARY KEY, municipality TEXT, parcels INTEGER,
            assessed INTEGER, candidates INTEGER, no_az INTEGER,
            seconds REAL, finished_at TEXT
        );
        -- ÖREB answers, cached so the shortlist check costs one call per parcel
        -- ever rather than one per click. `hard` non-empty means excluded.
        CREATE TABLE IF NOT EXISTS oereb_cache (
            egrid      TEXT PRIMARY KEY,
            hard       TEXT,
            notable    TEXT,
            error      TEXT,
            checked_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_delta ON parcel_results(delta DESC);
        """
    )
    have = {r[1] for r in con.execute("PRAGMA table_info(parcel_results)")}
    for name, decl in COLUMNS:
        if name not in have:
            # NOT NULL cannot be added to a populated table; the constraint only
            # matters for rows this version writes, which always supply a value.
            con.execute(f"ALTER TABLE parcel_results ADD COLUMN {name} {decl.replace(' NOT NULL', '')}")
    con.commit()


def recompute(progress=None):
    """Re-run the cascade over every municipality and rewrite the results.

    Callable from the interface, so its Run button can do what the brief asks
    rather than only the ÖREB half. Deliberately does NOT re-fetch parcels from
    the WFS: that is ~10 s per municipality against 0.1 s to recompute one, and
    the cadastre does not change between two clicks. Deleting `data/parcels_*.xml`
    forces a fresh download on the next run.

    Measured: 1.7 s to build the engine, 16.6 s for all 163 municipalities.
    """
    con = sqlite3.connect(DB)
    schema(con)
    targets = municipalities_with_az()

    import cascade

    engine = cascade.Engine()
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
            "INSERT OR REPLACE INTO runs VALUES (?,?,?,?,?,?,?,datetime('now'))",
            (bfs, name, res["parcels"], res["assessed"], len(res["candidates"]),
             res["no_az"], 0),
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
        "zone": r["zone"], "az": r["az"], "area": r["area"], "buildable": r["buildable"],
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
            "INSERT OR REPLACE INTO runs VALUES (?,?,?,?,?,?,?,datetime('now'))",
            (bfs, name, res["parcels"], res["assessed"], len(res["candidates"]),
             res["no_az"], round(time.time() - t0, 1)),
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
