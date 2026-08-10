"""
Densification Potential Finder — first vertical slice, one municipality.

Purpose is not the product. It is to run every assumption in the revised Part 3
against real data at once and see whether the resulting numbers are believable:

  parcels (WFS)  +  zones with AZmax (AGIS)  +  buildings (GWR)
      -> join -> potential = area * AZmax - footprint * floors
      -> rank

Deliberately not the final architecture. The spec calls for PostGIS; this runs
in memory with a hand-rolled point-in-polygon so the data questions can be
answered before any infrastructure exists. Everything here is throwaway except
the findings.

Usage: python3 slice.py <BFSNr>
"""
import csv
import glob
import os
import re
import sqlite3
import struct
import sys
from collections import defaultdict

BFS = int(sys.argv[1]) if len(sys.argv) > 1 else 4012
HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get("DENSIFICATION_DATA", "")

PARCELS_XML = os.path.join(HERE, "data", f"parcels_{BFS}.xml")
ZONES_GPKG = glob.glob(os.path.join(DATA, "bz", "*", "*.gpkg"))
GWR_CSV = os.path.join(DATA, "gwr_ag", "gebaeude_batiment_edificio.csv")

# One- and two-dwelling buildings: the replacement-newbuild candidates.
TARGET_CLASSES = {"1110", "1121"}
MIN_DELTA_M2 = 130.0


# ---------------------------------------------------------------- geometry --
def poly_rings(blob):
    """Outer rings of a GeoPackage (Multi)Polygon, as coordinate lists."""
    env = (blob[3] >> 1) & 0x07
    off = 8 + {0: 0, 1: 32, 2: 48, 3: 48, 4: 64}[env]
    bo = "<" if blob[off] == 1 else ">"
    typ = struct.unpack_from(bo + "I", blob, off + 1)[0] & 0xFF
    off += 5
    out = []
    if typ == 6:
        n = struct.unpack_from(bo + "I", blob, off)[0]
        off += 4
        for _ in range(n):
            bo2 = "<" if blob[off] == 1 else ">"
            ring, off = _one(blob, off + 5, bo2)
            out.append(ring)
    elif typ == 3:
        ring, _ = _one(blob, off, bo)
        out.append(ring)
    return out


def _one(b, off, bo):
    nring = struct.unpack_from(bo + "I", b, off)[0]
    off += 4
    outer = None
    for i in range(nring):
        n = struct.unpack_from(bo + "I", b, off)[0]
        off += 4
        if i == 0:
            outer = [struct.unpack_from(bo + "dd", b, off + j * 16) for j in range(n)]
        off += n * 16
    return outer, off


def bbox(ring):
    xs = [p[0] for p in ring]
    ys = [p[1] for p in ring]
    return min(xs), min(ys), max(xs), max(ys)


def inside(pt, ring):
    x, y = pt
    c = False
    for i in range(len(ring) - 1):
        x1, y1 = ring[i]
        x2, y2 = ring[i + 1]
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            c = not c
    return c


