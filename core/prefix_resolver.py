import pandas as pd


class PrefixResolver:
    def __init__(self, csv_path):
        df = pd.read_csv(csv_path)
        self.map = {}
        for r in df.itertuples():
            code = str(r.fir_code).upper().strip()
            pfx = str(r.icao_prefix).upper().strip()
            if pfx and pfx != "NAN":
                self.map[code] = pfx

    def prefix_for_fir(self, fir_code):
        if not fir_code:
            return None
        code = fir_code.upper().strip()
        return self.map.get(code) or (code[:2] if code else None)
import pandas as pd

class PrefixResolver:
    def __init__(self, csv_path):
        df = pd.read_csv(csv_path)
        self.map = {}
        for r in df.itertuples():
            code = str(r.fir_code).upper().strip()
            pfx = str(r.icao_prefix).upper().strip()
            if pfx and pfx != "NAN":
                self.map[code] = pfx

    def prefix_for_fir(self, fir_code):
        if not fir_code: return None
        code = fir_code.upper().strip()
        return self.map.get(code) or code[:2] if code else None