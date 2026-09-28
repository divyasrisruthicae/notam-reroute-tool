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
    # Save the names and coordinates from the original route.
    # This is also the final fallback if no closed segment can be found.
    cr_names = [r["waypoint"] for r in with_coord]
    cr_coords = [r["coord"] for r in with_coord]

    # Read the two waypoint names that mark the closed part of the route.
    seg = reroute.get("closed_segment")
    # If the closed segment is missing or incomplete, use the full original route.
    if not seg or not seg[0] or not seg[1]:
        return cr_names, cr_coords, "cr"

    # Convert both segment names to uppercase so they match stored waypoint names.
    a, b = seg[0].upper(), seg[1].upper()
    # If both endpoints are on the route, make sure they follow the route's order.
    if a in cr_names and b in cr_names:
        if cr_names.index(a) > cr_names.index(b):
            a, b = b, a
    # If only the ends of the route help identify the direction, reverse the segment.
    elif a == cr_names[-1] or b == cr_names[0]:
        a, b = b, a

    # Try to find the exact waypoint path on the closed airway first.
    awy = reroute.get("closed_airway")
    if awy:
        # Use the real airway path when the closed airway is known.
        path = wp_res.airway_path(awy, a, b)
        # A valid airway path must contain at least two waypoints.
        if path and len(path[0]) >= 2:
            return path[0], path[1], "airway"

    # If the airway path is unavailable, use the closed segment as a straight line.
    # Build a quick lookup table from waypoint name to its coordinates.
    lookup = dict(zip(cr_names, cr_coords))
    # Use known route coordinates first; otherwise ask the waypoint resolver.
    ca = lookup.get(a) or wp_res.coord(a, ref_coord=cr_coords[0])
    cb = lookup.get(b) or wp_res.coord(b, ref_coord=cr_coords[-1])
    # Only use this segment when both endpoints were found and are different.
    if ca and cb and ca != cb:
        return [a, b], [ca, cb], "segment"

    # If none of the more precise options worked, keep using the original route.
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
    # Get the waypoint names from the parsed reroute information.
    wps = reroute["waypoints"]

    # Find coordinates for each waypoint, using the NOTAM location when available.
    resolved_raw = wp_res.resolve_route(wps, anchor_coord=anchor_coord)

    # Add FIR and airport-prefix information to each waypoint.
    resolved = []
    # Process every waypoint returned by the resolver one at a time.
    for r in resolved_raw:
        # A missing coordinate means this waypoint could not be located.
        if r["coord"] is None:
            # Keep track of waypoint names that could not be located.
            resolved.append({
                "waypoint": r["waypoint"], "coord": None, "prefix": None,
                "fir": None, "source": "unresolved",
                "ambiguous": r.get("ambiguous", False),
            })
            continue

        # Some waypoint records already contain a two-letter prefix.
        ndic = r["ndic"]
        if ndic and len(ndic) == 2:
            # Use the prefix already provided in the waypoint data.
            resolved.append({
                "waypoint": r["waypoint"], "coord": r["coord"], "prefix": ndic,
                "fir": None, "source": "ndic",
                "ambiguous": r.get("ambiguous", False),
            })
        else:
            # Otherwise, find the FIR from the waypoint's coordinates.
            lat, lon = r["coord"]
            # Check which FIR polygon contains this waypoint.
            fir, _ = fir_res.fir_for_point(lat, lon)
            # Convert the FIR code into the prefix used by the output.
            pfx = pfx_res.prefix_for_fir(fir) if fir else None
            resolved.append({
                "waypoint": r["waypoint"], "coord": r["coord"], "prefix": pfx,
                "fir": fir, "source": "polygon" if pfx else "unknown",
                "ambiguous": r.get("ambiguous", False),
            })

    # Separate missing waypoints from those that have coordinates.
    missing = [r["waypoint"] for r in resolved if r["coord"] is None]
    with_coord = [r for r in resolved if r["coord"]]

    # A route needs at least two located waypoints to define a direction.
    if len(with_coord) < 2:
        return {"error": "not enough resolved waypoints",
                "missing_waypoints": missing,
                "resolved": resolved,
                "records": []}

    # Choose the route path used to decide which FIRs are on each side.
    ref_names, ref_coords, ref_source = _reference_path(reroute, wp_res, with_coord)

    exclude_prefix = None
    if source_fir:
        # Do not include the FIR where the NOTAM originated in the results.
        exclude_prefix = pfx_res.prefix_for_fir(source_fir)

    # Find the FIR prefixes before and after the closed route section.
    classification = classify_firs_directional(
        fir_res, pfx_res, ref_coords,
        max_distance_km=max_distance_km,
        min_distance_km=min_distance_km,
        corridor_deg=corridor_deg,
        exclude_prefix=exclude_prefix,
    )

    from_airports = reroute.get("from_airports", [])
    to_airports   = reroute.get("to_airports", [])

    # Add the listed departure and destination airports to the FIR results.
    # These lists represent traffic entering before the first waypoint and
    # leaving after the last waypoint.
    beyond_first_out = list(classification["prefixes_first"]) + from_airports
    beyond_last_out  = list(classification["prefixes_last"])  + to_airports

    # Create one record for each direction the reroute can be flown.
    records = []
    if reroute["bidirectional"]:
        # A bidirectional route needs both the normal and reversed directions.
        records.append({"direction": "FORWARD",
                        "dep": beyond_first_out, "dest": beyond_last_out})
        records.append({"direction": "REVERSE",
                        "dep": beyond_last_out,  "dest": beyond_first_out})
    else:
        # A one-way route only needs the direction provided by the parser.
        records.append({"direction": reroute["direction_hint"] or "ONEWAY",
                        "dep": beyond_first_out, "dest": beyond_last_out})

    # Keep the names of waypoints whose location was uncertain or duplicated.
    ambiguous = [r["waypoint"] for r in resolved if r.get("ambiguous")]

    # Return all details needed by the app, including the final departure/destination records.
    return {
        # Rebuild the route as a readable string for display.
        "route_string": "-".join(reroute["tokens"]),
        # Keep the original waypoint names as they appeared in the reroute.
        "waypoints": wps,
        # Include each waypoint's coordinates, FIR, prefix, and lookup status.
        "resolved": resolved,
        # List waypoint names that could not be found.
        "missing_waypoints": missing,
        # List waypoint names that had more than one possible location.
        "ambiguous_waypoints": ambiguous,
        # Identify the first and last points of the path used for classification.
        "first_wpt": ref_names[0],
        "last_wpt":  ref_names[-1],
        # Keep the selected path as pairs of waypoint names and coordinates.
        "reference_path": list(zip(ref_names, ref_coords)),
        # Explain whether the path came from the airway, segment, or original route.
        "reference_source": ref_source,
        # Include the FIR classification details for callers that need them.
        "classification": classification,
        # Preserve the airport lists from the parsed reroute.
        "from_airports": from_airports,
        "to_airports": to_airports,
        # Store the final records used for departure and destination results.
        "records": records,
        # Preserve route direction and closed-route information for display/debugging.
        "bidirectional": reroute["bidirectional"],
        "closed_airway": reroute["closed_airway"],
        "closed_segment": reroute["closed_segment"],
        "source_fir": source_fir,
        "excluded_prefix": exclude_prefix,
    }