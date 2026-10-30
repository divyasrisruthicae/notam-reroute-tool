import json
from shapely.geometry import shape, Point, LineString, Polygon
from shapely.affinity import translate      # NEW: to shift shapes by 360°
from shapely.ops import unary_union         # NEW: to join shape copies


def _fix_antimeridian(geom):
    """
    NEW. Fixes FIRs that cross the 180° line (Pacific), e.g. KZAK Oakland.
    Their points jump from +130 to -120, and shapely joins them the LONG
    way round the world, so the FIR wrongly covers Asia, Africa, India...

    Fix: if a shape is wider than 180°, move its negative longitudes up
    by 360 (-120 -> 240), so the shape sits correctly over the Pacific
    (130 -> 240). We also keep a copy shifted back by -360, so routes on
    EITHER side of 180° (Japan side or Hawaii side) still hit it.

    Normal FIRs (narrower than 180°) are returned unchanged.
    """
    def fix_poly(p):
        minx, _, maxx, _ = p.bounds
        if maxx - minx <= 180:
            return p                                   # normal FIR, no change

        def shift(ring):
            return [((x + 360) if x < 0 else x, y) for x, y in ring]

        q = Polygon(shift(p.exterior.coords),
                    [shift(i.coords) for i in p.interiors]).buffer(0)
        qminx, _, qmaxx, _ = q.bounds
        if qmaxx - qminx >= 180:
            return p                                   # really that wide, leave it
        # Shape over the Pacific + a copy on the other side of 180°
        return unary_union([q, translate(q, xoff=-360)])

    if geom.geom_type == "Polygon":
        return fix_poly(geom)
    if geom.geom_type == "MultiPolygon":
        return unary_union([fix_poly(p) for p in geom.geoms])
    return geom


class FIRResolver:
    def __init__(self, geojson_path):
        with open(geojson_path, "r", encoding="utf-8") as f:
            gj = json.load(f)
        self.firs = []
        for feat in gj.get("features", []):
            p = feat.get("properties", {}) or {}
            code = (
                p.get("fir_code") or p.get("FIR") or p.get("icao_code") or p.get("id") or ""
            ).upper()
            icao = (p.get("icao") or "").upper()
            try:
                geom = shape(feat["geometry"])
                geom = _fix_antimeridian(geom)   # NEW: fix FIRs crossing 180°
            except Exception:
                continue
            self.firs.append({"code": code, "icao": icao, "geom": geom})

    def fir_for_point(self, lat, lon):
        p = Point(lon, lat)
        for f in self.firs:
            if f["geom"].contains(p):
                return f["code"], f["icao"]
        return None, None

    def firs_crossing_line(self, coords):
        if len(coords) < 2:
            return []
        line = LineString([(lon, lat) for lat, lon in coords])
        return [(f["code"], f["icao"]) for f in self.firs if f["geom"].intersects(line)]