"""
Directional FIR classifier (search-slice logic).

At each end of the closed segment a REFERENCE LINE continues the end leg
outward:
    at FIRST : direction  wpt2  -> wpt1   (and beyond)
    at LAST  : direction  wptN-1 -> wptN  (and beyond)
A SEARCH SLICE of +/- corridor_deg (default 45 deg) is opened around each
reference line. Every FIR whose polygon overlaps a slice is picked up for
that side.
"""

from math import radians, degrees, sin, cos, asin, atan2, sqrt
from shapely.geometry import Polygon, Point
from shapely.ops import nearest_points, unary_union
from shapely.affinity import translate
from shapely.prepared import prep

R_EARTH = 6371.0


def _haversine_km(lat1, lon1, lat2, lon2):
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat/2)**2 + cos(radians(lat1))*cos(radians(lat2))*sin(dlon/2)**2
    return 2 * R_EARTH * asin(sqrt(a))


def _bearing_deg(lat1, lon1, lat2, lon2):
    """Initial great-circle bearing from point1 to point2, 0..360."""
    phi1, phi2 = radians(lat1), radians(lat2)
    dlon = radians(lon2 - lon1)
    x = sin(dlon) * cos(phi2)
    y = cos(phi1)*sin(phi2) - sin(phi1)*cos(phi2)*cos(dlon)
    return (degrees(atan2(x, y)) + 360) % 360


def _angular_diff(a, b):
    """Smallest angle between two bearings (0..180)."""
    d = abs(a - b) % 360
    return min(d, 360 - d)


def _destination(lat, lon, brg, dist_km):
    """Point reached from (lat, lon) going dist_km on bearing brg."""
    d = dist_km / R_EARTH
    b, p1, l1 = radians(brg), radians(lat), radians(lon)
    p2 = asin(sin(p1)*cos(d) + cos(p1)*sin(d)*cos(b))
    l2 = l1 + atan2(sin(b)*sin(d)*cos(p1), cos(d) - sin(p1)*sin(p2))
    return degrees(p2), degrees(l2)


def _unwrap(lon, ref):
    while lon - ref > 180:
        lon -= 360
    while lon - ref < -180:
        lon += 360
    return lon


def _build_slice(lat, lon, bearing, half_deg, min_km, max_km,
                 step_km=250, arc_step_deg=3):
    """
    Wedge polygon (lon/lat) from the waypoint, bearing +/- half_deg,
    between min_km and max_km. Edges are densified so they follow the
    great circle. Returns (raw_wedge, wedge_for_testing).
    """
    def pt(b, d):
        la, lo = _destination(lat, lon, b, d)
        return (_unwrap(lo, lon), la)

    left, right = bearing - half_deg, bearing + half_deg
    n_d = max(2, int((max_km - min_km) / step_km) + 1)
    dists = [min_km + (max_km - min_km) * i / (n_d - 1) for i in range(n_d)]
    n_a = max(2, int(2 * half_deg / arc_step_deg) + 1)
    angles = [left + (right - left) * i / (n_a - 1) for i in range(n_a)]

    ring = []
    if min_km <= 0:
        ring.append((lon, lat))
    ring += [pt(left, d) for d in dists if d > 0]            # left edge out
    ring += [pt(a, max_km) for a in angles]                   # outer arc
    ring += [pt(right, d) for d in reversed(dists) if d > 0]  # right edge in
    if min_km > 0:
        ring += [pt(a, min_km) for a in reversed(angles)]     # inner arc

    wedge = Polygon(ring).buffer(0)

    # Antimeridian: add shifted copies so FIRs on the other side still hit
    minx, _, maxx, _ = wedge.bounds
    parts = [wedge]
    if maxx > 180:
        parts.append(translate(wedge, xoff=-360))
    if minx < -180:
        parts.append(translate(wedge, xoff=360))
    test = unary_union(parts) if len(parts) > 1 else wedge
    return wedge, test


def _hit(geom, slc, slc_prep, o_lat, o_lon, ray_brg):
    """Returns overlap info if FIR polygon overlaps the slice, else None."""
    try:
        if not slc_prep.intersects(geom):
            return None
        inter = geom.intersection(slc)
        if inter.is_empty:
            return None
    except Exception:
        return None
    near = nearest_points(inter, Point(o_lon, o_lat))[0]
    rep = inter.representative_point()
    # NEW: centre of the WHOLE FIR (not just the part inside the slice).
    # Used later to decide which side a country belongs to if it hits both.
    fc = geom.centroid
    return {
        "distance_km": _haversine_km(o_lat, o_lon, near.y, near.x),
        "centroid": (rep.y, rep.x),     # point of the FIR inside the slice
        "fir_centroid": (fc.y, fc.x),   # NEW: centre of the whole FIR
        "bearing_diff": _angular_diff(
            _bearing_deg(o_lat, o_lon, rep.y, rep.x), ray_brg),
    }


def classify_firs_directional(
    fir_res, pfx_res,
    coords,              # reference path (lat, lon), FIRST -> LAST
    max_distance_km=6000,
    min_distance_km=0,
    corridor_deg=45,
    exclude_prefix=None,
):
    empty = {"beyond_first": [], "beyond_last": [],
             "prefixes_first": [], "prefixes_last": [],
             "slice_first": [], "slice_last": []}
    if len(coords) < 2:
        return empty

    first_lat, first_lon = coords[0]
    last_lat, last_lon = coords[-1]
    second_lat, second_lon = coords[1]
    stl_lat, stl_lon = coords[-2]

    # Reference lines = end legs continued outward
    bearing_beyond_first = _bearing_deg(second_lat, second_lon, first_lat, first_lon)
    bearing_beyond_last = _bearing_deg(stl_lat, stl_lon, last_lat, last_lon)

    raw_f, slc_f = _build_slice(first_lat, first_lon, bearing_beyond_first,
                                corridor_deg, min_distance_km, max_distance_km)
    raw_l, slc_l = _build_slice(last_lat, last_lon, bearing_beyond_last,
                                corridor_deg, min_distance_km, max_distance_km)
    prep_f, prep_l = prep(slc_f), prep(slc_l)

    exclude_prefix = (exclude_prefix or "").upper()
    beyond_first, beyond_last = [], []

    for f in fir_res.firs:
        prefix = pfx_res.prefix_for_fir(f["code"]) or f["icao"] or f["code"][:2]
        if not prefix or prefix.upper() == exclude_prefix:
            continue

        h1 = _hit(f["geom"], slc_f, prep_f, first_lat, first_lon, bearing_beyond_first)
        if h1:
            beyond_first.append({"fir_code": f["code"], "prefix": prefix, **h1})

        h2 = _hit(f["geom"], slc_l, prep_l, last_lat, last_lon, bearing_beyond_last)
        if h2:
            beyond_last.append({"fir_code": f["code"], "prefix": prefix, **h2})

    beyond_first.sort(key=lambda x: x["distance_km"])
    beyond_last.sort(key=lambda x: x["distance_km"])

    def _latlon(poly):
        g = poly if poly.geom_type == "Polygon" else max(poly.geoms, key=lambda p: p.area)
        return [(la, lo) for lo, la in g.exterior.coords]

    return {
        "beyond_first": beyond_first,
        "beyond_last": beyond_last,
        "prefixes_first": sorted({x["prefix"] for x in beyond_first}),
        "prefixes_last": sorted({x["prefix"] for x in beyond_last}),
        "bearing_beyond_first": bearing_beyond_first,
        "bearing_beyond_last": bearing_beyond_last,
        "slice_first": _latlon(raw_f),     # for drawing on the map
        "slice_last": _latlon(raw_l),
    }