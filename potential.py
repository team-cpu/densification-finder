"""
Densification potential — corrected geometry, one municipality.

Replaces the two formula bugs the naive slice exposed:

  1. one row per building        -> aggregate existing floor area per parcel
  2. parcel_area * AZ            -> sum over the parcel's intersection with each
                                    zone it touches: SUM(area_i * AZ_i)

(2) matters because the AZ only applies to the part of a parcel that lies inside
the building zone, and because a parcel can span two zones with different AZ.
The naive formula charged the full parcel area at a single zone's rate.

Prints both numbers so the size of the correction is visible.

Usage: .venv/bin/python potential.py <BFSNr>
"""
import csv
import glob
import os
import re
import sqlite3
import sys

from shapely import from_wkb
from shapely.geometry import Polygon
from shapely.ops import unary_union

BFS = int(sys.argv[1]) if len(sys.argv) > 1 else 4012
HERE = os.path.dirname(os.path.abspath(__file__))
DATA = __import__("paths").DATA

PARCELS_XML = os.path.join(DATA, f"parcels_{BFS}.xml")
ZONES_GPKG = glob.glob(os.path.join(DATA, "are_bzbauzone_*.gpkg"))[0]
GWR_CSV = os.path.join(DATA, "gwr", "gebaeude_batiment_edificio.csv")

TARGET_CLASSES = {"1110", "1121"}
MIN_DELTA_M2 = 130.0


def gpkg_geom(blob):
    """Strip the GeoPackage binary header and hand the WKB to shapely."""
    env = (blob[3] >> 1) & 0x07
    return from_wkb(bytes(blob[8 + {0: 0, 1: 32, 2: 48, 3: 48, 4: 64}[env]:]))


def valid(g):
    return g if g.is_valid else g.buffer(0)


def load_parcels():
    xml = open(PARCELS_XML, encoding="utf-8", errors="replace").read()
    out = []
    for m in re.finditer(r"<ms:RESF\b.*?</ms:RESF>", xml, re.S):
        f = m.group(0)

        def g(tag):
            mm = re.search(rf"<ms:{tag}>([^<]*)</ms:{tag}>", f)
            return mm.group(1) if mm else None

        # A parcel is exterior ring MINUS its interior rings. Parcels routinely
        # exclude roads, watercourses and carved-out neighbours: 491 alone has
        # 13 holes. Taking only the first posList (the exterior) overstated it
        # by 28%.
        def rings(kind):
            out_ = []
            for blk in re.findall(rf"<gml:{kind}>(.*?)</gml:{kind}>", f, re.S):
                for pl in re.findall(r"<gml:posList[^>]*>([^<]+)</gml:posList>", blk):
                    v = [float(x) for x in pl.split()]
                    r = list(zip(v[0::2], v[1::2]))
                    if len(r) >= 4:
                        out_.append(r)
            return out_

        ext = rings("exterior")
        if not ext:
            continue
        area = g("Flaeche")
        out.append(
            {
                "nummer": g("Nummer"),
                "egrid": g("EGRIS_EGRID"),
                "area_registry": float(area) if area else None,
                "geom": valid(Polygon(ext[0], rings("interior"))),
            }
        )
    return out


def load_zones():
    db = sqlite3.connect(ZONES_GPKG)
    t = [r[0] for r in db.execute("SELECT table_name FROM gpkg_contents")][0]
    out = []
    for shape, az, name in db.execute(
        f'SELECT SHAPE, AZmax, GDEBez FROM "{t}" WHERE GDENR=? AND AZmax>0', (BFS,)
    ):
        out.append({"geom": valid(gpkg_geom(shape)), "az": round(float(az), 2), "name": name})
    return out


def load_buildings():
    out = []
    with open(GWR_CSV, newline="", encoding="utf-8", errors="replace") as fh:
        for row in csv.DictReader(fh, delimiter="\t"):
            if row.get("GGDENR", "").strip() != str(BFS):
                continue
            if (row.get("GKLAS") or "").strip() not in TARGET_CLASSES:
                continue
            a, f = (row.get("GAREA") or "").strip(), (row.get("GASTW") or "").strip()
            if not a or not f:
                continue
            out.append(
                {
                    "egrid": (row.get("EGRID") or "").strip(),
                    "existing": float(a) * int(f),
                    "period": (row.get("GBAUP") or "").strip(),
                }
            )
    return out


def main():
    parcels = load_parcels()
    zones = load_zones()
    buildings = load_buildings()
    print(f"  parcels {len(parcels):,} | AZ zones {len(zones):,} | target buildings {len(buildings):,}\n")

    # existing floor area per parcel, summed over everything standing on it
    existing = {}
    counts = {}
    by_egrid = {p["egrid"]: p for p in parcels if p["egrid"]}
    for b in buildings:
        p = by_egrid.get(b["egrid"])
        if not p:
            continue
        existing[p["nummer"]] = existing.get(p["nummer"], 0.0) + b["existing"]
        counts[p["nummer"]] = counts.get(p["nummer"], 0) + 1

    rows = []
    for p in parcels:
        num = p["nummer"]
        if num not in existing:
            continue
        g = p["geom"]

        allowance = 0.0
        buildable = 0.0
        naive_az = None
        for z in zones:
            if not g.intersects(z["geom"]):
                continue
            inter = g.intersection(z["geom"]).area
            if inter <= 1.0:
                continue
            allowance += inter * z["az"]
            buildable += inter
            if naive_az is None or inter > 0:
                naive_az = z["az"] if naive_az is None else naive_az
        if buildable <= 1.0:
            continue

        reg_area = p["area_registry"] or g.area
        rows.append(
            {
                "parcel": num,
                "area": reg_area,
                "buildable": buildable,
                "share": buildable / reg_area if reg_area else 0.0,
                "existing": existing[num],
                "buildings": counts[num],
                "delta": allowance - existing[num],
                "delta_naive": reg_area * (naive_az or 0) - existing[num],
                "period": next((b["period"] for b in buildings if by_egrid.get(b["egrid"], {}).get("nummer") == num), ""),
            }
        )

    keep = [r for r in rows if r["delta"] >= MIN_DELTA_M2]
    keep_naive = [r for r in rows if r["delta_naive"] >= MIN_DELTA_M2]
    keep.sort(key=lambda r: r["delta"] / max(r["existing"], 1), reverse=True)

    over = sum(r["delta_naive"] - r["delta"] for r in rows if r["delta_naive"] > r["delta"])
    partial = sum(1 for r in keep if r["share"] < 0.99)

    print(f"  parcels assessed              : {len(rows):,}")
    print(f"  above {MIN_DELTA_M2:.0f} m²  corrected      : {len(keep):,}")
    print(f"                naive           : {len(keep_naive):,}")
    print(f"  of the corrected set, only partly in a building zone: {partial:,}")
    print(f"  total potential the naive formula invented: {over:,.0f} m² GFA\n")

    print("  TOP 10 by delta/existing ratio (corrected)")
    print(f"    {'parcel':>8} {'area':>7} {'in-zone':>8} {'bld':>4} {'exist':>7} {'delta':>8} {'naive':>8}")
    for r in keep[:10]:
        print(
            f"    {r['parcel']:>8} {r['area']:>7.0f} {r['share']*100:>7.0f}% {r['buildings']:>4} "
            f"{r['existing']:>7.0f} {r['delta']:>8.0f} {r['delta_naive']:>8.0f}"
        )


if __name__ == "__main__":
    main()
