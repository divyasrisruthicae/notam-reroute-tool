"""
dep_dest_builder.py
-------------------
Takes ONE reroute (from route_extractor) and works out:
  - coordinates + country for every waypoint
  - the closed segment we hang the search slices on
  - which countries are on the Dep side and the Dest side
"""
from .side_classifier import classify_firs_directional
from .waypoint_resolver import _haversine_km


def _km(p, q):
    # Distance in km between two (lat, lon) points
    return _haversine_km(p[0], p[1], q[0], q[1])


def _reference_path(reroute, wp_res, with_coord):
    """
    Picks the line the search slices hang off. We want the CLOSED SEGMENT,
    not the detour. Tries in this order:
      1. closed airway + segment known -> real airway fixes a..b (waypoints.csv)
      2. only the segment known        -> straight line a -> b
      3. nothing known                 -> just use the CR itself
    Always kept in the same direction as the CR (FIRST -> LAST).
    Returns (names, coords, source).
    """
    # Names and coords of the CR waypoints we could find
    cr_names = [r["waypoint"] for r in with_coord]
    cr_coords = [r["coord"] for r in with_coord]

    # No closed segment -> fall back to the CR (option 3)
    seg = reroute.get("closed_segment")
    if not seg or not seg[0] or not seg[1]:
        return cr_names, cr_coords, "cr"

    a, b = seg[0].upper(), seg[1].upper()

    # Coordinates of a and b (from the CR if they're in it, otherwise
    # look them up, picking the one nearest the CR)
    lookup = dict(zip(cr_names, cr_coords))
    ca = lookup.get(a) or wp_res.coord(a, ref_coord=cr_coords[0])
    cb = lookup.get(b) or wp_res.coord(b, ref_coord=cr_coords[-1])

    # ---- Make sure a -> b points the same way as the CR ----
    if a in cr_names and b in cr_names:
        # Both in the CR -> use their order in the CR
        if cr_names.index(a) > cr_names.index(b):
            a, b, ca, cb = b, a, cb, ca
    elif a == cr_names[-1] or b == cr_names[0]:
        # One end matches the wrong end of the CR -> swap
        a, b, ca, cb = b, a, cb, ca
    elif a not in cr_names and b not in cr_names and ca and cb:
        # CHANGED: neither end is in the CR (e.g. Iran N39 DEMBA-OBRIX,
        # CR RADAL..ULDUS). Before, we never swapped here, so Dep/Dest
        # could come out flipped. Now: a must be the end CLOSER to the
        # CR's first point.
        keep = _km(ca, cr_coords[0]) + _km(cb, cr_coords[-1])
        swap = _km(cb, cr_coords[0]) + _km(ca, cr_coords[-1])
        if swap < keep:
            a, b, ca, cb = b, a, cb, ca

    # Option 1: follow the real airway fixes between a and b
    # CHANGED: "G208/L125" = 2 airways on the same segment -> try each one
    awy = reroute.get("closed_airway")
    if awy:
        for one_awy in str(awy).split("/"):
            path = wp_res.airway_path(one_awy.strip(), a, b)
            if path and len(path[0]) >= 2:
                return path[0], path[1], "airway"

    # Option 2: straight line a -> b
    if ca and cb and ca != cb:
        return [a, b], [ca, cb], "segment"

    # Couldn't build the segment -> option 3
    return cr_names, cr_coords, "cr"


