"""
dep_dest_builder.py
-------------------
Takes ONE reroute (from route_extractor) and works out:
  - coordinates + country for every waypoint
  - which countries are on the Dep side and the Dest side

CHANGED: the 45 deg search slices now hang off the CR ITSELF:
  at FIRST : direction  CR wpt2   -> CR wpt1   (and beyond)
  at LAST  : direction  CR wptN-1 -> CR wptN   (and beyond)
The closed airway segment is no longer used for the slices,
so _reference_path() and _km() were removed.
"""
from .side_classifier import classify_firs_directional


def star_prefixes(codes):
    """
    NEW. Adds a * to every 2-letter country prefix:
        ["ZH", "ZS", "RJ", "ZGGG"] -> ["ZH*", "ZS*", "RJ*", "ZGGG"]
    4-letter airports (ZGGG) are left as they are.
    """
    return [c + "*" if c and len(c) == 2 else c for c in codes]


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
    Works the same for CR and optimizer-resolved reroutes.
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

    # Step 3 (CHANGED): slices hang off the CR's own end legs.
    # We pass the CR's points in order - side_classifier uses
    # the first 2 and the last 2 of them for the reference lines.
    ref_names = [r["waypoint"] for r in with_coord]
    ref_coords = [r["coord"] for r in with_coord]

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
    # NEW: aerodrome NOTAMs - the airport end is KNOWN, so no slice search there.
    #   ARR ("INBD TFC TO OEJN") -> LAST end = the airport only
    #   DEP ("DEP TFC TO OEDF")  -> both ends are airports (A) airport -> OEDF)
    # We empty that side's slice results so only the airport shows.
    ad_flow = reroute.get("ad_flow")

    def _blank(side):
        classification[f"beyond_{side}"] = []
        classification[f"prefixes_{side}"] = []
        classification[f"slice_{side}"] = []

    if ad_flow == "ARR":
        _blank("last")
    elif ad_flow == "DEP":
        _blank("first")
        _blank("last")

    # Airports named in the NOTAM text (e.g. Style C "ZGGG TO ZBAA")
    from_airports = reroute.get("from_airports", [])
    to_airports   = reroute.get("to_airports", [])

    # Countries + airports for each side
    # CHANGED: country prefixes get a * (ZH -> ZH*)
    beyond_first_out = star_prefixes(classification["prefixes_first"]) + from_airports
    beyond_last_out  = star_prefixes(classification["prefixes_last"])  + to_airports

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
        "first_wpt": ref_names[0],                          # CHANGED: first CR waypoint
        "last_wpt":  ref_names[-1],                         # CHANGED: last CR waypoint
        # REMOVED: "reference_path" / "reference_source" (closed-segment line)
        "classification": classification,
        "from_airports": from_airports,
        "to_airports": to_airports,
        "records": records,
        "bidirectional": reroute["bidirectional"],
        "closed_airway": reroute["closed_airway"],
        "closed_segment": reroute["closed_segment"],
        "source_fir": source_fir,
        "excluded_prefix": exclude_prefix,
        "cr": reroute.get("cr", False),                     # NEW: CR or optimizer label
        "ad_flow": ad_flow,                                 # NEW: "ARR" / "DEP" / None
    }
