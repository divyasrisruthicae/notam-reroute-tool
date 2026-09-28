"""
route_extractor.py
------------------
Reads the NOTAM E) text and pulls out every reroute in it.

NOTAMs are written in many different styles, so we have one small
"catcher" per style (A to I). Each catcher returns the same kind of dict.

Key idea - CR (coded route):
  cr = True   -> the NOTAM gives a FIXED path (coordinate fix or DCT chain)
                 -> shown as "Coded route (CR)"
  cr = False  -> it's just "wpt - AIRWAY - wpt", the NOTAM doesn't list the
                 fixes in between -> shown as "Optimizer-resolved"
  CHANGED: "cr" is now only a LABEL. Both kinds get Dep/Dest airports,
  the analyst decides which ones to keep.
"""

import re
import difflib

# Word "BIDIRECTIONAL" anywhere
BIDIR_RE = re.compile(r"BIDIRECTIONAL", re.I)

# Raw lat/long fix: 3105N12452E or 122030N1083917E
_COORD_RE = re.compile(r"^(\d{2})(\d{2})(\d{2})?([NS])(\d{3})(\d{2})(\d{2})?([EW])$")

# Coordinate written with the letter FIRST (China style):
#   N253957E1095707   or   N275120 E1105724   (space in the middle)
# The look-arounds stop it matching inside a longer word.
_PREFIX_COORD_RE = re.compile(
    r"(?<![A-Z0-9])([NS])(\d{4}|\d{6})\s?([EW])(\d{5}|\d{7})(?![A-Z0-9])"
)

# A named fix with its coordinate in brackets:
#   CG1(253957N1095707E)  /  CG3 (275120N1105724E)
# (runs AFTER the line above, so the coord is already letter-last here)
_NAMED_COORD_RE = re.compile(
    r"\b[A-Z0-9]{2,5}\s*\(\s*(\d{4}(?:\d{2})?[NS]\d{5}(?:\d{2})?[EW])\s*\)"
)

# "CHANGDE NDB 'CD'" / "LAIBIN VOR 'LBN'" -> we only want the ID ('CD', 'LBN')
_NAVAID_RE = re.compile(
    r"\b[A-Z]+\s+(?:DVOR/DME|VOR/DME|DVOR|VOR|NDB)\s*['\"]([A-Z0-9]{2,5})['\"]"
)

# Navaid type written AFTER the ID with no quotes:
#   "IKA DVOR/DME"  -> "IKA"      "HAM VOR" -> "HAM"
_NAVAID_SUFFIX_RE = re.compile(
    r"\b([A-Z]{2,3})\s+(?:DVOR/DME|VOR/DME|DVOR|VOR|NDB)\b(?!\s*['\"])"
)

# End of an "ADJUST TO" route that is NOT part of the route itself:
#   "... TO BEYOND ABTUD"  /  "... FOR BEYOND LBN"  /  "... FOR ARR ZGGG"
_ROUTE_TAIL_RE = re.compile(r"\b(?:FOR|TO)\s+(?:BEYOND|ARR|DEP)\b.*$")

# Closure sentence, e.g.
#   "AWY G208/L125 BTN RADAL AND IKA CLSD"
#   "AIRWAY N39 BTN DEMBA AND OBRIX CLSD"
#   "ATS ROUTE A1 SEGMENT BTN X AND Y"
# group 1 = airway(s)  (can be several joined by "/")
# group 2 = from fix,  group 3 = to fix
_CLOSURE_RE = re.compile(
    r"\b(?:AWY|AIRWAY|ATS\s+ROUTE)S?\s+"
    r"([A-Z]{1,2}\d{1,4}[A-Z]?(?:\s*/\s*[A-Z]{1,2}\d{1,4}[A-Z]?)*)\s+"
    r"(?:SEGMENT\s+)?BTN\s+([A-Z0-9]{2,15})\s+AND\s+([A-Z0-9]{2,15})"
)

# A route chain anywhere in the text:
#   "ROVAD DCT IKA"
#   "ULDUS DCT ALKUP DCT IMLIM DCT OXADU M715 OBRIX"
#   "EGREP B417 PATOR B417 BDB"   (airway-only - now caught too)
# = waypoint, then one or more (DCT or AIRWAY) + waypoint
_CHAIN_WP = r"[A-Z0-9]{2,15}"
_CHAIN_LINK = r"(?:DCT|[A-Z]{1,2}\d{1,4}[A-Z]?)"
_CHAIN_RE = re.compile(
    rf"(?<![A-Z0-9])({_CHAIN_WP}(?:\s+{_CHAIN_LINK}\s+{_CHAIN_WP})+)(?![A-Z0-9])"
)

