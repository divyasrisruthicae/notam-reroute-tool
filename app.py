import streamlit as st
import folium
from streamlit_folium import st_folium

from core.notam_parser import (
    extract_notam_id, extract_source_fir, extract_e_section, extract_q_coordinate, extract_a_location,      # NEW: A) airport, used for "DEP TFC TO XXXX" NOTAMs
)
from core.route_extractor import extract_reroutes
from core.waypoint_resolver import WaypointResolver
from core.fir_resolver import FIRResolver
from core.prefix_resolver import PrefixResolver
from core.dep_dest_builder import build_dep_dest, star_prefixes   # CHANGED: + star_prefixes

st.set_page_config(page_title="NOTAM Reroute Tool", layout="wide")
st.title("✈️ NOTAM Reroute → Dep/Dest FIR Tool")

@st.cache_resource
def load_resources():
    return (
        WaypointResolver("data/waypoints.csv"),
        FIRResolver("data/fir_boundaries.geojson"),
        PrefixResolver("data/fir_prefix_map.csv"),
    )

wp_res, fir_res, pfx_res = load_resources()

# ---------- Helper: dedup prefixes, keep nearest-first order ----------
def ordered_prefixes(items, extra_airports):
    seen = []
    for it in items:
        if it["prefix"] and it["prefix"] not in seen:
            seen.append(it["prefix"])
    # CHANGED: country prefixes get a * (ZH -> ZH*). Airports are added after,
    # so 4-letter codes like ZGGG never get a star.
    seen = star_prefixes(seen)
    for a in extra_airports:
        if a and a not in seen:
            seen.append(a)
    return seen

import urllib.parse   # NEW: used to build the SkyVector link


def skyvector_url(rr, out, chart=304, zoom=5):
    """
    NEW. Builds a SkyVector flight-plan link for this reroute.
    Three things matter, or SkyVector guesses wrong:
      1. NO "DCT" tokens - SkyVector doesn't understand them and
         silently drops the fix that comes after.
      2. ll=lat,lon  - centres the map on OUR route, otherwise it
         matches same-named fixes on the other side of the world
         (CEA -> KCEA in the USA).
      3. chart=304 (World Hi) + zoom, so the enroute chart opens.
    Result:
      https://skyvector.com/?ll=21.53,86.71&chart=304&zoom=5&fpl=CEA G450 JJS JRS VVZ
    """
    # Keep the NOTAM's own order (waypoints AND airways), drop DCT
    toks = [t for t in rr.get("raw", "").replace("-", " ").split()
            if t.upper() != "DCT"]
    if not toks:
        toks = out.get("waypoints", [])          # fallback
    if not toks:
        return None

    # Centre = middle of the resolved coordinates we already have
    pts = [r["coord"] for r in out.get("resolved", []) if r.get("coord")]
    params = {"chart": str(chart), "zoom": str(zoom), "fpl": " ".join(toks)}
    if pts:
        lat = sum(p[0] for p in pts) / len(pts)
        lon = sum(p[1] for p in pts) / len(pts)
        # ll must come FIRST in the link, like the working example
        params = {"ll": f"{lat},{lon}", **params}

    return "https://skyvector.com/?" + urllib.parse.urlencode(params)

# NEW: short text like "DEP OEJN → ARR OEDF" for aerodrome NOTAMs
def ad_flow_label(rr):
    if not rr.get("ad_flow"):
        return ""
    dep = ", ".join(rr["from_airports"]) or "—"
    arr = ", ".join(rr["to_airports"]) or "—"
    return f"DEP {dep} → ARR {arr}"

