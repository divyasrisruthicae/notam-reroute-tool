import re

BIDIR_RE = re.compile(r"BIDIRECTIONAL", re.I)

# Noise tokens that are NEVER waypoints
_NOISE = {
    "DCT", "AND", "OR", "AVBL", "FOR", "EB", "WB", "OVF", "TFC", "AT",
    "ACFT", "BLW", "ABV", "NOT", "VICE", "VERSA", "RTE", "ALTN", "BTN",
    "TO", "VOR", "NDB", "SFC", "UNL", "EXC", "SUBJ", "COOR", "ACC",
    "SEGMENT", "OF", "ATS", "CLSD", "TEMP", "ADJUST", "AS", "FLW",
    "BEYOND", "THE", "IN", "WI", "DUE", "REASON", "SCHEDULED", "FLIGHTS",
    "ALONG", "ALL", "KTN", "DEP", "ARR", "ADJ", "LOCAL", "REFER",
    "RERTE", "AFFECTED",
}


def _clean(s):
    return re.sub(r"\s+", " ", s).strip(" .,")


def _tokenize(route_str):
    """'CEA-G450-JJS-DCT-JRS-DCT-VVZ' or 'TUSYR DCT TANF' -> ordered token list."""
    s = route_str.replace("(BIDIRECTIONAL)", "")
    s = re.sub(r"\s*-\s*", "-", s)
    s = s.replace(" ", "-")
    return [t for t in re.split(r"[-]+", s) if t and t.strip()]


def _is_waypoint(tok):
    """True only if tok is a real fix/VOR ident (not airway, not FL, not noise)."""
    t = re.sub(r"[^A-Z0-9]", "", tok.upper())
    if not t:
        return False
    if t in _NOISE:
        return False
    # airway: single letter + digits (A412, G450, W41, N895, Q26, L524)
    if re.match(r"^[A-Z]\d{1,4}$", t):
        return False
    # flight level: FL310, F330, FL450, or bare 310 / 0330
    if re.match(r"^F?L?\d{2,4}$", t):
        return False
    # must contain at least one letter (real fix idents do)
    if not re.search(r"[A-Z]", t):
        return False
    return True


def _only_waypoints(tokens):
    out = []
    for t in tokens:
        if _is_waypoint(t):
            out.append(re.sub(r"[^A-Z0-9]", "", t.upper()))
    return out