def build_dep_dest(reroute, wp_res, fir_res, pfx_res,
                   source_fir=None,
                   anchor_coord=None,
                   max_distance_km=6000,
                   min_distance_km=0,
                   corridor_deg=45):
    """
    anchor_coord : (lat, lon) from the NOTAM Q-line. Helps pick the right
                   waypoint when the same name is in 2 countries
                   (e.g. BBS Algeria vs India).
    """
    wps = reroute["waypoints"]

    # Step 1: get coordinates for every waypoint in the reroute
    resolved_raw = wp_res.resolve_route(wps, anchor_coord=anchor_coord)

    # Step 2: add country prefix to each waypoint
    resolved = []
    for r in resolved_raw:
        # Waypoint not found anywhere
        if r["coord"] is None:
            resolved.append({
                "waypoint": r["waypoint"], "coord": None, "prefix": None,
                "fir": None, "source": "unresolved",
                "ambiguous": r.get("ambiguous", False),
            })
            continue

        ndic = r["ndic"]
        if ndic and len(ndic) == 2:
            # waypoints.csv already tells us the country (NDIC column)
            resolved.append({
                "waypoint": r["waypoint"], "coord": r["coord"], "prefix": ndic,
                "fir": None, "source": "ndic",
                "ambiguous": r.get("ambiguous", False),
            })
        else:
            # No country in the CSV (e.g. raw lat/long fix)
            # -> find which FIR shape the point is inside
            lat, lon = r["coord"]
            fir, _ = fir_res.fir_for_point(lat, lon)
            pfx = pfx_res.prefix_for_fir(fir) if fir else None
            resolved.append({
                "waypoint": r["waypoint"], "coord": r["coord"], "prefix": pfx,
                "fir": fir, "source": "polygon" if pfx else "unknown",
                "ambiguous": r.get("ambiguous", False),
            })

    # Split into "not found" and "found"
    missing = [r["waypoint"] for r in resolved if r["coord"] is None]
    with_coord = [r for r in resolved if r["coord"]]

    # Need at least 2 points to know a direction
    if len(with_coord) < 2:
        return {"error": "not enough resolved waypoints",
                "missing_waypoints": missing,
                "resolved": resolved,
                "records": []}

    # Step 3: pick the line the slices hang off (closed segment)
    ref_names, ref_coords, ref_source = _reference_path(reroute, wp_res, with_coord)

    # Step 4: don't list the NOTAM's own country on either side
    exclude_prefix = None
    if source_fir:
        exclude_prefix = pfx_res.prefix_for_fir(source_fir)

    # Step 5: find FIRs inside the two search slices
    classification = classify_firs_directional(
        fir_res, pfx_res, ref_coords,
        max_distance_km=max_distance_km,
        min_distance_km=min_distance_km,
        corridor_deg=corridor_deg,
        exclude_prefix=exclude_prefix,
    )

    # Airports named in the NOTAM text (e.g. Style C "ZGGG TO ZBAA")
    from_airports = reroute.get("from_airports", [])
    to_airports   = reroute.get("to_airports", [])

    # Countries + airports for each side
    beyond_first_out = list(classification["prefixes_first"]) + from_airports
    beyond_last_out  = list(classification["prefixes_last"])  + to_airports

    # Step 6: build output records
    #   bidirectional -> one record each way
    #   one-way       -> one record
    records = []
    if reroute["bidirectional"]:
        records.append({"direction": "FORWARD",
                        "dep": beyond_first_out, "dest": beyond_last_out})
        records.append({"direction": "REVERSE",
                        "dep": beyond_last_out,  "dest": beyond_first_out})
    else:
        records.append({"direction": reroute["direction_hint"] or "ONEWAY",
                        "dep": beyond_first_out, "dest": beyond_last_out})

    # Waypoints that had more than one match in waypoints.csv
    ambiguous = [r["waypoint"] for r in resolved if r.get("ambiguous")]

    return {
        "route_string": "-".join(reroute["tokens"]),
        "waypoints": wps,
        "resolved": resolved,
        "missing_waypoints": missing,
        "ambiguous_waypoints": ambiguous,
        "first_wpt": ref_names[0],                          # start of closed segment
        "last_wpt":  ref_names[-1],                         # end of closed segment
        "reference_path": list(zip(ref_names, ref_coords)), # blue dashed line on map
        "reference_source": ref_source,                     # "airway" / "segment" / "cr"
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