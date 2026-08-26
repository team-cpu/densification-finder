"""Official cadastral transport-surface classification.

Vacant cadastral parcels are not necessarily development sites: roads, paths,
sidewalks, traffic islands and rail corridors can have their own EGRID and no
GWR building.  The AV ``LCSF`` layer describes the actual land cover, so it is
a materially stronger signal than an address keyword or a long/thin geometry
heuristic.

Only the four transport classes are downloaded, per municipality, and cached
next to the parcel XML.  A parcel is treated as transport land when at least
60% of its cadastral area is covered by those classes.  That conservative
threshold keeps a normal plot with a driveway from being hidden.
"""
import concurrent.futures
import os
import re
import time
import urllib.parse
import urllib.request

from shapely.geometry import Polygon
from shapely.strtree import STRtree

import paths


WFS = "https://geodienste.ch/db/av_0/deu"
TRANSPORT_TYPES = ("Strasse_Weg", "Trottoir", "Verkehrsinsel", "Bahn")
MIN_TRANSPORT_SHARE = 0.60


def cache_path(bfs):
    return os.path.join(paths.DATA, f"landcover_transport_{int(bfs)}.xml")


def _filter(bfs):
    kinds = "".join(
        "<fes:PropertyIsEqualTo>"
        "<fes:ValueReference>Art</fes:ValueReference>"
        f"<fes:Literal>{kind}</fes:Literal>"
        "</fes:PropertyIsEqualTo>"
        for kind in TRANSPORT_TYPES
    )
    return (
        '<fes:Filter xmlns:fes="http://www.opengis.net/fes/2.0">'
        "<fes:And>"
        "<fes:PropertyIsEqualTo>"
        "<fes:ValueReference>BFSNr</fes:ValueReference>"
        f"<fes:Literal>{int(bfs)}</fes:Literal>"
        "</fes:PropertyIsEqualTo>"
        f"<fes:Or>{kinds}</fes:Or>"
        "</fes:And>"
        "</fes:Filter>"
    )


def fetch(bfs, retries=3):
    """Download and cache one municipality's transport land cover."""
    path = cache_path(bfs)
    if os.path.exists(path) and os.path.getsize(path) > 1000:
        return path

    query = urllib.parse.urlencode(
        {
            "SERVICE": "WFS",
            "VERSION": "2.0.0",
            "REQUEST": "GetFeature",
            "TYPENAMES": "ms:LCSF",
            "COUNT": "40000",
            "FILTER": _filter(bfs),
        }
    )
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(f"{WFS}?{query}", timeout=300) as response:
                body = response.read()
            if b"<ms:LCSF" not in body and b'numberReturned="0"' not in body:
                raise RuntimeError("unexpected LCSF WFS payload")
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as handle:
                handle.write(body)
            return path
        except Exception:
            if attempt == retries - 1:
                raise
            time.sleep(5 * (attempt + 1))


def prefetch(bfs_numbers, workers=6, progress=None):
    """Fetch municipality caches concurrently and return failures by BFS.

    Six workers keep a canton-wide refresh practical without placing the much
    higher burst load on the public cadastral service that an unbounded pool
    would create. Existing cache files return immediately.
    """
    numbers = sorted({int(bfs) for bfs in bfs_numbers})
    failures = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(fetch, bfs): bfs for bfs in numbers}
        for completed, future in enumerate(
            concurrent.futures.as_completed(futures), 1
        ):
            bfs = futures[future]
            try:
                future.result()
            except Exception as exc:
                failures[bfs] = str(exc)
            if progress:
                progress(completed, len(numbers), bfs, failures.get(bfs))
    return failures


def _rings(block, kind):
    rings = []
    for part in re.findall(rf"<gml:{kind}>(.*?)</gml:{kind}>", block, re.S):
        for positions in re.findall(
            r"<gml:posList[^>]*>([^<]+)</gml:posList>", part
        ):
            values = [float(value) for value in positions.split()]
            ring = list(zip(values[0::2], values[1::2]))
            if len(ring) >= 4:
                rings.append(ring)
    return rings


def _valid(geometry):
    return geometry if geometry.is_valid else geometry.buffer(0)


def load(bfs):
    """Return transport polygons, or ``None`` when the layer is unavailable."""
    path = cache_path(bfs)
    if not os.path.exists(path):
        return None

    with open(path, encoding="utf-8", errors="replace") as handle:
        xml = handle.read()

    surfaces = []
    for feature in re.findall(r"<ms:LCSF\b.*?</ms:LCSF>", xml, re.S):
        art = re.search(r"<ms:Art>([^<]+)</ms:Art>", feature)
        if art is None or art.group(1) not in TRANSPORT_TYPES:
            continue
        # A feature can be a MultiSurface. Treat every member polygon as one
        # index entry; LCSF surfaces are mutually exclusive, so overlap areas
        # can safely be summed later.
        for polygon in re.findall(r"<gml:Polygon\b.*?</gml:Polygon>", feature, re.S):
            exterior = _rings(polygon, "exterior")
            if not exterior:
                continue
            geometry = _valid(Polygon(exterior[0], _rings(polygon, "interior")))
            if not geometry.is_empty:
                surfaces.append(geometry)
    return surfaces


class TransportSurfaceIndex:
    """Spatial index reused for every parcel in one municipality."""

    def __init__(self, surfaces):
        self.surfaces = surfaces
        self.index = STRtree(surfaces) if surfaces else None

    def share(self, parcel):
        if self.index is None or parcel.is_empty or parcel.area <= 0:
            return 0.0
        covered = sum(
            parcel.intersection(self.surfaces[index]).area
            for index in self.index.query(parcel)
        )
        return min(1.0, covered / parcel.area)
