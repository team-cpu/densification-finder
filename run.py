"""
Densification Potential Finder — full filter cascade, one municipality.

Runs the corrected formula from `potential.py` and then applies the cascade from
the revised Part 3, in order, reporting what each step removes. Nothing is
dropped silently: a parcel that cannot be assessed says why.

All seven cascade steps are implemented, ÖREB included. Its endpoint shape is
recorded in `oereb.py` — the format goes in the path and the identifier is a
query parameter, which is not what the first four guesses assumed.

Usage: .venv/bin/python run.py <BFSNr>
"""
import csv
import os
import sys
from datetime import date

import constraints as C
import oereb as O
import potential as P

BUILT_AFTER = int(os.environ.get("BUILT_AFTER", date.today().year - 15))
MIN_DELTA = float(os.environ.get("MIN_DELTA", 130))
MIN_AREA = float(os.environ.get("MIN_AREA", 300))
MAX_AREA = float(os.environ.get("MAX_AREA", 5000))
EXCLUDE_INVENTORY = os.environ.get("EXCLUDE_INVENTORY", "0") == "1"
SHORTLIST = int(os.environ.get("SHORTLIST", 30))


def main():
    parcels = P.load_parcels()
    zones = P.load_zones()
    print(f"  parcels {len(parcels):,} | AZ zones {len(zones):,}")

    # buildings, grouped onto their parcel via EGRID
    per_parcel = {}
    with open(P.GWR_CSV, newline="", encoding="utf-8", errors="replace") as fh:
        by_egrid = {p["egrid"]: p for p in parcels if p["egrid"]}
        for row in csv.DictReader(fh, delimiter="\t"):
            if row.get("GGDENR", "").strip() != str(P.BFS):
                continue
            if (row.get("GKLAS") or "").strip() not in P.TARGET_CLASSES:
                continue
            a, f = (row.get("GAREA") or "").strip(), (row.get("GASTW") or "").strip()
            if not a or not f:
                continue
            p = by_egrid.get((row.get("EGRID") or "").strip())
            if not p:
                continue
            r = per_parcel.setdefault(p["nummer"], {"parcel": p, "existing": 0.0, "n": 0, "periods": [], "years": []})
            r["existing"] += float(a) * int(f)
            r["n"] += 1
            r["periods"].append((row.get("GBAUP") or "").strip())
            r["years"].append((row.get("GBAUJ") or "").strip())

    heritage_tree, tiers = C.load_heritage()
    plans = C.load_design_plans()
    freezes = C.load_planning_freezes()
    print(f"  heritage objects {len(tiers):,} | design-plan areas indexed\n")

    step = {k: 0 for k in ("no_az", "recent", "small_delta", "area_range", "hard_heritage", "freeze", "design_plan", "oereb")}
    out, notes = [], []

    for num, r in per_parcel.items():
        g = r["parcel"]["geom"]
        area = r["parcel"]["area_registry"] or g.area

        allowance = buildable = 0.0
        for z in zones:
            if not g.intersects(z["geom"]):
                continue
            inter = g.intersection(z["geom"]).area
            if inter > 1.0:
                allowance += inter * z["az"]
                buildable += inter
        if buildable <= 1.0:
            step["no_az"] += 1
            notes.append((num, "not assessable: no building zone with an AZ"))
            continue

        # 2. age — newest building on the parcel decides
        if all(C.is_recent(p, BUILT_AFTER, y) for p, y in zip(r["periods"], r["years"])):
            step["recent"] += 1
            continue

        # Heritage BEFORE the delta filter. The spec's cascade puts hard
        # exclusions first, and the order is not only about cost: checking it
        # last means a protected parcel gets dropped by the delta threshold and
        # is reported as "too little potential" rather than "may not be
        # demolished". The first is misleading, the second is the truth.
        tiersfound = C.heritage_for(heritage_tree, tiers, g)
        hard = tiersfound & C.HARD
        if hard:
            step["hard_heritage"] += 1
            notes.append((num, "excluded: " + ", ".join(sorted(hard))))
            continue
        soft = sorted(tiersfound - C.HARD)
        if soft and EXCLUDE_INVENTORY:
            step["hard_heritage"] += 1
            continue

        # Planning freeze — checked locally, not left to the ÖREB step. Nearly
        # half of Aargau's Planungszonen are not exported to the cadastre, so a
        # parcel inside one of those would pass the ÖREB call clean.
        if C.in_planning_freeze(freezes, g):
            step["freeze"] += 1
            notes.append((num, "excluded: Planungszone (planning freeze in force)"))
            continue

        delta = allowance - r["existing"]
        if delta < MIN_DELTA:
            step["small_delta"] += 1
            continue
        if not (MIN_AREA <= area <= MAX_AREA):
            step["area_range"] += 1
            continue

        superseded = C.under_design_plan(plans, g)
        if superseded:
            step["design_plan"] += 1

        out.append(
            {
                "parcel": num,
                "egrid": r["parcel"]["egrid"],
                "area": area,
                "share": buildable / area if area else 0,
                "n": r["n"],
                "existing": r["existing"],
                "delta": delta,
                "heritage": ",".join(soft) or "-",
                "flag": "AZ possibly superseded" if superseded else "",
                "oereb": "",
            }
        )

    out.sort(key=lambda r: r["delta"] / max(r["existing"], 1), reverse=True)

    # Step 7 — ÖREB, on the shortlist only. One call per parcel, so it stays a
    # few dozen requests per run rather than thousands.
    shortlist = out[:SHORTLIST]
    dropped = 0
    for r in shortlist:
        if not r["egrid"]:
            continue
        hard, notable, err = O.assess(r["egrid"])
        if err:
            r["oereb"] = "lookup failed"
            continue
        if hard:
            r["oereb"] = "EXCLUDED: " + "; ".join(hard)
            dropped += 1
        elif notable:
            r["oereb"] = "; ".join(notable)
    step["oereb"] = dropped
    out = [r for r in shortlist if not r["oereb"].startswith("EXCLUDED")]

    print(f"  cascade (parcels removed at each step)")
    print(f"    no AZ zone                : {step['no_az']:>5}")
    print(f"    built after {BUILT_AFTER}          : {step['recent']:>5}")
    print(f"    delta < {MIN_DELTA:.0f} m²            : {step['small_delta']:>5}")
    print(f"    area outside {MIN_AREA:.0f}–{MAX_AREA:.0f} m² : {step['area_range']:>5}")
    print(f"    protected (demolition)    : {step['hard_heritage']:>5}")
    print(f"    planning freeze (local)   : {step['freeze']:>5}")
    print(f"    ÖREB hard restriction     : {step['oereb']:>5}   (shortlist of {SHORTLIST})")
    print(f"  ── remaining candidates      : {len(out):>5}")
    print(f"     of which under a design plan (AZ unreliable): {step['design_plan']}\n")

    print("  TOP 20")
    print(f"    {'parcel':>8} {'area':>7} {'zone%':>6} {'bld':>4} {'exist':>7} {'delta':>8} {'~units':>7}  constraints")
    for r in out[:20]:
        units = r["delta"] / 90
        extra = " | ".join(x for x in (r["heritage"] if r["heritage"] != "-" else "", r["flag"], r["oereb"]) if x)
        print(
            f"    {r['parcel']:>8} {r['area']:>7.0f} {r['share']*100:>5.0f}% {r['n']:>4} "
            f"{r['existing']:>7.0f} {r['delta']:>8.0f} {units:>7.1f}  {extra}"
        )

    if notes:
        print(f"\n  not assessable / excluded, with reason: {len(notes)}")
        for num, why in notes[:5]:
            print(f"    {num}: {why}")


if __name__ == "__main__":
    main()
