import re
import difflib

BIDIR_RE = re.compile(r"BIDIRECTIONAL", re.I)

# raw lat/long fix: 3105N12452E / 122030N1083917E
_COORD_RE = re.compile(r"^(\d{2})(\d{2})(\d{2})?([NS])(\d{3})(\d{2})(\d{2})?([EW])$")

_AWY_TOK_RE = re.compile(r"^[A-Z]{1,2}\d{1,4}[A-Z]?$")
_APT_TOK_RE = re.compile(r"^[A-Z]{4}$")

# "1. Y711 POINK - MUGUS"  /  "1.L642 KARAN-PTH"  /  "3.W15 LKH-KARAN"
_ITEM_HDR_RE = re.compile(
    r"^\s*\d{1,2}\s*[\.\)]\s*([A-Z]{1,2}\d{1,4}[A-Z]?)\s+"
    r"([A-Z0-9]{2,6})\s*[-\u2013\u2014]\s*([A-Z0-9]{2,6})\s*$"
)

# "ALTN RTE : PONIK DCT 3105N12452E DCT MUGUS"
# Colon REQUIRED, so "ALTN RTE ESTABLISHED DUE TO..." can never match.
_ALTN_LINE_RE = re.compile(r"ALT[N]?\s*(?:RTE|ROUTE)\s*:\s*(.+)$", re.I)

# "- ACFT ON L642:"
_ON_HDR_RE = re.compile(r"^-\s*ACFT\s+ON\s+([A-Z]{1,2}\d{1,4}[A-Z]?)\s*:?\s*$", re.I)

# "- ACFT DEP/ARR VVDL VIA W15 - Q15: CHANGE TO VVDL - W7 (BMT) - ..."
_CHANGE_RE = re.compile(r"^-\s*(.+?)\bVIA\b\s*(.+?)\s*:\s*CHANGE\s+TO\s+(.+)$", re.I)

# Noise tokens that are NEVER waypoints
_NOISE = {
    "DCT", "AND", "OR", "AVBL", "FOR", "EB", "WB", "OVF", "TFC", "AT",
    "ACFT", "BLW", "ABV", "NOT", "VICE", "VERSA", "RTE", "ALTN", "BTN",
    "TO", "VOR", "NDB", "SFC", "UNL", "EXC", "SUBJ", "COOR", "ACC",
    "SEGMENT", "OF", "ATS", "CLSD", "TEMP", "ADJUST", "AS", "FLW",
    "BEYOND", "THE", "IN", "WI", "DUE", "REASON", "SCHEDULED", "FLIGHTS",
    "ALONG", "ALL", "KTN", "DEP", "ARR", "ADJ", "LOCAL", "REFER",
    "RERTE", "AFFECTED",
    # bullet-style (Vietnam) noise
    "ON", "VIA", "CHANGE", "FM", "FROM", "DEVIATE", "LEFT", "RIGHT",
    "SEE", "NOTAM", "FREE", "MET", "BALLOON", "BE", "OPERATED",
    "MENTIONED", "SEGMENTS", "BELOW", "SHALL", "ACT",
}


def _clean(s):
    return re.sub(r"\s+", " ", s).strip(" .,")


def _unwrap(e_text):
    """
    NOTAM E) wraps mid-sentence; rejoin continuation lines onto their bullet.
    Never absorbs a new bullet, a numbered item, or an ALTN RTE line.
    """
    out = []
    for ln in e_text.splitlines():
        s = ln.strip()
        if not s:
            continue
        if out and not re.match(r"^[-+]|^\d{1,2}\s*[\.\)]|^ALT[N]?\s*(RTE|ROUTE)", s, re.I):
            out[-1] = out[-1].rstrip() + " " + s
        else:
            out.append(s)
    return out


def _tokenize(route_str):
    """'CEA-G450-JJS-DCT-JRS' or 'TUSYR DCT TANF' -> ordered token list."""
    s = route_str.replace("(BIDIRECTIONAL)", "")
    s = re.sub(r"\s*-\s*", "-", s)
    s = s.replace(" ", "-")
    return [t for t in re.split(r"[-]+", s) if t and t.strip()]


