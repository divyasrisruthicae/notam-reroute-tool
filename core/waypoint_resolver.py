import pandas as pd
import re
from math import radians, sin, cos, asin, sqrt


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
    A single waypoint IDENT (e.g. 'BBS') can exist in MULTIPLE countries
    (BBS = Beni Abbes/Algeria AND Bhubaneswar/India). We keep ALL candidates
    and pick the right one using nearby-context coordinates.
    """

    def __init__(self, csv_path, coord_col=None):
        df = pd.read_csv(csv_path, dtype=str, sep=None, engine="python")
        df.columns = [c.strip() for c in df.columns]

        # detect coord column
        if coord_col is None:
            for c in df.columns:
                if c.upper() in ("COORD", "COORDS", "LATLON", "LATLNG"):
                    coord_col = c
                    break
            if coord_col is None:
                for c in df.columns[::-1]:
                    sample = df[c].dropna().astype(str).head(5).tolist()
                    if sample and any(
                        re.match(r"\d{6}[NS]\d{7}[EW]", s.strip()) for s in sample
                    ):
                        coord_col = c
                        break
                if coord_col is None:
                    coord_col = df.columns[-1]
        self.coord_col = coord_col

        # Drop separator/header noise rows like '------,-----,----'
        df = df[df["NDID"].astype(str).str.match(r"^[A-Z0-9]+$", na=False)]

        df["NDID_U"] = df["NDID"].str.upper().str.strip()
        df["NDIC_U"] = df["NDIC"].str.upper().str.strip()
        df["COORDVAL"] = df[coord_col].astype(str).map(_parse_dms)
        df = df.dropna(subset=["COORDVAL"])

        # Keep ALL candidates per ident (dedupe only exact coord+ndic dupes)
        self.candidates = {}
        for r in df.itertuples():
            self.candidates.setdefault(r.NDID_U, [])
            entry = {"coord": r.COORDVAL, "ndic": r.NDIC_U}
            if not any(
                e["coord"] == entry["coord"] and e["ndic"] == entry["ndic"]
                for e in self.candidates[r.NDID_U]
            ):
                self.candidates[r.NDID_U].append(entry)

    def resolve(self, ident, ref_coord=None):
        cands = self.candidates.get(ident.upper().strip())
        if not cands:
            return None
        if len(cands) == 1 or ref_coord is None:
            return cands[0]
        rlat, rlon = ref_coord
        return min(
            cands,
            key=lambda c: _haversine_km(rlat, rlon, c["coord"][0], c["coord"][1]),
        )

    def coord(self, ident, ref_coord=None):
        rec = self.resolve(ident, ref_coord)
        return rec["coord"] if rec else None

    def ndic(self, ident, ref_coord=None):
        rec = self.resolve(ident, ref_coord)
        return rec["ndic"] if rec else None

    def has_multiple(self, ident):
        return len(self.candidates.get(ident.upper().strip(), [])) > 1

    def resolve_route(self, waypoints, anchor_coord=None):
        """
        Resolve an ordered list of idents, disambiguating duplicates by
        proximity to unambiguous neighbours (and/or NOTAM Q-line anchor).
        Returns: [{waypoint, coord, ndic, source, ambiguous}, ...]
        """
        idents = [w.upper().strip() for w in waypoints]

        # Pass 1: anchors = coords of unambiguous waypoints
        anchors = []
        for w in idents:
            cands = self.candidates.get(w, [])
            if len(cands) == 1:
                anchors.append(cands[0]["coord"])

        if anchors:
            alat = sum(c[0] for c in anchors) / len(anchors)
            alon = sum(c[1] for c in anchors) / len(anchors)
            centroid = (alat, alon)
        elif anchor_coord:
            centroid = anchor_coord
        else:
            centroid = None

        # Pass 2: resolve each, nearest candidate to centroid
        resolved = []
        for w in idents:
            cands = self.candidates.get(w, [])
            if not cands:
                resolved.append({
                    "waypoint": w, "coord": None, "ndic": None,
                    "source": "unresolved", "ambiguous": False,
                })
                continue

            ambiguous = len(cands) > 1
            if not ambiguous:
                chosen = cands[0]
            elif centroid:
                chosen = min(
                    cands,
                    key=lambda c: _haversine_km(
                        centroid[0], centroid[1], c["coord"][0], c["coord"][1]
                    ),
                )
            else:
                chosen = cands[0]

            resolved.append({
                "waypoint": w,
                "coord": chosen["coord"],
                "ndic": chosen["ndic"],
                "source": "ndic" if chosen["ndic"] else "coord",
                "ambiguous": ambiguous,
            })

        return resolved