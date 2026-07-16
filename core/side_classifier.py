"""
Directional FIR classifier: casts a ray from each endpoint OUTWARD along the
route's direction of travel, and returns only FIRs whose centroid falls
inside a narrow angular corridor around that ray.
"""

from math import radians, degrees, sin, cos, asin, atan2, sqrt


def _haversine_km(lat1, lon1, lat2, lon2):
    R = 6371.0
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat/2)**2 + cos(radians(lat1))*cos(radians(lat2))*sin(dlon/2)**2
    return 2 * R * asin(sqrt(a))


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


def classify_firs_directional(
    fir_res, pfx_res,
    coords,              # list of (lat, lon) along reroute in order
    max_distance_km=4000,
    min_distance_km=250,
    corridor_deg=45,
    exclude_prefix=None,
):
    """
    coords[0]        = FIRST waypoint
    coords[-1]       = LAST  waypoint

    beyond_first     = ray from FIRST in direction (second_wpt -> first_wpt)
    beyond_last      = ray from LAST  in direction (second_to_last -> last)

    corridor_deg     = half-angle in degrees around the ray for a FIR to count
    min_distance_km  = ignore FIRs closer than this (source FIR / very close ones)
    max_distance_km  = ignore FIRs further than this
    """
    if len(coords) < 2:
        return {"beyond_first": [], "beyond_last": [],
                "prefixes_first": [], "prefixes_last": []}

    first_lat, first_lon = coords[0]
    last_lat,  last_lon  = coords[-1]

    # Direction of travel entering FIRST (from 2nd point back to 1st)
    second_lat, second_lon = coords[1]
    bearing_beyond_first = _bearing_deg(second_lat, second_lon,
                                        first_lat,  first_lon)

    # Direction of travel exiting LAST (from 2nd-last to last)
    stl_lat, stl_lon = coords[-2]
    bearing_beyond_last = _bearing_deg(stl_lat, stl_lon,
                                       last_lat, last_lon)

    exclude_prefix = (exclude_prefix or "").upper()
    beyond_first, beyond_last = [], []

    for f in fir_res.firs:
        try:
            c = f["geom"].centroid
            clat, clon = c.y, c.x
        except Exception:
            continue

        prefix = pfx_res.prefix_for_fir(f["code"]) or f["icao"] or f["code"][:2]
        if not prefix or prefix.upper() == exclude_prefix:
            continue

        # ----- Test "beyond FIRST" ray -----
        d1 = _haversine_km(first_lat, first_lon, clat, clon)
        if min_distance_km <= d1 <= max_distance_km:
            brg = _bearing_deg(first_lat, first_lon, clat, clon)
            if _angular_diff(brg, bearing_beyond_first) <= corridor_deg:
                beyond_first.append({
                    "fir_code": f["code"], "prefix": prefix,
                    "centroid": (clat, clon), "distance_km": d1,
                    "bearing_diff": _angular_diff(brg, bearing_beyond_first),
                })
                continue  # don't double-count

        # ----- Test "beyond LAST" ray -----
        d2 = _haversine_km(last_lat, last_lon, clat, clon)
        if min_distance_km <= d2 <= max_distance_km:
            brg = _bearing_deg(last_lat, last_lon, clat, clon)
            if _angular_diff(brg, bearing_beyond_last) <= corridor_deg:
                beyond_last.append({
                    "fir_code": f["code"], "prefix": prefix,
                    "centroid": (clat, clon), "distance_km": d2,
                    "bearing_diff": _angular_diff(brg, bearing_beyond_last),
                })

    pfx_first = sorted({x["prefix"] for x in beyond_first})
    pfx_last  = sorted({x["prefix"] for x in beyond_last})

    return {
        "beyond_first": beyond_first,
        "beyond_last": beyond_last,
        "prefixes_first": pfx_first,
        "prefixes_last": pfx_last,
        "bearing_beyond_first": bearing_beyond_first,
        "bearing_beyond_last": bearing_beyond_last,
    }