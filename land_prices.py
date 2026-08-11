"""Land-price reference values used by the result list.

The bundled CSV intentionally contains only a defensible canton-wide fallback.
Licensed or official municipality/zone values can be added without changing
code; the most specific matching row wins in this order:

    municipality + zone > municipality > zone > canton fallback

``zone_pattern`` uses shell-style matching (for example ``*Wohnen*``).
``bfs`` is preferred over a municipality name because names can change.
"""
import csv
import fnmatch
import os
from dataclasses import dataclass

import paths


DEFAULT_PATH = os.environ.get("DENSIFICATION_LAND_PRICES") or os.path.join(
    paths.HERE, "land_prices.csv"
)


@dataclass(frozen=True)
class LandPriceReference:
    price_chf_m2: float
    source: str
    source_url: str
    as_of: str
    note: str
    bfs: int | None = None
    municipality: str = ""
    zone_pattern: str = ""

    @property
    def scope(self):
        if self.municipality:
            return self.municipality
        if self.zone_pattern:
            return self.zone_pattern
        return "Kanton AG"

    @property
    def display(self):
        return " · ".join(x for x in (self.scope, self.as_of) if x)


def load(path=DEFAULT_PATH):
    if not os.path.exists(path):
        return []

    references = []
    with open(path, newline="", encoding="utf-8") as fh:
        for line, row in enumerate(csv.DictReader(fh), 2):
            raw_price = (row.get("price_chf_m2") or "").strip()
            if not raw_price:
                continue
            try:
                price = float(raw_price)
            except ValueError as exc:
                raise ValueError(f"{path}:{line}: invalid price_chf_m2") from exc
            if price <= 0:
                raise ValueError(f"{path}:{line}: price_chf_m2 must be positive")

            raw_bfs = (row.get("bfs") or "").strip()
            if raw_bfs and not raw_bfs.isdigit():
                raise ValueError(f"{path}:{line}: bfs must be an integer")
            references.append(
                LandPriceReference(
                    bfs=int(raw_bfs) if raw_bfs else None,
                    municipality=(row.get("municipality") or "").strip(),
                    zone_pattern=(row.get("zone_pattern") or "").strip(),
                    price_chf_m2=price,
                    source=(row.get("source") or "").strip(),
                    source_url=(row.get("source_url") or "").strip(),
                    as_of=(row.get("as_of") or "").strip(),
                    note=(row.get("note") or "").strip(),
                )
            )
    return references


def resolve(references, bfs, municipality, zone):
    municipality_key = (municipality or "").strip().casefold()
    zone_key = (zone or "").strip().casefold()
    try:
        bfs_key = int(bfs)
    except (TypeError, ValueError):
        bfs_key = None

    matches = []
    for order, ref in enumerate(references):
        if ref.bfs is not None and ref.bfs != bfs_key:
            continue
        if ref.municipality and ref.municipality.casefold() != municipality_key:
            continue
        if ref.zone_pattern and not fnmatch.fnmatch(
            zone_key, ref.zone_pattern.casefold()
        ):
            continue

        # BFS/name identify the same scope, so each contributes one level of
        # specificity. File order is the deterministic tie-breaker.
        location = int(ref.bfs is not None or bool(ref.municipality))
        zone_specific = int(bool(ref.zone_pattern))
        score = location * 2 + zone_specific
        matches.append((score, -order, ref))

    return max(matches, default=(None, None, None))[2]