# Direction words in front of a route ("FOR WESTBOUND:", "EB")
_DIR_WORD_RE = re.compile(r"\b(WEST|EAST|NORTH|SOUTH)BOUND\b|\b(WB|EB|NB|SB)\b")

# NEW: aerodrome traffic sentences (Saudi style)
#   "INBD TFC TO OEJN AD ..."   -> traffic ARRIVING at OEJN
#   "DEP TFC TO OEDF AD ..."    -> traffic DEPARTING (from the A) airport) to OEDF
_AD_ARR_RE = re.compile(
    r"\b(?:INBD|INBOUND|ARRIVING|ARR)\s+(?:TFC|TRAFFIC|ACFT|FLT|FLIGHTS?)\s+"
    r"(?:TO|FOR|INTO)\s+([A-Z]{4})\b"
)
_AD_DEP_RE = re.compile(
    r"\b(?:DEP|DEPARTING|OUTBD|OUTBOUND)\s+(?:TFC|TRAFFIC|ACFT|FLT|FLIGHTS?)\s+"
    r"(?:TO|FOR)\s+([A-Z]{4})\b"
)

# Airway name: 1-2 letters + 1-4 digits + optional letter (L642, UL888, W15)
_AWY_TOK_RE = re.compile(r"^[A-Z]{1,2}\d{1,4}[A-Z]?$")

# Airport: exactly 4 letters (VVTS)
_APT_TOK_RE = re.compile(r"^[A-Z]{4}$")

# Numbered airway header:
#   "1. Y711 POINK - MUGUS"  /  "1.L642 KARAN-PTH"  /  "3.W15 LKH-KARAN"
_ITEM_HDR_RE = re.compile(
    r"^\s*\d{1,2}\s*[\.\)]\s*([A-Z]{1,2}\d{1,4}[A-Z]?)\s+"
    r"([A-Z0-9]{2,6})\s*[-\u2013\u2014]\s*([A-Z0-9]{2,6})\s*$"
)

# "ALTN RTE : PONIK DCT 3105N12452E DCT MUGUS"
# The colon is REQUIRED, so "ALTN RTE ESTABLISHED DUE TO..." never matches.
_ALTN_LINE_RE = re.compile(r"ALT[N]?\s*(?:RTE|ROUTE)\s*:\s*(.+)$", re.I)

# "- ACFT ON L642:"  -> tells us which airway the next bullets are about
_ON_HDR_RE = re.compile(r"^-\s*ACFT\s+ON\s+([A-Z]{1,2}\d{1,4}[A-Z]?)\s*:?\s*$", re.I)

# "- ACFT DEP/ARR VVDL VIA W15 - Q15: CHANGE TO VVDL - W7 (BMT) - ..."
_CHANGE_RE = re.compile(r"^-\s*(.+?)\bVIA\b\s*(.+?)\s*:\s*CHANGE\s+TO\s+(.+)$", re.I)

# Words that are NEVER waypoints (so we throw them away)
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
    # word that can sit in front of a route
    "ROUTE",
    # Iran-style words that can end up next to a DCT chain
    "FPL", "FILE", "TOS", "MNM", "LVL", "FLT", "DVOR", "DME", "AWY", "AIRWAY",
        # NEW: Saudi-style aerodrome words
    "INBD", "OUTBD", "AD", "TRAFFIC",
}


def _normalize_coords(text):
    """
    Rewrites every coordinate into the ONE format the rest of the
    tool already understands (letter LAST, no spaces):

        N253957E1095707        -> 253957N1095707E
        N275120 E1105724       -> 275120N1105724E
        CG1(N253957E1095707)   -> 253957N1095707E   (name dropped, coord kept)

    Because it runs once at the start, every style and
    waypoint_resolver.py get the fix for free - nothing else changes.
    """
    # Step 1: move N/S and E/W to the end, remove the space
    text = _PREFIX_COORD_RE.sub(
        lambda m: f"{m.group(2)}{m.group(1)}{m.group(4)}{m.group(3)}", text
    )
    # Step 2: "CG1(coord)" -> "coord"
    text = _NAMED_COORD_RE.sub(r"\1", text)
    return text