# ---------- Sidebar ----------
with st.sidebar:
    st.header("⚙️ Settings")

    dep_side_choice = st.radio(
        "Which end is 'Dep. Airports'?",
        ["Route END side (beyond LAST wpt)",
         "Route START side (beyond FIRST wpt)"],
        index=0,
        help="The route flies FIRST → LAST. Pick which end holds the "
             "departure airports. The other end becomes destinations.",
    )

    # NEW: analyst can hide / show the optimizer-resolved reroutes
    show_optimizer = st.toggle(
        "Show optimizer-resolved reroutes", value=True,
        help="Airway-only reroutes (wpt - AWY - wpt). The NOTAM doesn't list "
             "every fix, so check these before using them.",
    )

    nearest_n = st.slider(
        "Show nearest FIRs per side", 3, 40, 6, 1,
        help="Shows only the N closest FIRs first. Expand below to see all.",
    )

    max_dist = st.slider(
        "Max ray distance (km)", 500, 9000, 6000, 250,
        help="Upper bound only. Nearest-N fills first, so far FIRs rarely show."
    )
    min_dist = st.slider("Min ray distance (km)", 0, 1000, 0, 50)
    corridor = st.slider(
        "Search slice half-angle (°)", 15, 90, 45, 5,
        # CHANGED: slices now come off the CR's end legs, not the closed segment
        help="±45° around the reference line (CR end leg extended outward)."
    )

    st.caption("Source FIR is auto-excluded. Nearest countries shown first.")

# ---------- State ----------
if "analysis" not in st.session_state:
    st.session_state.analysis = None

notam_text = st.text_area(
    "Paste NOTAM", height=280,
    value=st.session_state.get("last_notam", ""),
    key="notam_input",
)

if st.button("🚀 Analyze"):
    st.session_state.last_notam = notam_text
    notam_id = extract_notam_id(notam_text)
    src_fir  = extract_source_fir(notam_text)
    e_text   = extract_e_section(notam_text)
    reroutes = extract_reroutes(e_text)
    q_coord  = extract_q_coordinate(notam_text)
    a_loc    = extract_a_location(notam_text)      # NEW

    # NEW: "DEP TFC TO OEDF" -> departure airport = the A) airport (e.g. OEJN)
    for rr in reroutes:
        if rr.get("ad_flow") == "DEP" and not rr["from_airports"] and a_loc:
            rr["from_airports"] = [a_loc]

    # CHANGED: removed the "if not rr['cr']: skip" filter.
    # EVERY reroute (CR and optimizer-resolved) now gets Dep/Dest.
    outputs = []
    for rr in reroutes:
        out = build_dep_dest(
            rr, wp_res, fir_res, pfx_res,
            source_fir=src_fir,
            anchor_coord=q_coord,
            max_distance_km=max_dist,
            min_distance_km=min_dist,
            corridor_deg=corridor,
        )
        outputs.append((rr, out))

    st.session_state.analysis = {
        "notam_id": notam_id,
        "src_fir": src_fir,
        "reroutes": outputs,
        # REMOVED: "optimizer_only" list - they're inside "reroutes" now
        "dep_side_choice": dep_side_choice,
        "nearest_n": nearest_n,
    }

