import json
from shapely.geometry import shape, Point, LineString


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
import json
from shapely.geometry import shape, Point, LineString

class FIRResolver:
    def __init__(self, geojson_path):
        with open(geojson_path, "r", encoding="utf-8") as f:
            gj = json.load(f)
        self.firs = []
        for feat in gj.get("features", []):
            p = feat.get("properties", {}) or {}
            code = (p.get("fir_code") or p.get("FIR") or p.get("icao_code")
                    or p.get("id") or "").upper()
            icao = (p.get("icao") or "").upper()
            try:
                geom = shape(feat["geometry"])
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
        if len(coords) < 2: return []
        line = LineString([(lon, lat) for lat, lon in coords])
        return [(f["code"], f["icao"]) for f in self.firs if f["geom"].intersects(line)]