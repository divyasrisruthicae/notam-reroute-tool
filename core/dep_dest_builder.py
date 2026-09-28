from .side_classifier import classify_firs_directional


def _reference_path(reroute, wp_res, with_coord):
    """
    The search slices hang off the CLOSED SEGMENT, not the detour:
      1. closed airway + segment  -> real airway fixes a..b (waypoints.csv)
      2. closed segment only      -> straight line a -> b
      3. nothing known            -> the CR itself (old behaviour)
    Always oriented the same way as the CR (FIRST -> LAST).
    Returns (names, coords, source).
    """
    cr_names = [r["waypoint"] for r in with_coord]
    cr_coords = [r["coord"] for r in with_coord]

    seg = reroute.get("closed_segment")
    if not seg or not seg[0] or not seg[1]:
        return cr_names, cr_coords, "cr"

    a, b = seg[0].upper(), seg[1].upper()
    if a in cr_names and b in cr_names:
        if cr_names.index(a) > cr_names.index(b):
            a, b = b, a
    elif a == cr_names[-1] or b == cr_names[0]:
        a, b = b, a

    awy = reroute.get("closed_airway")
    if awy:
        path = wp_res.airway_path(awy, a, b)
        if path and len(path[0]) >= 2:
            return path[0], path[1], "airway"

    lookup = dict(zip(cr_names, cr_coords))
    ca = lookup.get(a) or wp_res.coord(a, ref_coord=cr_coords[0])
    cb = lookup.get(b) or wp_res.coord(b, ref_coord=cr_coords[-1])
    if ca and cb and ca != cb:
        return [a, b], [ca, cb], "segment"

    return cr_names, cr_coords, "cr"


def build_dep_dest(reroute, wp_res, fir_res, pfx_res,
                   source_fir=None,
                   anchor_coord=None,
                   max_distance_km=6000,
                   min_distance_km=0,
                   corridor_deg=45):
    """
    anchor_coord : (lat, lon) from NOTAM Q-line, used to disambiguate
                   duplicate waypoint idents (e.g. BBS Algeria vs India).
    """
    wps = reroute["waypoints"]

    resolved_raw = wp_res.resolve_route(wps, anchor_coord=anchor_coord)

    # Attach FIR/prefix info to each resolved waypoint
    resolved = []
    for r in resolved_raw:
        if r["coord"] is None:
            resolved.append({
                "waypoint": r["waypoint"], "coord": None, "prefix": None,
                "fir": None, "source": "unresolved",
                "ambiguous": r.get("ambiguous", False),
            })
            continue
        ndic = r["ndic"]
        if ndic and len(ndic) == 2:
            resolved.append({
                "waypoint": r["waypoint"], "coord": r["coord"], "prefix": ndic,
                "fir": None, "source": "ndic",
                "ambiguous": r.get("ambiguous", False),
            })
        else:
            lat, lon = r["coord"]
            fir, _ = fir_res.fir_for_point(lat, lon)
            pfx = pfx_res.prefix_for_fir(fir) if fir else None
            resolved.append({
                "waypoint": r["waypoint"], "coord": r["coord"], "prefix": pfx,
                "fir": fir, "source": "polygon" if pfx else "unknown",
                "ambiguous": r.get("ambiguous", False),
            })

    missing = [r["waypoint"] for r in resolved if r["coord"] is None]
    with_coord = [r for r in resolved if r["coord"]]

    if len(with_coord) < 2:
        return {"error": "not enough resolved waypoints",
                "missing_waypoints": missing,
                "resolved": resolved,
                "records": []}

    ref_names, ref_coords, ref_source = _reference_path(reroute, wp_res, with_coord)

    exclude_prefix = None
    if source_fir:
        exclude_prefix = pfx_res.prefix_for_fir(source_fir)

    classification = classify_firs_directional(
        fir_res, pfx_res, ref_coords,
        max_distance_km=max_distance_km,
        min_distance_km=min_distance_km,
        corridor_deg=corridor_deg,
        exclude_prefix=exclude_prefix,
    )

    from_airports = reroute.get("from_airports", [])
    to_airports   = reroute.get("to_airports", [])

    beyond_first_out = list(classification["prefixes_first"]) + from_airports
    beyond_last_out  = list(classification["prefixes_last"])  + to_airports

    records = []
    if reroute["bidirectional"]:
        records.append({"direction": "FORWARD",
                        "dep": beyond_first_out, "dest": beyond_last_out})
        records.append({"direction": "REVERSE",
                        "dep": beyond_last_out,  "dest": beyond_first_out})
    else:
        records.append({"direction": reroute["direction_hint"] or "ONEWAY",
                        "dep": beyond_first_out, "dest": beyond_last_out})

    ambiguous = [r["waypoint"] for r in resolved if r.get("ambiguous")]

    return {
        "route_string": "-".join(reroute["tokens"]),
        "waypoints": wps,
        "resolved": resolved,
        "missing_waypoints": missing,
        "ambiguous_waypoints": ambiguous,
        "first_wpt": ref_names[0],
        "last_wpt":  ref_names[-1],
        "reference_path": list(zip(ref_names, ref_coords)),
        "reference_source": ref_source,
        "classification": classification,
        "from_airports": from_airports,
        "to_airports": to_airports,
        "records": records,
        "bidirectional": reroute["bidirectional"],
        "closed_airway": reroute["closed_airway"],
        "closed_segment": reroute["closed_segment"],
        "source_fir": source_fir,
        "excluded_prefix": exclude_prefix,
    }