# ---------- Render ----------
analysis = st.session_state.analysis
if analysis:
    # NEW: count CR vs optimizer from the one list
    all_rr = analysis["reroutes"]
    n_cr  = sum(1 for rr, _ in all_rr if rr["cr"])
    n_opt = len(all_rr) - n_cr

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("NOTAM ID", analysis["notam_id"] or "—")
    c2.metric("Source FIR", analysis["src_fir"] or "—")
    c3.metric("Coded routes", n_cr)
    c4.metric("Optimizer-resolved", n_opt)

    if not all_rr:
        st.warning("No reroutes extracted from this NOTAM text.")

    # NEW: apply the sidebar toggle (works straight away, no re-Analyze needed)
    shown = [(rr, out) for rr, out in all_rr if rr["cr"] or show_optimizer]
    if n_opt and not show_optimizer:
        st.caption(f"🙈 {n_opt} optimizer-resolved reroute(s) hidden — "
                   f"turn on the toggle in the sidebar to see them.")

    dep_is_last_setting = analysis["dep_side_choice"].startswith("Route END")   # CHANGED: renamed
    N = analysis["nearest_n"]

    for i, (rr, out) in enumerate(shown, 1):
        st.markdown(f"---\n### 🛫 Reroute {i}")

        # NEW: mark so the analyst can see which kind it is
        if rr["cr"]:
            st.success("📌 **Coded route (CR)** — full path given in the NOTAM "
                       "(DCT / coordinate fix).")
        else:
            st.warning("⚙️ **Optimizer-resolved** — airway route (wpt - AWY - wpt), "
                       "fixes in between not listed. Analyst to check before use.")

        st.code(rr["raw"])

        # NEW: aerodrome NOTAM -> traffic always flies FIRST -> LAST
        # (e.g. TOKRA ... BOSUT -> OEJN), so Dep = START side whatever the
        # sidebar says. Normal NOTAMs still use the sidebar setting.
        if rr.get("ad_flow"):
            dep_is_last = False
            st.info(f"Aerodrome routing: **{ad_flow_label(rr)}**")
        else:
            dep_is_last = dep_is_last_setting

        if rr["closed_airway"]:
            seg = rr["closed_segment"]
            if seg:
                tag = " _(inferred from CR endpoints)_" if rr["segment_inferred"] else ""
                st.info(f"Closed: **{rr['closed_airway']}** between "
                        f"**{seg[0]}** and **{seg[1]}**{tag}")
            else:
                st.info(f"Closed: **{rr['closed_airway']}**")

        if out.get("error") and not out["records"]:
            st.error(f"❌ {out['error']}. Missing: {out['missing_waypoints']}")
            continue
        if out["missing_waypoints"]:
            st.warning(f"⚠️ Unresolved waypoints: `{', '.join(out['missing_waypoints'])}`")

        cls = out["classification"]

        # ---- Assign Dep/Dest ITEMS based on toggle (sorted nearest-first) ----
        if dep_is_last:
            dep_items   = sorted(cls["beyond_last"],  key=lambda x: x["distance_km"])
            dest_items  = sorted(cls["beyond_first"], key=lambda x: x["distance_km"])
            dep_extra   = out["to_airports"]
            dest_extra  = out["from_airports"]
        else:
            dep_items   = sorted(cls["beyond_first"], key=lambda x: x["distance_km"])
            dest_items  = sorted(cls["beyond_last"],  key=lambda x: x["distance_km"])
            dep_extra   = out["from_airports"]
            dest_extra  = out["to_airports"]


        dep_near  = dep_items[:N]
        dest_near = dest_items[:N]

        dep_pref_near  = ordered_prefixes(dep_near,  dep_extra)
        dest_pref_near = ordered_prefixes(dest_near, dest_extra)
        dep_pref_all   = ordered_prefixes(dep_items,  dep_extra)
        dest_pref_all  = ordered_prefixes(dest_items, dest_extra)


        colA, colB = st.columns(2)
        with colA:
            st.markdown(f"**Dep. Airports** — nearest {len(dep_pref_near)}")
            st.code(", ".join(dep_pref_near) or "—")
        with colB:
            st.markdown(f"**Dest. Airports** — nearest {len(dest_pref_near)}")
            st.code(", ".join(dest_pref_near) or "—")

        with st.expander(f"➕ Show ALL FIRs (Dep {len(dep_pref_all)} / Dest {len(dest_pref_all)})"):
            cc1, cc2 = st.columns(2)
            with cc1:
                st.markdown("**Dep. (all)**"); st.code(", ".join(dep_pref_all) or "—")
            with cc2:
                st.markdown("**Dest. (all)**"); st.code(", ".join(dest_pref_all) or "—")

        summary = (
            f"NOTAM: {analysis['notam_id']}\n"
            f"Type: {'CR' if rr['cr'] else 'Optimizer-resolved'}\n"   # NEW line
            f"Route: {out['route_string']}\n"
            f"First WPT: {out['first_wpt']} | Last WPT: {out['last_wpt']}\n"
            f"Dep Airports: {', '.join(dep_pref_near) or '-'}\n"
            f"Dest Airports: {', '.join(dest_pref_near) or '-'}\n"
            f"Bidirectional: {out['bidirectional']}"
        )
        st.markdown(f"**Copyable output (Reroute {i})**")
        st.code(summary, language="text")
        # ---- SkyVector link (opens the same route on their chart) ----
        sv = skyvector_url(rr, out)
        if sv:
            st.markdown(
                f"🗺️ [**SkyVector**]({sv}) "
                f"<span style='color:#888;font-size:0.85em'>"
                f"(opens in a new tab)</span>",
                unsafe_allow_html=True,
            )
        # ---- Map ----
        coords = [r["coord"] for r in out["resolved"] if r["coord"]]
        if coords:
            center = coords[len(coords)//2]
            m = folium.Map(location=center, zoom_start=4, tiles=None)

            folium.TileLayer(
                tiles="https://server.arcgisonline.com/ArcGIS/rest/services/"
                      "World_Street_Map/MapServer/tile/{z}/{y}/{x}",
                attr="Tiles © Esri", name="Street", max_zoom=16,
            ).add_to(m)

            folium.TileLayer(
                tiles="https://server.arcgisonline.com/ArcGIS/rest/services/"
                      "Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}",
                attr="Tiles © Esri", name="Light Gray", max_zoom=16,
            ).add_to(m)

            folium.TileLayer(
                tiles="https://server.arcgisonline.com/ArcGIS/rest/services/"
                      "World_Imagery/MapServer/tile/{z}/{y}/{x}",
                attr="Tiles © Esri", name="Satellite", max_zoom=16,
            ).add_to(m)

            # CHANGED: optimizer-resolved reroutes are drawn dashed
            folium.PolyLine(coords, color="black", weight=4,
                            dash_array=None if rr["cr"] else "10",
                            tooltip="CR" if rr["cr"] else "Optimizer-resolved").add_to(m)

            # REMOVED: blue dashed "closed segment" line - the slices now
            # come off the black reroute line's end legs, so it's not needed.

            dep_slice  = cls["slice_last"]  if dep_is_last else cls["slice_first"]
            dest_slice = cls["slice_first"] if dep_is_last else cls["slice_last"]
            if dep_slice:
                folium.Polygon(dep_slice, color="red", weight=1,
                               fill=True, fill_opacity=0.08,
                               tooltip="Dep search slice").add_to(m)
            if dest_slice:
                folium.Polygon(dest_slice, color="blue", weight=1,
                               fill=True, fill_opacity=0.08,
                               tooltip="Dest search slice").add_to(m)
            folium.Marker(coords[0],  icon=folium.Icon(color="green"),
                          popup=f"FIRST: {out['first_wpt']}").add_to(m)
            folium.Marker(coords[-1], icon=folium.Icon(color="red"),
                          popup=f"LAST: {out['last_wpt']}").add_to(m)

            for item in dep_near:
                folium.CircleMarker(
                    location=item["centroid"], radius=8,
                    color="red", fill=True, fill_opacity=0.75,
                    tooltip=f"{item['fir_code']} ({item['prefix']}*) — DEP "     # CHANGED: *
                            f"{item['distance_km']:.0f}km",
                ).add_to(m)

            for item in dest_near:
                folium.CircleMarker(
                    location=item["centroid"], radius=8,
                    color="blue", fill=True, fill_opacity=0.6,
                    tooltip=f"{item['fir_code']} ({item['prefix']}*) — DEST "    # CHANGED: *
                            f"{item['distance_km']:.0f}km",
                ).add_to(m)

            st.caption(
                "🔴 Red circles = **Dep. Airports** side  |  "
                "🔵 Blue circles = **Dest. Airports** side  |  "
                "⚫ Black line = reroute path (dashed = optimizer-resolved)"
            )
            folium.LayerControl(collapsed=True).add_to(m)
            st_folium(m, height=500, key=f"map_{i}", returned_objects=[])

    # REMOVED: the separate "Optimizer-resolved — no CR issued" list at the
    # bottom. Those reroutes now show above with full Dep/Dest + a ⚙️ mark.