def _find_closures(text):
    """
    Finds every "AWY X BTN A AND B" closure sentence in the text.
    Returns a list of (airway, (A, B)).
      "AWY G208/L125 BTN RADAL AND IKA CLSD" -> ("G208/L125", ("RADAL", "IKA"))
    Navaid words (DVOR/DME...) must already be removed from the text.
    """
    out = []
    for m in _CLOSURE_RE.finditer(text):
        awy = re.sub(r"\s+", "", m.group(1))         # "G208 / L125" -> "G208/L125"
        out.append((awy, (m.group(2), m.group(3))))
    return out


def _pick_closure(closures, waypoints):
    """
    Which closure belongs to this route?
      1. one whose end fix (A or B) is IN the route  -> that one
      2. otherwise, if the NOTAM has only ONE closure -> that one
      3. otherwise we don't guess                     -> (None, None)
    """
    wset = set(waypoints)
    for awy, seg in closures:
        if seg[0] in wset or seg[1] in wset:
            return awy, seg
    if len(closures) == 1:
        return closures[0]
    return None, None

def _find_ad_flow(text):
    """
    NEW. Is this NOTAM about traffic to/from ONE aerodrome?
      "INBD TFC TO OEJN AD" -> ("ARR", "OEJN")
      "DEP TFC TO OEDF AD"  -> ("DEP", "OEDF")
      nothing found         -> (None, None)
    """
    m = _AD_ARR_RE.search(text)
    if m:
        return "ARR", m.group(1)
    m = _AD_DEP_RE.search(text)
    if m:
        return "DEP", m.group(1)
    return None, None

def _clean(s):
    # Squash extra spaces and trim dots/commas off the ends
    return re.sub(r"\s+", " ", s).strip(" .,")


def _unwrap(e_text):
    """
    NOTAM lines break in the middle of sentences.
    This glues a broken line back onto the line above it.
    It will NOT glue a line that starts a new bullet (- or +),
    a numbered item (1. / 2)), or an ALTN RTE line.
    """
    out = []
    for ln in e_text.splitlines():
        s = ln.strip()
        if not s:
            continue
        if out and not re.match(r"^[-+]|^\d{1,2}\s*[\.\)]|^ALT[N]?\s*(RTE|ROUTE)", s, re.I):
            out[-1] = out[-1].rstrip() + " " + s     # continuation -> glue on
        else:
            out.append(s)                            # new item -> new line
    return out


def _tokenize(route_str):
    """'CEA-G450-JJS-DCT-JRS' or 'TUSYR DCT TANF' -> ordered list of pieces."""
    s = route_str.replace("(BIDIRECTIONAL)", "")
    s = re.sub(r"\s*-\s*", "-", s)          # " - " -> "-"
    s = s.replace(" ", "-")                  # spaces -> "-"
    return [t for t in re.split(r"[-]+", s) if t and t.strip()]


def _is_waypoint(tok):
    """True only if tok is a real fix/VOR name (not airway, not FL, not noise)."""
    t = re.sub(r"[^A-Z0-9]", "", tok.upper())
    if not t:
        return False
    # Raw lat/long fix IS a waypoint (check this BEFORE the number rules below)
    if _COORD_RE.match(t):
        return True
    if t in _NOISE:
        return False
    if re.match(r"^[A-Z]\d{1,4}$", t):      # airway: A412, G450, W41, N895
        return False
    if re.match(r"^F?L?\d{2,4}$", t):       # flight level: FL350, 350
        return False
    if not re.search(r"[A-Z]", t):          # only digits -> not a name
        return False
    return True


def _only_waypoints(tokens):
    # Keep only the waypoint pieces (cleaned to letters/digits)
    return [re.sub(r"[^A-Z0-9]", "", t.upper()) for t in tokens if _is_waypoint(t)]


def _split_tokens(seg):
    """
    Sorts every piece of a route text into 3 buckets:
    (waypoints, airways, airports).  'W7 (BMT)' -> BMT counted as a waypoint.
    """
    seg = re.sub(r"\(SEE[^)]*\)", "", seg, flags=re.I)       # drop "(SEE ...)"
    seg = re.sub(r"\(([A-Z]{2,5})\)", r"- \1", seg)          # "(BMT)" -> "- BMT"
    parts = [p.strip(" .,;:") for p in re.split(r"\s*-\s*|\s+", seg) if p.strip(" .,;:")]
    wps, awys, apts = [], [], []
    for p in parts:
        u = re.sub(r"[^A-Z0-9]", "", p.upper())
        if not u or u in _NOISE:
            continue
        if _COORD_RE.match(u):
            wps.append(u)                       # lat/long fix
        elif _AWY_TOK_RE.match(u):
            awys.append(u)                      # airway
        elif _APT_TOK_RE.match(u):
            apts.append(u)                      # 4-letter airport
        elif re.search(r"[A-Z]", u) and 2 <= len(u) <= 5:
            wps.append(u)                       # normal waypoint / VOR
    return wps, awys, apts


