"""
Utilization metrics — how a zone's headline figure becomes floor area.

Aargau writes its plans in Ausnützungsziffer, a floor-area ratio that multiplies
straight into the answer. Lucerne and Nidwalden use Überbauungsziffer instead, a
coverage ratio, and the brief's Part 4 asks that the architecture accommodate
that later. The two are not interchangeable: a coverage ratio bounds the
FOOTPRINT, and says nothing about floor area until you know how many storeys are
allowed on it.

So the seam is one function per metric, `(area, zone) -> float | None`. `None`
means the zone carries the metric but not the second input the conversion needs
— reported as not assessable, with the reason, which is what §3.5 step 1 asks
for and the opposite of inventing a number. Part 1 is explicit that a parcel
wrongly listed as high-potential costs Philipp time and erodes trust, so a
missing figure is always better than a guessed one.

This is not only future-proofing. Aargau's own zone table already carries all
three metrics, and 220 of its zones use a non-AZ one exclusively:

    AZmax   19,008 zones   Ausnützungsziffer   floor-area ratio
    BMZmax     198 zones   Baumassenziffer     volume ratio
    UEZmax     123 zones   Überbauungsziffer   coverage ratio

Adding a canton means adding an entry to CANTONS naming its dataset and columns.
"""
from dataclasses import dataclass
from typing import Callable, Optional


def _floor_area_ratio(area: float, zone: dict) -> Optional[float]:
    """Ausnützungsziffer / Geschossflächenziffer.

    anrechenbare Geschossfläche / Grundstücksfläche — already a floor area, so
    the parcel area times the figure is the answer with nothing assumed.
    """
    return area * zone["value"]


def _coverage_ratio(area: float, zone: dict) -> Optional[float]:
    """Überbauungsziffer.

    überbaute Fläche / Grundstücksfläche — a limit on the footprint. The floor
    count is what turns it into floor area, and where the plan does not publish
    one there is no honest conversion.
    """
    floors = zone.get("floors")
    if not floors:
        return None
    return area * zone["value"] * floors


def _volume_ratio(area: float, zone: dict) -> Optional[float]:
    """Baumassenziffer.

    Baumasse / Grundstücksfläche — a volume. Converting it would need an assumed
    storey height on top of the floor count, and two stacked assumptions produce
    exactly the confidently wrong number the brief warns about. Recognised so
    the parcel is reported as not assessable for a stated reason rather than
    silently dropped; deliberately not converted.
    """
    return None


@dataclass(frozen=True)
class Metric:
    key: str
    column: str
    label: str
    allowance: Callable[[float, dict], Optional[float]]
    #: Shown when `allowance` returns None, so "not assessable" always says why.
    unconvertible: str


METRICS = {
    "AZ": Metric("AZ", "AZmax", "Ausnützungsziffer", _floor_area_ratio, ""),
    "UEZ": Metric("UEZ", "UEZmax", "Überbauungsziffer", _coverage_ratio,
                  "Überbauungsziffer ohne publizierte Geschosszahl"),
    "BMZ": Metric("BMZ", "BMZmax", "Baumassenziffer", _volume_ratio,
                  "Baumassenziffer (Umrechnung in Geschossfläche nicht belastbar)"),
    "GFZ": Metric("GFZ", "GFZmax", "Geschossflächenziffer", _floor_area_ratio, ""),
}


@dataclass(frozen=True)
class Canton:
    code: str
    dataset: str
    #: Metric keys in precedence order — the first one a zone carries wins, so a
    #: zone with both an AZ and a coverage ratio is read as its planners' primary
    #: figure rather than whichever column happens to be queried first.
    metrics: tuple
    floors_column: Optional[str]
    name_column: str
    bfs_column: str


CANTONS = {
    "AG": Canton(
        code="AG",
        dataset="are_bzbauzone_*.gpkg",
        metrics=("AZ", "GFZ", "UEZ", "BMZ"),
        floors_column="GZ",
        name_column="GDEBez",
        bfs_column="GDENR",
    ),
}


def columns_for(canton: Canton) -> list:
    """Every column the loader has to SELECT for this canton."""
    cols = [canton.bfs_column, canton.name_column]
    cols += [METRICS[k].column for k in canton.metrics]
    if canton.floors_column:
        cols.append(canton.floors_column)
    return cols


def read_zone(canton: Canton, row: dict) -> Optional[dict]:
    """Turn one raw zone row into {metric, value, floors, name}, or None when the
    zone publishes no utilization figure at all."""
    for key in canton.metrics:
        raw = row.get(METRICS[key].column)
        if raw is None:
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if value <= 0:
            continue
        floors = row.get(canton.floors_column) if canton.floors_column else None
        try:
            floors = float(floors) if floors else None
        except (TypeError, ValueError):
            floors = None
        return {
            "metric": key,
            "value": round(value, 3),
            "floors": floors,
            "name": row.get(canton.name_column),
        }
    return None


def allowance(area: float, zone: dict) -> Optional[float]:
    """Floor area this zone permits on `area` m², or None if not convertible."""
    return METRICS[zone["metric"]].allowance(area, zone)
