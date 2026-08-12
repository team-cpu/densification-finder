"""External links for a parcel result.

AGIS consumes Swiss LV95 coordinates. Google Maps consumes WGS84 latitude and
longitude, so the latter links use Swisstopo's published approximation (better
than one metre throughout Switzerland) rather than adding a projection
dependency solely for two URLs.
"""
import math
import urllib.parse


AGIS_MAP = (
    "https://www.ag.ch/geoportal/apps/onlinekarten/?welcome="
    "&basemap=base_landeskarten_sw::topicmaps.geo.ag.ch,1,true"
    "&center={e},{n}&z=13&info={e},{n},2"
)
GOOGLE_MAP = "https://www.google.com/maps/search/?"
GOOGLE_STREET_VIEW = "https://www.google.com/maps/@?"


def _coordinates(east, north):
    try:
        e, n = float(east), float(north)
    except (TypeError, ValueError):
        return None
    return (e, n) if math.isfinite(e) and math.isfinite(n) else None


def agis_link(east, north):
    coordinates = _coordinates(east, north)
    if coordinates is None:
        return None
    e, n = coordinates
    return AGIS_MAP.format(e=f"{e:.2f}", n=f"{n:.2f}")


def lv95_to_wgs84(east, north):
    """Return ``(latitude, longitude)`` from LV95 east/north coordinates.

    Formula: Swisstopo, "Approximate formulas for the transformation between
    Swiss projection coordinates and WGS84", inverse transformation.
    """
    coordinates = _coordinates(east, north)
    if coordinates is None:
        return None
    e, n = coordinates
    y = (e - 2_600_000.0) / 1_000_000.0
    x = (n - 1_200_000.0) / 1_000_000.0

    longitude = (
        2.6779094
        + 4.728982 * y
        + 0.791484 * y * x
        + 0.1306 * y * x**2
        - 0.0436 * y**3
    ) * 100.0 / 36.0
    latitude = (
        16.9023892
        + 3.238272 * x
        - 0.270978 * y**2
        - 0.002528 * x**2
        - 0.0447 * y**2 * x
        - 0.0140 * x**3
    ) * 100.0 / 36.0
    return latitude, longitude


def google_map_link(east, north):
    coordinates = lv95_to_wgs84(east, north)
    if coordinates is None:
        return None
    latitude, longitude = coordinates
    query = urllib.parse.urlencode(
        {"api": 1, "query": f"{latitude:.7f},{longitude:.7f}"}
    )
    return GOOGLE_MAP + query


def google_street_view_link(east, north, fallback_east=None, fallback_north=None):
    """Open Street View at the entrance, or at the parcel as a safe fallback.

    ``sv_e``/``sv_n`` were added after the first production database had
    already been copied to Railway's persistent volume. Accepting fallback
    coordinates keeps that older schema readable while newer databases use the
    more reliable road-facing entrance point.
    """
    coordinates = lv95_to_wgs84(east, north)
    if coordinates is None:
        coordinates = lv95_to_wgs84(fallback_east, fallback_north)
    if coordinates is None:
        return None
    latitude, longitude = coordinates
    query = urllib.parse.urlencode(
        {
            "api": 1,
            "map_action": "pano",
            "viewpoint": f"{latitude:.7f},{longitude:.7f}",
        }
    )
    return GOOGLE_STREET_VIEW + query
