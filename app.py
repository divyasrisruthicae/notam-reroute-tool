import streamlit as st
import folium
from streamlit_folium import st_folium

from core.notam_parser import (
    extract_notam_id, extract_source_fir, extract_e_section, extract_q_coordinate
)
from core.route_extractor import extract_reroutes
from core.waypoint_resolver import WaypointResolver
from core.fir_resolver import FIRResolver
from core.prefix_resolver import PrefixResolver
from core.dep_dest_builder import build_dep_dest

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
    for a in extra_airports:
        if a and a not in seen:
            seen.append(a)
    return seen

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

    nearest_n = st.slider(
        "Show nearest FIRs per side", 3, 40, 6, 1,
        help="Shows only the N closest FIRs first. Expand below to see all.",
    )

    max_dist = st.slider(
        "Max ray distance (km)", 500, 12000, 6000, 250,   # 6000 (wide, hemisphere)
        help="Upper bound only. Nearest-N fills first, so far FIRs rarely show."
    )
    min_dist = st.slider(
        "Min ray distance (km)", 0, 1000, 0, 50,           # 0
    )
    corridor = st.slider(
        "Corridor half-angle (°)", 15, 90, 90, 5,          # 90 = FULL HEMISPHERE
        help="90° = everything ahead vs behind (matches manual FPID logic). "
             "Lower it only if you want a tighter directional slice."
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
    q_coord = extract_q_coordinate(notam_text)
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
        "dep_side_choice": dep_side_choice,
        "nearest_n": nearest_n,
    }

# ---------- Render ----------
analysis = st.session_state.analysis
if analysis:
    c1, c2, c3 = st.columns(3)
    c1.metric("NOTAM ID", analysis["notam_id"] or "—")
    c2.metric("Source FIR", analysis["src_fir"] or "—")
    c3.metric("Reroutes found", len(analysis["reroutes"]))

    if not analysis["reroutes"]:
        st.warning("No reroutes extracted from this NOTAM text.")

    dep_is_last = analysis["dep_side_choice"].startswith("Route END")
    N = analysis["nearest_n"]

    for i, (rr, out) in enumerate(analysis["reroutes"], 1):
        st.markdown(f"---\n### 🛫 Reroute {i}")
        st.code(rr["raw"])

        if rr["closed_airway"]:
            st.info(
                f"Closed: **{rr['closed_airway']}** between "
                f"**{rr['closed_segment'][0]}** and **{rr['closed_segment'][1]}**"
            )

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

        # Nearest N slices
        dep_near  = dep_items[:N]
        dest_near = dest_items[:N]

        dep_pref_near  = ordered_prefixes(dep_near,  dep_extra)
        dest_pref_near = ordered_prefixes(dest_near, dest_extra)
        dep_pref_all   = ordered_prefixes(dep_items,  dep_extra)
        dest_pref_all  = ordered_prefixes(dest_items, dest_extra)

        # ---- Text output (nearest first) ----
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

        # ---- Copyable output ----
        summary = (
            f"NOTAM: {analysis['notam_id']}\n"
            f"Route: {out['route_string']}\n"
            f"First WPT: {out['first_wpt']} | Last WPT: {out['last_wpt']}\n"
            f"Dep Airports: {', '.join(dep_pref_near) or '-'}\n"
            f"Dest Airports: {', '.join(dest_pref_near) or '-'}\n"
            f"Bidirectional: {out['bidirectional']}"
        )
        st.markdown(f"**Copyable output (Reroute {i})**")
        st.code(summary, language="text")

        # ---- Map (only nearest N per side, colors MATCH text) ----
        coords = [r["coord"] for r in out["resolved"] if r["coord"]]
        if coords:
            center = coords[len(coords)//2]
            m = folium.Map(location=center, zoom_start=4, tiles="cartodbpositron")

            folium.PolyLine(coords, color="black", weight=4).add_to(m)
            folium.Marker(coords[0],  icon=folium.Icon(color="green"),
                          popup=f"FIRST: {out['first_wpt']}").add_to(m)
            folium.Marker(coords[-1], icon=folium.Icon(color="red"),
                          popup=f"LAST: {out['last_wpt']}").add_to(m)

            # DEP = red circles
            for item in dep_near:
                folium.CircleMarker(
                    location=item["centroid"], radius=8,
                    color="red", fill=True, fill_opacity=0.75,
                    tooltip=f"{item['fir_code']} ({item['prefix']}) — DEP "
                            f"{item['distance_km']:.0f}km",
                ).add_to(m)

            # DEST = blue circles
            for item in dest_near:
                folium.CircleMarker(
                    location=item["centroid"], radius=8,
                    color="blue", fill=True, fill_opacity=0.6,
                    tooltip=f"{item['fir_code']} ({item['prefix']}) — DEST "
                            f"{item['distance_km']:.0f}km",
                ).add_to(m)

            st.caption(
                "🔴 Red circles = **Dep. Airports** side  |  "
                "🔵 Blue circles = **Dest. Airports** side  |  "
                "⚫ Black line = reroute path"
            )

            st_folium(m, height=500, key=f"map_{i}", returned_objects=[])