def _is_cr(waypoints, raw):
    """
    Is this a CR (forced path)? Only if the NOTAM spells out the full path:
      - it has a coordinate fix   (122030N1083917E), or
      - it has a DCT chain        (LKH DCT ... DCT KARAN)
    'wpt - AIRWAY - wpt' (N892 - MIMUX - N500) is NOT a CR -> "optimizer-resolved".
    CHANGED: this is only a label now - every reroute still gets Dep/Dest.
    """
    if any(_COORD_RE.match(w) for w in waypoints):
        return True
    if re.search(r"\bDCT\b", raw, re.I):
        return True
    return False


def _infer_segment(legs, airports=()):
    """
    CR given but the NOTAM never says which segment is closed?
    Then the closed segment = the CR's first and last NAMED fix.

        LKH - 122030N1083917E - KARAN   ->  closed segment = LKH -> KARAN

    Coordinate fixes are the detour itself, so they can't be the ends.
    Airports (VVTS/VVTH) are also skipped - they're the city pair,
    not the closed airway segment.
    (Only used for the "Closed:" info box - the search slices come
     from the CR itself, see dep_dest_builder.py.)
    """
    skip = {a.upper() for a in airports}
    named = [l for l in legs if not _COORD_RE.match(l) and l.upper() not in skip]
    if len(named) >= 2:
        return (named[0], named[-1])
    return None


def _reconcile(name, legs):
    """
    Fixes typos in the NOTAM: header says POINK but the route line says PONIK.
    If the name isn't in the route, use the closest-looking one (75%+ similar).
    Returns (name_to_use, was_it_corrected).
    """
    if name in legs:
        return name, False
    cand = [l for l in legs if not _COORD_RE.match(l)]
    close = difflib.get_close_matches(name, cand, n=1, cutoff=0.75)
    return (close[0], True) if close else (name, False)


