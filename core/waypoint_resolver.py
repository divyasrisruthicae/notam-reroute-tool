import pandas as pd
import re
from math import radians, sin, cos, asin, sqrt

COORD_RE = re.compile(
    r'^(\d{2})(\d{2})(\d{2})?([NS])(\d{3})(\d{2})(\d{2})?([EW])$'
)

def parse_coord_token(tok):
    """3105N12452E -> (31.0833, 124.8667). Returns None if not a coord."""
    m = COORD_RE.match(tok.strip().upper())
    if not m:
        return None
    la_d, la_m, la_s, ns, lo_d, lo_m, lo_s, ew = m.groups()
    lat = int(la_d) + int(la_m)/60 + int(la_s or 0)/3600
    lon = int(lo_d) + int(lo_m)/60 + int(lo_s or 0)/3600
    if ns == 'S': lat = -lat
    if ew == 'W': lon = -lon
    return (round(lat, 6), round(lon, 6))
# raw lat/long fixes in NOTAM text: 3105N12452E or 310530N1245212E
_COORD_TOKEN_RE = re.compile(r"^(\d{2})(\d{2})(\d{2})?([NS])(\d{3})(\d{2})(\d{2})?([EW])$")


def _parse_dms(coord_str):
    """'135336N1003548E' -> (13.8933, 100.5967)"""
    m = re.match(r"(\d{6})([NS])(\d{7})([EW])", str(coord_str).strip())
    if not m:
        return None
    lat_raw, ns, lon_raw, ew = m.groups()
    lat = int(lat_raw[:2]) + int(lat_raw[2:4]) / 60 + int(lat_raw[4:6]) / 3600
    lon = int(lon_raw[:3]) + int(lon_raw[3:5]) / 60 + int(lon_raw[5:7]) / 3600
    if ns == "S":
        lat = -lat
    if ew == "W":
        lon = -lon
    return lat, lon


def _haversine_km(lat1, lon1, lat2, lon2):
    R = 6371.0
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return 2 * R * asin(sqrt(a))


class WaypointResolver:
    """
    A single waypoint IDENT can exist in MULTIPLE countries. We keep ALL
    candidates and pick the right one using nearby-context coordinates.
    Raw lat/long fixes (3105N12452E) are resolved directly, no CSV needed.
    """

    def __init__(self, csv_path, coord_col=None):
        df = pd.read_csv(csv_path, dtype=str, sep=None, engine="python")
        df.columns = [c.strip() for c in df.columns]

        if coord_col is None:
            for c in df.columns:
                if c.upper() in ("COORD", "COORDS", "LATLON", "LATLNG"):
                    coord_col = c
                    break
            if coord_col is None:
                for c in df.columns[::-1]:
                    sample = df[c].dropna().astype(str).head(5).tolist()
                    if sample and any(re.match(r"\d{6}[NS]\d{7}[EW]", s.strip()) for s in sample):
                        coord_col = c
                        break
                if coord_col is None:
                    coord_col = df.columns[-1]
        self.coord_col = coord_col

        df = df[df["NDID"].astype(str).str.match(r"^[A-Z0-9]+$", na=False)]
        df["NDID_U"] = df["NDID"].str.upper().str.strip()
        df["NDIC_U"] = df["NDIC"].str.upper().str.strip()
        df["COORDVAL"] = df[coord_col].astype(str).map(_parse_dms)
        df = df.dropna(subset=["COORDVAL"])

        self.candidates = {}
        for r in df.itertuples():
            self.candidates.setdefault(r.NDID_U, [])
            entry = {"coord": r.COORDVAL, "ndic": r.NDIC_U}
            if not any(e["coord"] == entry["coord"] and e["ndic"] == entry["ndic"]
                       for e in self.candidates[r.NDID_U]):
                self.candidates[r.NDID_U].append(entry)

    # ---------- single-ident lookups ----------
    def resolve(self, ident, ref_coord=None):
        ident = str(ident).upper().strip()

        lit = parse_coord_token(ident)          # NEW: raw lat/long fix
        if lit:
            return {"coord": lit, "ndic": None}

        cands = self.candidates.get(ident)
        if not cands:
            return None
        if len(cands) == 1 or ref_coord is None:
            return cands[0]
        rlat, rlon = ref_coord
        return min(cands, key=lambda c: _haversine_km(rlat, rlon, c["coord"][0], c["coord"][1]))

    def coord(self, ident, ref_coord=None):
        rec = self.resolve(ident, ref_coord)
        return rec["coord"] if rec else None

    def ndic(self, ident, ref_coord=None):
        rec = self.resolve(ident, ref_coord)
        return rec["ndic"] if rec else None

    def has_multiple(self, ident):
        if parse_coord_token(ident):
            return False
        return len(self.candidates.get(str(ident).upper().strip(), [])) > 1

    # ---------- route resolution ----------
    def resolve_route(self, waypoints, anchor_coord=None):
        """
        Resolve an ordered list of idents, disambiguating duplicates by
        proximity to unambiguous neighbours (and/or NOTAM Q-line anchor).
        Raw coordinate fixes resolve directly and also act as anchors.
        """
        idents = [str(w).upper().strip() for w in waypoints]

        # Pass 1: anchors = coords of unambiguous waypoints + coord literals
        anchors = []
        for w in idents:
            lit = parse_coord_token(w)
            if lit:
                anchors.append(lit)
                continue
            cands = self.candidates.get(w, [])
            if len(cands) == 1:
                anchors.append(cands[0]["coord"])

        if anchors:
            centroid = (sum(c[0] for c in anchors) / len(anchors),
                        sum(c[1] for c in anchors) / len(anchors))
        elif anchor_coord:
            centroid = anchor_coord
        else:
            centroid = None

        # Pass 2
        resolved = []
        for w in idents:
            lit = parse_coord_token(w)
            if lit:
                resolved.append({"waypoint": w, "coord": lit, "ndic": None,
                                 "source": "coord_literal", "ambiguous": False})
                continue

            cands = self.candidates.get(w, [])
            if not cands:
                resolved.append({"waypoint": w, "coord": None, "ndic": None,
                                 "source": "unresolved", "ambiguous": False})
                continue

            ambiguous = len(cands) > 1
            if not ambiguous:
                chosen = cands[0]
            elif centroid:
                chosen = min(cands, key=lambda c: _haversine_km(
                    centroid[0], centroid[1], c["coord"][0], c["coord"][1]))
            else:
                chosen = cands[0]

            resolved.append({
                "waypoint": w, "coord": chosen["coord"], "ndic": chosen["ndic"],
                "source": "ndic" if chosen["ndic"] else "coord",
                "ambiguous": ambiguous,
            })

        return resolved
