"""
The seven-step cascade, as a reusable engine.

`run.py` loads every constraint layer on each invocation, which is fine for one
municipality and wasteful for 196. `Engine` loads them once — heritage,
planning freezes, design plans, the zone table and the GWR extract — and then
answers per municipality.

Order follows the brief: hard exclusions first, so a parcel that may not be
demolished is reported as protected rather than as having too little potential.
"""
import csv
import glob
import os
import re
import sqlite3
from datetime import date

from shapely.geometry import Polygon
from shapely.strtree import STRtree

import constraints as C
import metrics as M
from shapely import from_wkb


def gpkg_geom(blob):
    """Strip the GeoPackage binary header and hand the WKB to shapely."""
    env = (blob[3] >> 1) & 0x07
    return from_wkb(bytes(blob[8 + {0: 0, 1: 32, 2: 48, 3: 48, 4: 64}[env]:]))


def valid(g):
    return g if g.is_valid else g.buffer(0)

import paths

HERE = paths.HERE
DATA = paths.DATA
GWR_CSV = os.path.join(DATA, "gwr", "gebaeude_batiment_edificio.csv")
# Addresses are a separate GWR file keyed by EGID; the building extract has none.
ENTRANCE_CSV = os.path.join(DATA, "gwr", "eingang_entree_entrata.csv")
CODES_CSV = os.path.join(DATA, "gwr", "kodes_codes_codici.csv")
TARGET_CLASSES = {"1110", "1121"}

# GWR keeps a building's whole life cycle, not only what stands today. Counting
# a demolished one as existing floor area inflates what is already built, which
# lowers the computed potential and quietly buries the best leads — a parcel
# whose house has been torn down is effectively vacant land.
#
#   1003 im Bau        counted: it will be there, and the parcel is not a target
#   1004 bestehend     counted
#   1005 nicht nutzbar counted: unusable, but physically standing
#   1001 projektiert   not counted — not built
#   1002 bewilligt     not counted — approved only
#   1007 abgebrochen   not counted — demolished
#   1008 nicht real.   not counted — never built
STANDING = {"1003", "1004", "1005"}


def load_parcels(bfs):
    """Exterior ring minus interior rings — parcels routinely exclude roads and
    carved-out neighbours, and dropping the holes overstates the area."""
    path = os.path.join(DATA, f"parcels_{bfs}.xml")
    xml = open(path, encoding="utf-8", errors="replace").read()
    out = []
    for m in re.finditer(r"<ms:RESF\b.*?</ms:RESF>", xml, re.S):
        f = m.group(0)

        def g(tag):
            mm = re.search(rf"<ms:{tag}>([^<]*)</ms:{tag}>", f)
            return mm.group(1) if mm else None

        def rings(kind):
            res = []
            for blk in re.findall(rf"<gml:{kind}>(.*?)</gml:{kind}>", f, re.S):
                for pl in re.findall(r"<gml:posList[^>]*>([^<]+)</gml:posList>", blk):
                    v = [float(x) for x in pl.split()]
                    r = list(zip(v[0::2], v[1::2]))
                    if len(r) >= 4:
                        res.append(r)
            return res

        ext = rings("exterior")
        if not ext:
            continue
        area = g("Flaeche")
        out.append(
            {
                "nummer": g("Nummer"),
                "egrid": g("EGRIS_EGRID"),
                "area": float(area) if area else None,
                "geom": valid(Polygon(ext[0], rings("interior"))),
            }
        )
    return out


