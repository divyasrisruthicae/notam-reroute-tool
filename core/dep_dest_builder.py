from .side_classifier import classify_firs_directional


def build_dep_dest(reroute, wp_res, fir_res, pfx_res,
                   source_fir=None,
                   anchor_coord=None,
                   max_distance_km=6000,
                   min_distance_km=0,
                   corridor_deg=90):
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

    coords_seq = [r["coord"] for r in with_coord]

    exclude_prefix = None
    if source_fir:
        exclude_prefix = pfx_res.prefix_for_fir(source_fir)

    classification = classify_firs_directional(
        fir_res, pfx_res, coords_seq,
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
        "first_wpt": with_coord[0]["waypoint"],
        "last_wpt":  with_coord[-1]["waypoint"],
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