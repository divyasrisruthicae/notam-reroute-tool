import re


def extract_notam_id(text):
    m = re.match(r"\s*([A-Z]\d{3,4}/\d{2})\b", text)
    return m.group(1) if m else None


def extract_source_fir(text):
    m = re.search(r"Q\)\s*([A-Z]{4})", text)
    return m.group(1) if m else None

def extract_a_location(text):
    # NEW. First 4-letter code after "A)"
    # e.g. "A)OEJN B)2609..." -> "OEJN"
    # Used as the departure airport for "DEP TFC TO XXXX" NOTAMs.
    m = re.search(r"\bA\)\s*([A-Z]{4})\b", text)
    return m.group(1) if m else None

def extract_q_coordinate(text):
    """
    Q)VECF/QWELW/IV/BO/W/000/999/2016N08748E110
                                  ^^^^^^^^^^^^^ lat/lon/radius
    Returns (lat, lon) in decimal degrees or None.
    """
    m = re.search(r"Q\)[^\n]*?(\d{4})([NS])(\d{5})([EW])", text)
    if not m:
        return None
    lat_raw, ns, lon_raw, ew = m.groups()
    lat = int(lat_raw[:2]) + int(lat_raw[2:]) / 60.0
    lon = int(lon_raw[:3]) + int(lon_raw[3:]) / 60.0
    if ns == "S": lat = -lat
    if ew == "W": lon = -lon
    return (lat, lon)


def extract_e_section(text):
    m = re.search(r"E\)(.*?)(?=\n\s*F\)|\Z)", text, re.S)
    return m.group(1).strip() if m else text


def extract_explicit_airports(text, first_wpt, last_wpt):
    """
    Detects 4-letter ICAO airports mentioned in the NOTAM E) section
    and figures out which side they belong to based on context:
        'TO ZGKL'    -> destination side (side_last)
        'ZGKL TO'    -> departure  side (side_first)
        'FROM ZGKL'  -> departure  side
    Returns: {"first_side": [...], "last_side": [...]}
    """
    first_side, last_side = set(), set()
    exclude = {first_wpt.upper(), last_wpt.upper()}

    for m in re.finditer(r"\bTO\s+([A-Z]{4})\b", text):
        if m.group(1) not in exclude:
            last_side.add(m.group(1))

    for m in re.finditer(r"\b([A-Z]{4})\s+TO\b", text):
        if m.group(1) not in exclude:
            first_side.add(m.group(1))

    for m in re.finditer(r"\bFROM\s+([A-Z]{4})\b", text):
        if m.group(1) not in exclude:
            first_side.add(m.group(1))

    return {"first_side": sorted(first_side), "last_side": sorted(last_side)}