class Engine:
    # §3.5 step 4 asks for a parcel-area range that is configurable, and the
    # interface is where it gets configured — so the stored table must not be
    # narrower than what the control offers. It was: the engine only ever kept
    # 300–5,000 m², which made the slider stop at a wall rather than at the end
    # of the data, and hid the single largest lead in the canton (Rheinfelden
    # 574, ~108,000 m² of potential on a 199,442 m² Wohnzone B parcel). Storing
    # every area costs 1,937 extra rows out of 36,274 — 5.6% — and the filtering
    # now happens where the user can see and undo it.
    def __init__(self, built_after=None, min_delta=130.0, min_area=0.0,
                 max_area=float("inf"), exclude_inventory=False, canton="AG"):
        self.canton = canton
        self.built_after = built_after or (date.today().year - 15)
        self.min_delta, self.min_area, self.max_area = min_delta, min_area, max_area
        self.exclude_inventory = exclude_inventory

        self.heritage, self.tiers = C.load_heritage()
        self.freezes = C.load_planning_freezes()
        self.plans = C.load_design_plans()
        self._zones = self._load_all_zones()
        self._labels = self._load_labels()
        self._addresses = self._load_addresses()
        self._gwr, self._occupied_egrids = self._load_gwr()

    @staticmethod
    def _load_labels():
        """GWR ships its own code table; the alternative is hardcoding German
        strings that only the federal office is entitled to change."""
        out = {}
        if not os.path.exists(CODES_CSV):
            return out
        with open(CODES_CSV, newline="", encoding="utf-8", errors="replace") as fh:
            for row in csv.DictReader(fh, delimiter="\t"):
                out[(row.get("CMERKM"), row.get("CECODID"))] = (row.get("CODTXTLD") or "").strip()
        return out

    @staticmethod
    def _load_addresses():
        """EGID -> (street address, entrance coordinate).

        A building can carry several entrances; the official one (DOFFADR=1)
        wins, and anything else is only a fallback so a parcel still gets an
        address rather than a blank.

        The entrance coordinate is kept because Street View needs it. A parcel's
        representative point sits in the middle of the plot — 23 to 68 m from the
        entrance on the parcels checked — and Google only returns a panorama
        within roughly 50 m of the requested point, so linking from the parcel
        centre yields a black screen. The entrance faces the road, where the
        panoramas actually are. Verified against a live panorama.
        """
        official, fallback = {}, {}
        if not os.path.exists(ENTRANCE_CSV):
            return official
        with open(ENTRANCE_CSV, newline="", encoding="utf-8", errors="replace") as fh:
            for row in csv.DictReader(fh, delimiter="\t"):
                egid = (row.get("EGID") or "").strip()
                street = (row.get("STRNAME") or "").strip()
                if not egid or not street:
                    continue
                nr = (row.get("DEINR") or "").strip()
                plz = (row.get("DPLZ4") or "").strip()
                town = (row.get("DPLZNAME") or "").strip()
                text = " ".join(x for x in (street, nr) if x)
                if plz or town:
                    text += ", " + " ".join(x for x in (plz, town) if x)
                try:
                    point = (float(row["DKODE"]), float(row["DKODN"]))
                except (KeyError, TypeError, ValueError):
                    point = None
                target = official if (row.get("DOFFADR") or "").strip() == "1" else fallback
                target.setdefault(egid, (text, point))
        for egid, value in fallback.items():
            official.setdefault(egid, value)
        return official

    def _load_all_zones(self):
        """Zone polygons keyed by municipality, so a run touches only its own.

        Every zone publishing ANY utilization figure is loaded, not only those
        with an Ausnützungsziffer. `metrics.read_zone` decides which figure a
        zone is read by; whether that figure can become floor area is decided
        later, per parcel, so a zone we cannot convert is reported rather than
        filtered out here and silently lost.
        """
        canton = M.CANTONS[self.canton]
        db = sqlite3.connect(glob.glob(os.path.join(DATA, canton.dataset))[0])
        t = [r[0] for r in db.execute("SELECT table_name FROM gpkg_contents")][0]
        cols = M.columns_for(canton)
        by_bfs = {}
        for row in db.execute(f'SELECT SHAPE, {",".join(cols)} FROM "{t}"'):
            attrs = dict(zip(cols, row[1:]))
            zone = M.read_zone(canton, attrs)
            if zone is None:
                continue  # no utilization figure at all — not a building zone
            zone["geom"] = valid(gpkg_geom(row[0]))
            by_bfs.setdefault(int(attrs[canton.bfs_column]), []).append(zone)
        return by_bfs

    def _load_gwr(self):
        """Load target buildings and independently track occupied parcels.

        Vacancy cannot be inferred from the target residential classes alone:
        a parcel with a workshop, barn, or standing building whose area is
        missing is occupied too. ``occupied`` therefore records every standing
        GWR building with an EGRID, while ``by_bfs`` retains the narrower set
        whose existing residential floor area can be estimated.
        """
        by_bfs, occupied = {}, {}
        with open(GWR_CSV, newline="", encoding="utf-8", errors="replace") as fh:
            for row in csv.DictReader(fh, delimiter="\t"):
                if (row.get("GSTAT") or "").strip() not in STANDING:
                    continue
                bfs = (row.get("GGDENR") or "").strip()
                if not bfs.isdigit():
                    continue
                bfs = int(bfs)
                egrid = (row.get("EGRID") or "").strip()
                if egrid:
                    occupied.setdefault(bfs, set()).add(egrid)

                if (row.get("GKLAS") or "").strip() not in TARGET_CLASSES:
                    continue
                a, f = (row.get("GAREA") or "").strip(), (row.get("GASTW") or "").strip()
                if not a or not f:
                    continue
                by_bfs.setdefault(int(bfs), []).append(
                    {
                        "egrid": egrid,
                        "egid": (row.get("EGID") or "").strip(),
                        "existing": float(a) * int(f),
                        "footprint": float(a),
                        "klass": (row.get("GKLAS") or "").strip(),
                        "period": (row.get("GBAUP") or "").strip(),
                        "year": (row.get("GBAUJ") or "").strip(),
                    }
                )
        return by_bfs, occupied

    def _describe(self, r):
        """The human-facing columns, derived from the buildings on one parcel.

        Age reports the OLDEST building, because that is the one a replacement
        new-build is arguing against; reporting the newest would make a parcel
        with one recent annexe look untouchable. Falls back to the GWR period
        label when no exact year is recorded, which is most of this stock.
        """
        years = sorted(int(y) for y in r["years"] if y.strip().isdigit())
        if years:
            built = str(years[0])
        else:
            periods = sorted(p for p in r["periods"] if p)
            built = self._labels.get(("GBAUP", periods[0]), periods[0]) if periods else ""
            built = built.replace("Periode ", "")

        # Use: the class of the largest building on the parcel.
        biggest = max(r["buildings"], key=lambda b: b["footprint"], default=None)
        use = self._labels.get(("GKLAS", biggest["klass"]), "") if biggest else ""

        # The entrance of the same building the address came from, so the
        # Street View link and the address always describe the same front door.
        address, entrance = "", None
        for b in sorted(r["buildings"], key=lambda b: -b["footprint"]):
            address, entrance = self._addresses.get(b["egid"], ("", None))
            if address:
                break
        return address, built, use, entrance

    def run(self, bfs):
        parcels = load_parcels(bfs)
        zones = self._zones.get(bfs, [])
        zone_index = STRtree([z["geom"] for z in zones]) if zones else None
        by_egrid = {p["egrid"]: p for p in parcels if p["egrid"]}

        per_parcel = {}
        for b in self._gwr.get(bfs, []):
            p = by_egrid.get(b["egrid"])
            if not p:
                continue
            r = per_parcel.setdefault(
                p["nummer"],
                {"parcel": p, "existing": 0.0, "n": 0, "periods": [], "years": [],
                 "buildings": []},
            )
            r["existing"] += b["existing"]
            r["n"] += 1
            r["periods"].append(b["period"])
            r["years"].append(b["year"])
            r["buildings"].append(b)

        # Start truly vacant parcels at zero existing floor area. An absent
        # EGRID is not enough evidence of vacancy, so those parcels are skipped
        # rather than creating a false positive.
        occupied = self._occupied_egrids.get(bfs, set())
        for parcel in parcels:
            if not parcel["egrid"] or parcel["egrid"] in occupied:
                continue
            per_parcel.setdefault(
                parcel["nummer"],
                {"parcel": parcel, "existing": 0.0, "n": 0, "periods": [],
                 "years": [], "buildings": []},
            )

        no_az = 0
        unassessable = {}   # reason -> count, so nothing is dropped silently
        candidates = []
        for num, r in per_parcel.items():
            g = r["parcel"]["geom"]
            area = r["parcel"]["area"] or g.area

            allowance = buildable = 0.0
            unconvertible = 0.0   # zoned, but its figure cannot become floor area
            reason = ""
            dominant = None       # (intersected area, zone) — the reported figure
            if zone_index is not None:
                for i in zone_index.query(g):
                    z = zones[i]
                    if not r["buildings"] and not z["residential"]:
                        continue
                    inter = g.intersection(z["geom"]).area
                    if inter <= 1.0:
                        continue
                    share = M.allowance(inter, z)
                    if share is None:
                        # Recorded, not discarded: §3.5 step 1 asks for "not
                        # assessable" with a reason rather than a silent skip.
                        unconvertible += inter
                        reason = reason or M.METRICS[z["metric"]].unconvertible
                        continue
                    allowance += share
                    buildable += inter
                    if dominant is None or inter > dominant[0]:
                        dominant = (inter, z)
            if buildable <= 1.0:
                no_az += 1
                if reason:
                    unassessable.setdefault(reason, 0)
                    unassessable[reason] += 1
                continue

            if r["buildings"] and all(
                C.is_recent(p, self.built_after, y)
                for p, y in zip(r["periods"], r["years"])
            ):
                continue

            # The heritage sources describe buildings, not land. Probing them
            # with a five-metre tolerance is useful for an existing house but
            # would attach a neighbour's protected building to a vacant plot.
            # For a putatively vacant parcel, an unbuffered hit is instead
            # evidence that GWR missed a building, so it is not called vacant.
            found = C.heritage_for(
                self.heritage, self.tiers, g,
                buffer_m=5.0 if r["buildings"] else 0.0,
            )
            if not r["buildings"] and found:
                continue
            if found & C.HARD:
                continue
            soft = sorted(found - C.HARD)
            if soft and self.exclude_inventory:
                continue

            if C.in_planning_freeze(self.freezes, g):
                continue

            delta = allowance - r["existing"]
            if delta < self.min_delta or not (self.min_area <= area <= self.max_area):
                continue

            address, built, use, entrance = self._describe(r)
            # The AGIS map link works by simulated click: info=E,N pops the
            # parcel card at that LV95 coordinate (format from Philipp's own
            # browser, 2026-08-11 — there is no EGRID parameter). The
            # representative point is guaranteed inside the polygon, where a
            # centroid of an L-shaped parcel can land on the neighbour.
            pt = g.representative_point()
            candidates.append(
                {
                    "parcel": num,
                    "egrid": r["parcel"]["egrid"],
                    "east": round(pt.x, 2),
                    "north": round(pt.y, 2),
                    # Where to stand to look at the building. Falls back to the
                    # parcel point on vacant land, which has no front door — and
                    # nothing to look at either.
                    "sv_east": round(entrance[0], 2) if entrance else round(pt.x, 2),
                    "sv_north": round(entrance[1], 2) if entrance else round(pt.y, 2),
                    "address": address,
                    "built": built,
                    "use": use,
                    # The zone covering most of the parcel, and the AZ actually
                    # applied — area-weighted, so a parcel straddling two zones
                    # reports the figure the potential was computed from rather
                    # than one of the two headline values.
                    "zone": dominant[1]["name"] if dominant else "",
                    # Area-weighted over every zone the parcel touches, so a
                    # parcel straddling two zones reports the figure its
                    # potential was computed from. `metric` names which figure
                    # that is — labelling an Überbauungsziffer "AZ" would be a
                    # real error for an architect reading the list.
                    "az": round(allowance / buildable, 3) if buildable else 0.0,
                    "metric": dominant[1]["metric"] if dominant else "",
                    "unconvertible": round(unconvertible, 1),
                    "area": area,
                    "buildable": buildable,
                    "share": buildable / area if area else 0.0,
                    "n": r["n"],
                    "existing": r["existing"],
                    "delta": delta,
                    "heritage": ",".join(soft),
                    "design_plan": C.under_design_plan(self.plans, g),
                }
            )

        candidates.sort(key=lambda r: r["delta"] / max(r["existing"], 1), reverse=True)
        return {
            "parcels": len(parcels),
            "assessed": len(per_parcel),
            "no_az": no_az,
            "unassessable": unassessable,
            "candidates": candidates,
        }