def extract_reroutes(e_text):
    """
    Returns a list of reroute dicts.
    Styles: A Damascus OVF | B ALTN RTE: | C numbered 'X TO Y:' |
            D standalone dash | E VOR adjust | F numbered + ALTN RTE |
            G '+' bullet route | H 'VIA ... : CHANGE TO ...' |
            I route chain anywhere (Iran style)

    Each dict has:
      "cr"               -> True = coded route, False = optimizer-resolved
                            (label only - both get Dep/Dest)
      "closed_segment"   -> (from, to) of the closed airway part
      "segment_inferred" -> True if we guessed the segment from the CR ends
                            (NOTAM didn't say it directly)
    """
    # Put every coordinate into one format BEFORE any style runs
    e_text = _normalize_coords(e_text)

    results = []
    lines = [l.strip() for l in e_text.splitlines() if l.strip()]   # raw lines
    unwrapped = _unwrap(e_text)                                      # broken lines glued
    joined = " ".join(lines)                                         # all in one line

    # ---------- Style A: Damascus "... AVBL FOR EB/WB OVF" ----------
    for line in lines:
        m = re.match(r"\.?\s*([A-Z0-9\s\-']+?)\s+AVBL\s+FOR\s+(EB|WB)\s+OVF", line, re.I)
        if m:
            wps = _only_waypoints(_tokenize(m.group(1)))
            if len(wps) >= 2:
                results.append({
                    "raw": _clean(line), "tokens": wps, "waypoints": wps,
                    "bidirectional": False, "direction_hint": m.group(2).upper(),  # EB / WB
                    "closed_airway": None, "closed_segment": None,
                    "from_airports": [], "to_airports": [],
                    "cr": _is_cr(wps, line),
                })

    # ---------- Style B: "ALTN RTE: ..." (look BACKWARDS for what's closed) ----------
    for m in re.finditer(r"ALTN\s+RTE\s*:\s*([A-Z0-9\-\s\.']+?)(?=\.\s|\(|$)", joined, re.I):
        alt = m.group(1)
        wps = _only_waypoints(_tokenize(alt))
        if len(wps) < 2:
            continue

        # Text before this ALTN RTE -> find the last "X123 NOT AVBL [BTN A AND B]"
        before = joined[:m.start()]
        closed_awy, seg = None, None
        for cm in re.finditer(
            r"([A-Z]\d{1,4})\s+NOT\s+AVBL(?:\s+BTN\s+([A-Z]{2,5})\s+AND\s+([A-Z]{2,5}))?",
            before, re.I,
        ):
            closed_awy = cm.group(1)
            seg = (cm.group(2), cm.group(3)) if cm.group(2) and cm.group(3) else None

        # Look a little after the route for "BIDIRECTIONAL"
        window = joined[m.start(): m.end() + 40]
        results.append({
            "raw": _clean(alt), "tokens": wps, "waypoints": wps,
            "bidirectional": bool(BIDIR_RE.search(window)), "direction_hint": None,
            "closed_airway": closed_awy, "closed_segment": seg,
            "from_airports": [], "to_airports": [],
            "cr": _is_cr(wps, alt),
        })

    # ---------- Style F: numbered airway header, then "ALTN RTE :" line ----------
    #   1. Y711 POINK - MUGUS
    #   ALTN RTE : PONIK DCT 3105N12452E DCT MUGUS
    pending = None
    for line in lines:
        h = _ITEM_HDR_RE.match(line.upper())
        if h:
            # Remember the header, the ALTN RTE line comes next
            pending = {"airway": h.group(1), "from": h.group(2), "to": h.group(3)}
            continue
        am = _ALTN_LINE_RE.search(line)
        if am and pending:
            legs = _only_waypoints(_tokenize(am.group(1)))
            if len(legs) >= 2:
                # Fix header typos using the spelling in the route line
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

    # ---------- Styles G/H: bullet lists (Vietnam style) ----------
    cur_awy = None
    for line in unwrapped:
        # "- ACFT ON L642:" -> remember airway for the bullets under it
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
            w, a, ap = _split_tokens(new_txt)       # new route pieces
            _ow, oa, _oap = _split_tokens(old)      # old route -> closed airway
            if w:
                results.append({
                    "raw": _clean(line), "tokens": w, "waypoints": w,
                    "bidirectional": bidir, "direction_hint": None,
                    "closed_airway": oa[0] if oa else None,
                    "closed_segment": None,          # filled in by the UNIVERSAL step below
                    "from_airports": ap[:1], "to_airports": ap[1:2],
                    "new_airways": a,
                    "cr": _is_cr(w, new_txt),
                })
            continue

        # Style G: "+ L642 - KARAN - 122030N1083917E - PTH"
        if line.startswith("+"):
            body = line[1:].strip()
            # "+ ... SHALL NOT DEVIATE ..." is an instruction, not a route
            if re.search(r"SHALL\s+NOT\s+DEVIATE", body, re.I):
                continue
            w, a, ap = _split_tokens(body)
            if len(w) >= 2:
                results.append({
                    "raw": _clean(line), "tokens": w, "waypoints": w,
                    "bidirectional": False, "direction_hint": None,
                    "closed_airway": a[0] if a else cur_awy,
                    "closed_segment": None,          # filled in by the UNIVERSAL step below
                    "from_airports": [], "to_airports": [],
                    "cr": _is_cr(w, body),
                })

    # ---------- Style C: China "N. X TO Y: route" ----------
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
                # 4-letter codes in "X" / "Y" are airports
                "from_airports": re.findall(r"\b([A-Z]{4})\b", from_ctx),
                "to_airports": re.findall(r"\b([A-Z]{4})\b", to_ctx),
                "cr": _is_cr(wps, alt),
            })

    # ---------- Style E: "... SHALL ADJUST TO <route>" ----------
    for vl in re.findall(
        r"ADJUST\s+TO\s+([^\n\.]+?)(?:,\s*AND\s+VICE\s+VERSA|\.|$)", joined, re.I
    ):
        # Swap "NAME VOR 'ID'" for just ID, then read the whole route
        body = _NAVAID_RE.sub(r"\1", vl)                          # SANJIANG VOR 'SJG' -> SJG
        body = re.sub(r"['\"]([A-Z0-9]{2,5})['\"]", r"\1", body)  # leftover 'HAM' -> HAM

        # "FOR ARR ZGGG" -> ZGGG is the destination airport, not a waypoint
        arr_apts = re.findall(r"\bARR\s+([A-Z]{4})\b", body)

        # Cut off "TO BEYOND X" / "FOR BEYOND X" / "FOR ARR X"
        body = _ROUTE_TAIL_RE.sub("", body)

        wps = _only_waypoints(_tokenize(body))
        if len(wps) >= 2:
            results.append({
                "raw": _clean(vl), "tokens": wps, "waypoints": wps,
                "bidirectional": bool(re.search(r"VICE\s+VERSA", vl, re.I)),
                "direction_hint": None, "closed_airway": None, "closed_segment": None,
                "from_airports": [], "to_airports": arr_apts,
                "cr": _is_cr(wps, body),
            })

    # ---------- Style D: a line that is ONLY a dash route "AAA-BBB-CCC" ----------
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

    # ---------- Style I: route chain anywhere (Iran style) ----------
    #   "... SHALL FILE FPL VIA ROVAD DCT IKA DVOR/DME,"
    #   "- ALTN TOS FOR WESTBOUND: RADAL DCT IMKER DCT ULDUS,"
    #   "... VIA EGREP B417 PATOR B417 BDB ..."   (airway-only - now caught too)
    # Runs LAST, and only adds routes the other styles didn't already catch,
    # so it can never change what Styles A-H give.
    txt_i = _NAVAID_SUFFIX_RE.sub(r"\1", joined)         # "IKA DVOR/DME" -> "IKA"
    closures = _find_closures(txt_i)                     # "AWY X BTN A AND B" sentences
    ad_flow, ad_apt = _find_ad_flow(txt_i)               # NEW: "INBD TFC TO OEJN" etc.
    already = {tuple(r["waypoints"]) for r in results}   # routes other styles found
    prev_end = 0
    for m in _CHAIN_RE.finditer(txt_i):
        chain = m.group(1)

        # CHANGED: removed the "must contain a DCT" check.
        # "wpt AWY wpt" chains are caught too now - _is_cr below
        # labels them "optimizer-resolved" so the analyst can decide.

        wps = _only_waypoints(_tokenize(chain))
        if len(wps) < 2 or tuple(wps) in already:
            prev_end = m.end()
            continue

        # Direction word just before this route ("FOR WESTBOUND:" -> WB)
        before = txt_i[prev_end:m.start()]
        dirs = list(_DIR_WORD_RE.finditer(before))
        hint = None
        if dirs:
            d = dirs[-1]
            hint = (d.group(1)[0] + "B") if d.group(1) else d.group(2)

        # "AND VICE VERSA" / "BIDIRECTIONAL" just after the route
        after = txt_i[m.end(): m.end() + 40]
        bidir = bool(re.search(r"VICE\s+VERSA|BIDIRECTIONAL", after))

        # Which closed airway this route replaces
        awy, seg = _pick_closure(closures, wps)

        results.append({
            "raw": _clean(chain), "tokens": wps, "waypoints": wps,
            "bidirectional": bidir, "direction_hint": hint,
            "closed_airway": awy, "closed_segment": seg,
            # NEW: aerodrome NOTAM -> the named airport is the destination.
            # (For DEP, the departure airport = A) line - app.py fills it in.)
            "from_airports": [], "to_airports": [ad_apt] if ad_apt else [],
            "ad_flow": ad_flow,
            # CHANGED: was always True. Now DCT chain = CR,
            # airway-only chain = optimizer-resolved.
            "cr": _is_cr(wps, chain),
        })
        already.add(tuple(wps))
        prev_end = m.end()

    # ---------- Make sure every dict has every key ----------
    for r in results:
        r.setdefault("from_airports", [])
        r.setdefault("to_airports", [])
        r.setdefault("typo_corrected", False)
        r.setdefault("new_airways", [])
        r.setdefault("cr", False)
        r.setdefault("closed_segment", None)
        r.setdefault("ad_flow", None)        # NEW

    # ---------- UNIVERSAL: closed segment from the CR itself ----------
    # For every style: if it's a CR and no segment was given,
    # use the CR's first/last named fix as the closed segment.
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

    # ---------- Remove copies that have no closed airway ----------
    # If the same route was caught twice (once WITH an airway, once WITHOUT),
    # keep only the one with the airway.
    closed_sigs = {
        (tuple(r["waypoints"]), r["bidirectional"])
        for r in results if r["closed_airway"]
    }
    filtered = [
        r for r in results
        if not (r["closed_airway"] is None
                and (tuple(r["waypoints"]), r["bidirectional"]) in closed_sigs)
    ]

    # ---------- Final: remove exact duplicates ----------
    # Airports are not part of the key, so the same route caught by 2 styles
    # (one with "ARR ZGGG", one without) shows only once.
    seen, out = set(), []
    for r in filtered:
        key = (tuple(r["waypoints"]), r["bidirectional"], r["closed_airway"])
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out