def _is_waypoint(tok):
    """True only if tok is a real fix/VOR ident (not airway, not FL, not noise)."""
    t = re.sub(r"[^A-Z0-9]", "", tok.upper())
    if not t:
        return False
    # raw lat/long fix IS a waypoint -- checked BEFORE the FL/digit rules
    if _COORD_RE.match(t):
        return True
    if t in _NOISE:
        return False
    if re.match(r"^[A-Z]\d{1,4}$", t):      # airway: A412, G450, W41, N895
        return False
    if re.match(r"^F?L?\d{2,4}$", t):       # flight level
        return False
    if not re.search(r"[A-Z]", t):
        return False
    return True


def _only_waypoints(tokens):
    return [re.sub(r"[^A-Z0-9]", "", t.upper()) for t in tokens if _is_waypoint(t)]


def _split_tokens(seg):
    """Returns (waypoints, airways, airports). 'W7 (BMT)' -> BMT as waypoint."""
    seg = re.sub(r"\(SEE[^)]*\)", "", seg, flags=re.I)
    seg = re.sub(r"\(([A-Z]{2,5})\)", r"- \1", seg)   # parenthetical VOR
    parts = [p.strip(" .,;:") for p in re.split(r"\s*-\s*|\s+", seg) if p.strip(" .,;:")]
    wps, awys, apts = [], [], []
    for p in parts:
        u = re.sub(r"[^A-Z0-9]", "", p.upper())
        if not u or u in _NOISE:
            continue
        if _COORD_RE.match(u):
            wps.append(u)
        elif _AWY_TOK_RE.match(u):
            awys.append(u)
        elif _APT_TOK_RE.match(u):
            apts.append(u)
        elif re.search(r"[A-Z]", u) and 2 <= len(u) <= 5:
            wps.append(u)
    return wps, awys, apts


def _is_cr(waypoints, raw):
    """
    CR (coded route) = a FORCED path. Only emit one when the NOTAM fully
    specifies the routing:
      - a geospatial coordinate fix  (122030N1083917E), or
      - an explicit DCT chain        (LKH DCT ... DCT KARAN)
    A 'wpt - AIRWAY - wpt' form (N892 - MIMUX - N500) is NOT a CR: the NOTAM
    never states the intermediate fixes, so the optimizer picks the route.
    """
    if any(_COORD_RE.match(w) for w in waypoints):
        return True
    if re.search(r"\bDCT\b", raw, re.I):
        return True
    return False


def _infer_segment(legs, airports=()):
    """
    If a CR is given but the NOTAM never states which segment is closed, the
    closed segment is the CR's own endpoints: first and last NAMED fix.

        LKH - 122030N1083917E - KARAN   ->  closed segment = LKH -> KARAN

    Coordinate fixes are the detour itself, so they can never be endpoints.
    Airports (VVTS/VVTH) are trimmed too -- they are the city pair, not the
    closed airway segment.
    """
    skip = {a.upper() for a in airports}
    named = [l for l in legs if not _COORD_RE.match(l) and l.upper() not in skip]
    if len(named) >= 2:
        return (named[0], named[-1])
    return None


def _reconcile(name, legs):
    """Fix source typos: POINK (header) -> PONIK (as spelled in the ALTN RTE line)."""
    if name in legs:
        return name, False
    cand = [l for l in legs if not _COORD_RE.match(l)]
    close = difflib.get_close_matches(name, cand, n=1, cutoff=0.75)
    return (close[0], True) if close else (name, False)