class Index:
    """Uniform-grid bbox index. Enough for one municipality."""

    CELL = 200.0

    def __init__(self):
        self.cells = defaultdict(list)

    def add(self, ring, payload):
        x0, y0, x1, y1 = bbox(ring)
        for cx in range(int(x0 // self.CELL), int(x1 // self.CELL) + 1):
            for cy in range(int(y0 // self.CELL), int(y1 // self.CELL) + 1):
                self.cells[(cx, cy)].append((ring, (x0, y0, x1, y1), payload))

    def hit(self, pt):
        x, y = pt
        for ring, (x0, y0, x1, y1), payload in self.cells.get(
            (int(x // self.CELL), int(y // self.CELL)), ()
        ):
            if x0 <= x <= x1 and y0 <= y <= y1 and inside(pt, ring):
                return payload
        return None


# ------------------------------------------------------------------ inputs --
def load_parcels(path):
    """Parse the WFS GML. Only the fields the formula needs."""
    xml = open(path, encoding="utf-8", errors="replace").read()
    out = []
    for m in re.finditer(r"<ms:RESF\b.*?</ms:RESF>", xml, re.S):
        f = m.group(0)

        def g(tag):
            mm = re.search(rf"<ms:{tag}>([^<]*)</ms:{tag}>", f)
            return mm.group(1) if mm else None

        pos = re.search(r"<gml:posList[^>]*>([^<]+)</gml:posList>", f)
        if not pos:
            continue
        vals = [float(v) for v in pos.group(1).split()]
        ring = list(zip(vals[0::2], vals[1::2]))
        area = g("Flaeche")
        out.append(
            {
                "nummer": g("Nummer"),
                "egrid": g("EGRIS_EGRID"),
                "area": float(area) if area else None,
                "ring": ring,
            }
        )
    return out


def load_zones(bfs):
    db = sqlite3.connect(ZONES_GPKG[0])
    t = [r[0] for r in db.execute("SELECT table_name FROM gpkg_contents")][0]
    idx = Index()
    n = 0
    for shape, az, name in db.execute(
        f'SELECT SHAPE, AZmax, GDEBez FROM "{t}" WHERE GDENR=? AND AZmax>0', (bfs,)
    ):
        for ring in poly_rings(shape):
            if ring:
                idx.add(ring, (round(float(az), 2), name))
                n += 1
    return idx, n


def load_buildings(bfs):
    out = []
    with open(GWR_CSV, newline="", encoding="utf-8", errors="replace") as fh:
        for row in csv.DictReader(fh, delimiter="\t"):
            if row.get("GGDENR", "").strip() != str(bfs):
                continue
            if (row.get("GKLAS") or "").strip() not in TARGET_CLASSES:
                continue
            area, floors = (row.get("GAREA") or "").strip(), (row.get("GASTW") or "").strip()
            if not area or not floors:
                continue
            out.append(
                {
                    "egid": row["EGID"],
                    "egrid": (row.get("EGRID") or "").strip(),
                    "footprint": float(area),
                    "floors": int(floors),
                    "period": (row.get("GBAUP") or "").strip(),
                    "pt": (float(row["GKODE"]), float(row["GKODN"])),
                }
            )
    return out


# -------------------------------------------------------------------- main --
def main():
    parcels = load_parcels(PARCELS_XML)
    zones, nzone = load_zones(BFS)
    buildings = load_buildings(BFS)
    print(f"  parcels {len(parcels):,} | AZ zone rings {nzone:,} | target buildings {len(buildings):,}\n")

    by_egrid = {p["egrid"]: p for p in parcels if p["egrid"]}
    pidx = Index()
    for p in parcels:
        if p["ring"]:
            pidx.add(p["ring"], p)

    # Aggregate by PARCEL, not by building. A parcel's quota is consumed by
    # everything standing on it, so one row per building both duplicates the
    # parcel and subtracts only one building's floor area from the whole
    # allowance — which overstates the potential on every multi-building plot.
    joined_attr = joined_spatial = unjoined = 0
    agg = {}
    for b in buildings:
        parcel = by_egrid.get(b["egrid"])
        if parcel:
            joined_attr += 1
        else:
            parcel = pidx.hit(b["pt"])
            if parcel:
                joined_spatial += 1
            else:
                unjoined += 1
                continue

        zone = zones.hit(b["pt"])
        if not zone or not parcel["area"]:
            continue
        az, zname = zone
        key = parcel["nummer"]
        rec = agg.get(key)
        if rec is None:
            rec = agg[key] = {
                "parcel": key,
                "area": parcel["area"],
                "az": az,
                "zone": zname,
                "existing": 0.0,
                "buildings": 0,
                "period": b["period"],
            }
        rec["existing"] += b["footprint"] * b["floors"]
        rec["buildings"] += 1

    rows = list(agg.values())
    for r in rows:
        r["delta"] = r["area"] * r["az"] - r["existing"]

    multi = sum(1 for r in rows if r["buildings"] > 1)
    print(f"  building->parcel join: {joined_attr:,} by EGRID, {joined_spatial:,} spatial, {unjoined:,} unmatched")
    print(f"  parcels inside an AZ zone: {len(rows):,}  ({multi:,} carry more than one building)\n")

    keep = [r for r in rows if r["delta"] >= MIN_DELTA_M2]
    keep.sort(key=lambda r: r["delta"] / max(r["existing"], 1), reverse=True)
    print(f"  above the {MIN_DELTA_M2:.0f} m² threshold: {len(keep):,}\n")
    print("  TOP 10 by delta/existing ratio")
    print(f"    {'parcel':>8} {'area':>7} {'AZ':>5} {'bld':>4} {'exist':>7} {'delta':>8}  zone")
    for r in keep[:10]:
        print(
            f"    {r['parcel']:>8} {r['area']:>7.0f} {r['az']:>5.2f} {r['buildings']:>4} "
            f"{r['existing']:>7.0f} {r['delta']:>8.0f}  {r['zone'][:30]}"
        )


if __name__ == "__main__":
    main()