def extract_reroutes(e_text):
    """
    Returns list of reroute dicts. ONLY real reroutes are returned
    (Damascus 'AVBL FOR .. OVF' lines, 'ALTN RTE' lines, numbered China
    routes, VOR-adjust lines, and standalone dash routes).
    FL ranges and closure-only lines (no ALTN RTE) are ignored.
    """
    results = []
    lines = [l.strip() for l in e_text.splitlines() if l.strip()]
    joined = " ".join(lines)

    # ---------- Style A: Damascus dots (MUST contain OVF) ----------
    # ".TUSYR DCT TANF DCT NAMBO AVBL FOR EB OVF TFC AT ..."
    for line in lines:
        m = re.match(
            r"\.?\s*([A-Z0-9\s\-']+?)\s+AVBL\s+FOR\s+(EB|WB)\s+OVF",
            line, re.I,
        )
        if m:
            wps = _only_waypoints(_tokenize(m.group(1)))
            if len(wps) >= 2:
                results.append({
                    "raw": _clean(line),
                    "tokens": wps,
                    "waypoints": wps,
                    "bidirectional": False,
                    "direction_hint": m.group(2).upper(),
                    "closed_airway": None,
                    "closed_segment": None,
                    "from_airports": [],
                    "to_airports": [],
                })

    # ---------- Style B: ALTN RTE (anchor on ALTN RTE, look BACK for closure) ----------
    for m in re.finditer(
        r"ALTN\s+RTE:\s*([A-Z0-9\-\s\.']+?)(?=\.\s|\(|$)",
        joined, re.I,
    ):
        alt = m.group(1)
        wps = _only_waypoints(_tokenize(alt))
        if len(wps) < 2:
            continue

        # nearest preceding "<AIRWAY> NOT AVBL BTN <A> AND <B>"
        before = joined[:m.start()]
        closed_awy, seg = None, None
        for cm in re.finditer(
            r"([A-Z]\d{1,4})\s+NOT\s+AVBL(?:\s+BTN\s+([A-Z]{2,5})\s+AND\s+([A-Z]{2,5}))?",
            before, re.I,
        ):
            closed_awy = cm.group(1)
            if cm.group(2) and cm.group(3):
                seg = (cm.group(2), cm.group(3))
            else:
                seg = None
        # bidirectional check in a small window after the route
        window = joined[m.start(): m.end() + 40]
        bidir = bool(BIDIR_RE.search(window))

        results.append({
            "raw": _clean(alt),
            "tokens": wps,
            "waypoints": wps,
            "bidirectional": bidir,
            "direction_hint": None,
            "closed_airway": closed_awy,
            "closed_segment": seg,
            "from_airports": [],
            "to_airports": [],
        })

    # ---------- Style C: numbered China "N. X TO Y: route" ----------
    for m in re.finditer(
        r"\d+\.\s*([A-Z0-9\s\-]+?)\s+TO\s+([A-Z0-9\s\-]+?):\s*([A-Z0-9\-\s]+?)(?=\.|$)",
        joined,
    ):
        from_ctx = m.group(1).strip()
        to_ctx = m.group(2).strip()
        alt = re.sub(r"\bAND\b|\bBEYOND\b", "", m.group(3), flags=re.I)
        wps = _only_waypoints(_tokenize(alt))
        from_airports = re.findall(r"\b([A-Z]{4})\b", from_ctx)
        to_airports = re.findall(r"\b([A-Z]{4})\b", to_ctx)
        if len(wps) >= 2:
            results.append({
                "raw": f"{from_ctx} TO {to_ctx} :: {_clean(m.group(3))}",
                "tokens": wps,
                "waypoints": wps,
                "bidirectional": False,
                "direction_hint": None,
                "closed_airway": None,
                "closed_segment": None,
                "from_airports": from_airports,
                "to_airports": to_airports,
            })

    # ---------- Style E: VOR adjust "ADJUST TO ... , AND VICE VERSA" ----------
    for vl in re.findall(
        r"ADJUST\s+TO\s+([^\n\.]+?)(?:,\s*AND\s+VICE\s+VERSA|\.|$)",
        joined, re.I,
    ):
        quoted = re.findall(r"'([A-Z0-9]{2,5})'|\"([A-Z0-9]{2,5})\"", vl)
        quoted = [a or b for a, b in quoted]

        if quoted:
            # Quoted VOR style: use the quoted IDs directly (PLT, SHR)
            wps = [w for w in quoted if _is_waypoint(w)]
        else:
            # Unquoted dash style: OMDEM-V173-TOCEF-KIGUN
            wps = _only_waypoints(_tokenize(vl))

        if len(wps) >= 2:
            results.append({
                "raw": _clean(vl),
                "tokens": wps,
                "waypoints": wps,
                "bidirectional": bool(re.search(r"VICE\s+VERSA", vl, re.I)),
                "direction_hint": None,
                "closed_airway": None,
                "closed_segment": None,
                "from_airports": [],
                "to_airports": [],
            })

    # ---------- Style D: standalone dash route (no ALTN RTE prefix) ----------
    # e.g. top-of-NOTAM  CEA-G450-JJS-DCT-JRS-DCT-VVZ. (BIDIRECTIONAL)
    for line in lines:
        if re.match(r"^[A-Z0-9]{2,5}(-[A-Z0-9]+){2,}\.?\s*(\(BIDIRECTIONAL\))?\s*$",
                    line, re.I):
            wps = _only_waypoints(_tokenize(line))
            if len(wps) >= 2:
                results.append({
                    "raw": _clean(line),
                    "tokens": wps,
                    "waypoints": wps,
                    "bidirectional": bool(BIDIR_RE.search(line)),
                    "direction_hint": None,
                    "closed_airway": None,
                    "closed_segment": None,
                    "from_airports": [],
                    "to_airports": [],
                })

    # ---------- Ensure keys exist ----------
    for r in results:
        r.setdefault("from_airports", [])
        r.setdefault("to_airports", [])

    # ---------- Drop Style-D duplicates ----------
    # Signatures (waypoints+bidir) that already have a real closed airway
    closed_sigs = {
        (tuple(r["waypoints"]), r["bidirectional"])
        for r in results if r["closed_airway"]
    }
    filtered = []
    for r in results:
        sig = (tuple(r["waypoints"]), r["bidirectional"])
        # skip a closure-less duplicate if a closed version exists
        if r["closed_airway"] is None and sig in closed_sigs:
            continue
        filtered.append(r)

    # ---------- Final dedupe ----------
    # closed_airway IN key -> N895 and G472 (same path, diff airway) BOTH kept.
    seen, out = set(), []
    for r in filtered:
        key = (
            tuple(r["waypoints"]),
            r["bidirectional"],
            r["closed_airway"],
            tuple(r["from_airports"]),
            tuple(r["to_airports"]),
        )
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out