def extract_reroutes(e_text):
    """
    Returns list of reroute dicts.
    Styles: A Damascus OVF | B ALTN RTE: | C numbered 'X TO Y:' |
            D standalone dash | E VOR adjust | F numbered + ALTN RTE |
            G '+' bullet route | H 'VIA ... : CHANGE TO ...'

    Each dict carries:
      "cr"                -> True = forced route, plot it
                             False = leave to the optimizer, do not force
      "closed_segment"    -> (from, to) of the closed airway segment
      "segment_inferred"  -> True when that segment came from the CR endpoints
                             rather than from explicit NOTAM wording
    """
    results = []
    lines = [l.strip() for l in e_text.splitlines() if l.strip()]
    unwrapped = _unwrap(e_text)
    joined = " ".join(lines)

    # ---------- Style A: Damascus dots (MUST contain OVF) ----------
    for line in lines:
        m = re.match(r"\.?\s*([A-Z0-9\s\-']+?)\s+AVBL\s+FOR\s+(EB|WB)\s+OVF", line, re.I)
        if m:
            wps = _only_waypoints(_tokenize(m.group(1)))
            if len(wps) >= 2:
                results.append({
                    "raw": _clean(line), "tokens": wps, "waypoints": wps,
                    "bidirectional": False, "direction_hint": m.group(2).upper(),
                    "closed_airway": None, "closed_segment": None,
                    "from_airports": [], "to_airports": [],
                    "cr": _is_cr(wps, line),
                })

    # ---------- Style B: ALTN RTE (look BACK for closure) ----------
    for m in re.finditer(r"ALTN\s+RTE\s*:\s*([A-Z0-9\-\s\.']+?)(?=\.\s|\(|$)", joined, re.I):
        alt = m.group(1)
        wps = _only_waypoints(_tokenize(alt))
        if len(wps) < 2:
            continue
        before = joined[:m.start()]
        closed_awy, seg = None, None
        for cm in re.finditer(
            r"([A-Z]\d{1,4})\s+NOT\s+AVBL(?:\s+BTN\s+([A-Z]{2,5})\s+AND\s+([A-Z]{2,5}))?",
            before, re.I,
        ):
            closed_awy = cm.group(1)
            seg = (cm.group(2), cm.group(3)) if cm.group(2) and cm.group(3) else None
        window = joined[m.start(): m.end() + 40]
        results.append({
            "raw": _clean(alt), "tokens": wps, "waypoints": wps,
            "bidirectional": bool(BIDIR_RE.search(window)), "direction_hint": None,
            "closed_airway": closed_awy, "closed_segment": seg,
            "from_airports": [], "to_airports": [],
            "cr": _is_cr(wps, alt),
        })

    # ---------- Style F: numbered airway header + "ALTN RTE :" line ----------
    pending = None
    for line in lines:
        h = _ITEM_HDR_RE.match(line.upper())
        if h:
            pending = {"airway": h.group(1), "from": h.group(2), "to": h.group(3)}
            continue
        am = _ALTN_LINE_RE.search(line)
        if am and pending:
            legs = _only_waypoints(_tokenize(am.group(1)))
            if len(legs) >= 2:
                a, t1 = _reconcile(pending["from"], legs)
                b, t2 = _reconcile(pending["to"], legs)
                results.append({
                    "raw": _clean(line), "tokens": legs, "waypoints": legs,
                    "bidirectional": False, "direction_hint": None,
                    "closed_airway": pending["airway"], "closed_segment": (a, b),
                    "from_airports": [], "to_airports": [],
                    "typo_corrected": t1 or t2,
                    "cr": _is_cr(legs, am.group(1)),
                })
            pending = None

    # ---------- Styles G/H: bullet route + VIA/CHANGE TO ----------
    cur_awy = None
    for line in unwrapped:
        h = _ON_HDR_RE.match(line)
        if h:
            cur_awy = h.group(1).upper()
            continue

        # Style H: "... VIA <old> : CHANGE TO <new> [AND VICE VERSA]"
        c = _CHANGE_RE.search(line)
        if c:
            _ctx, old, new = c.groups()
            bidir = bool(re.search(r"VICE\s+VERSA", new, re.I))
            new_txt = re.sub(r"\bAND\s+VICE\s+VERSA\b", "", new, flags=re.I)
            w, a, ap = _split_tokens(new_txt)
            _ow, oa, _oap = _split_tokens(old)
            if w:
                results.append({
                    "raw": _clean(line), "tokens": w, "waypoints": w,
                    "bidirectional": bidir, "direction_hint": None,
                    "closed_airway": oa[0] if oa else None,
                    "closed_segment": None,          # filled by the pass below
                    "from_airports": ap[:1], "to_airports": ap[1:2],
                    "new_airways": a,
                    "cr": _is_cr(w, new_txt),
                })
            continue

        # Style G: "+ L642 - KARAN - 122030N1083917E - PTH"
        if line.startswith("+"):
            body = line[1:].strip()
            if re.search(r"SHALL\s+NOT\s+DEVIATE", body, re.I):
                continue
            w, a, ap = _split_tokens(body)
            if len(w) >= 2:
                results.append({
                    "raw": _clean(line), "tokens": w, "waypoints": w,
                    "bidirectional": False, "direction_hint": None,
                    "closed_airway": a[0] if a else cur_awy,
                    "closed_segment": None,          # filled by the pass below
                    "from_airports": [], "to_airports": [],
                    "cr": _is_cr(w, body),
                })

    # ---------- Style C: numbered China "N. X TO Y: route" ----------
    for m in re.finditer(
        r"\d+\.\s*([A-Z0-9\s\-]+?)\s+TO\s+([A-Z0-9\s\-]+?):\s*([A-Z0-9\-\s]+?)(?=\.|$)",
        joined,
    ):
        from_ctx, to_ctx = m.group(1).strip(), m.group(2).strip()
        alt = re.sub(r"\bAND\b|\bBEYOND\b", "", m.group(3), flags=re.I)
        wps = _only_waypoints(_tokenize(alt))
        if len(wps) >= 2:
            results.append({
                "raw": f"{from_ctx} TO {to_ctx} :: {_clean(m.group(3))}",
                "tokens": wps, "waypoints": wps,
                "bidirectional": False, "direction_hint": None,
                "closed_airway": None, "closed_segment": None,
                "from_airports": re.findall(r"\b([A-Z]{4})\b", from_ctx),
                "to_airports": re.findall(r"\b([A-Z]{4})\b", to_ctx),
                "cr": _is_cr(wps, alt),
            })

    # ---------- Style E: VOR adjust ----------
    for vl in re.findall(
        r"ADJUST\s+TO\s+([^\n\.]+?)(?:,\s*AND\s+VICE\s+VERSA|\.|$)", joined, re.I
    ):
        quoted = re.findall(r"'([A-Z0-9]{2,5})'|\"([A-Z0-9]{2,5})\"", vl)
        quoted = [a or b for a, b in quoted]
        wps = [w for w in quoted if _is_waypoint(w)] if quoted \
            else _only_waypoints(_tokenize(vl))
        if len(wps) >= 2:
            results.append({
                "raw": _clean(vl), "tokens": wps, "waypoints": wps,
                "bidirectional": bool(re.search(r"VICE\s+VERSA", vl, re.I)),
                "direction_hint": None, "closed_airway": None, "closed_segment": None,
                "from_airports": [], "to_airports": [],
                "cr": _is_cr(wps, vl),
            })

    # ---------- Style D: standalone dash route ----------
    for line in lines:
        if re.match(r"^[A-Z0-9]{2,5}(-[A-Z0-9]+){2,}\.?\s*(\(BIDIRECTIONAL\))?\s*$",
                    line, re.I):
            wps = _only_waypoints(_tokenize(line))
            if len(wps) >= 2:
                results.append({
                    "raw": _clean(line), "tokens": wps, "waypoints": wps,
                    "bidirectional": bool(BIDIR_RE.search(line)),
                    "direction_hint": None, "closed_airway": None,
                    "closed_segment": None, "from_airports": [], "to_airports": [],
                    "cr": _is_cr(wps, line),
                })

    # ---------- Ensure keys exist ----------
    for r in results:
        r.setdefault("from_airports", [])
        r.setdefault("to_airports", [])
        r.setdefault("typo_corrected", False)
        r.setdefault("new_airways", [])
        r.setdefault("cr", False)
        r.setdefault("closed_segment", None)

    # ---------- UNIVERSAL: derive closed segment from the CR itself ----------
    # A CR is a forced path, so its own endpoints ARE the closed segment.
    # Applies to every style: if no segment was stated explicitly, infer it.
    for r in results:
        if r["closed_segment"] is None and r["cr"]:
            seg = _infer_segment(
                r["waypoints"],
                airports=list(r["from_airports"]) + list(r["to_airports"]),
            )
            r["closed_segment"] = seg
            r["segment_inferred"] = seg is not None
        else:
            r.setdefault("segment_inferred", False)

    # ---------- Drop closure-less duplicates ----------
    closed_sigs = {
        (tuple(r["waypoints"]), r["bidirectional"])
        for r in results if r["closed_airway"]
    }
    filtered = [
        r for r in results
        if not (r["closed_airway"] is None
                and (tuple(r["waypoints"]), r["bidirectional"]) in closed_sigs)
    ]

    # ---------- Final dedupe ----------
    seen, out = set(), []
    for r in filtered:
        key = (tuple(r["waypoints"]), r["bidirectional"], r["closed_airway"],
               tuple(r["from_airports"]), tuple(r["to_airports"]))
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out