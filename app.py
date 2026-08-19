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
from core.dep_dest_builder import build_dep_dest, build_dep_dest_airway
from core.airway_graph import AirwayGraph

st.set_page_config(page_title="NOTAM Reroute Tool", layout="wide")
st.title("✈️ NOTAM Reroute → Dep/Dest FIR Tool")


@st.cache_resource
def load_resources():
    return (
        WaypointResolver("data/waypoints.csv"),
        FIRResolver("data/fir_boundaries.geojson"),
        PrefixResolver("data/fir_prefix_map.csv"),
        AirwayGraph("data/waypoints.csv"),
    )


wp_res, fir_res, pfx_res, awy_graph = load_resources()

# ---------- Sidebar ----------
with st.sidebar:
    st.header("⚙️ Settings")

    method = st.radio(
        "Dep/Dest method",
        ["Airway network (recommended)", "Geometry (fallback)"],
        index=0,
        help="Airway network follows real airways from the CSV. "
             "Geometry uses direction on the map.",
    )

    awy_dist = st.slider(
        "Airway spread distance (km)", 1000, 8000, 3500, 250,
        help="How far to follow airways outward from each endpoint.",
    )

    collapse_wild = st.checkbox(
        "Show collapsed wildcards (V*, Z* ...)", value=True,
        help="Collapses all reachable FIRs to 1-letter wildcards, "
             "like the manual FPID format.",
    )

    swap_sides = st.checkbox(
        "Swap Dep ↔ Dest", value=False,
        help="Flip if the two sides come out reversed for your convention.",
    )

    # geometry-only sliders
    if method.startswith("Geometry"):
        nearest_n = st.slider("Nearest FIRs per side", 3, 40, 6, 1)
        max_dist  = st.slider("Max ray distance (km)", 500, 9000, 6000, 250)
        corridor  = st.slider("Corridor half-angle (°)", 15, 90, 90, 5)
    else:
        nearest_n, max_dist, corridor = 6, 6000, 90

    st.caption("Source FIR auto-excluded.")

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
    q_coord  = extract_q_coordinate(notam_text)
    reroutes = extract_reroutes(e_text)

    src_prefix = pfx_res.prefix_for_fir(src_fir) if src_fir else None

    outputs = []
    for rr in reroutes:
        out_geo = build_dep_dest(
            rr, wp_res, fir_res, pfx_res,
            source_fir=src_fir, anchor_coord=q_coord,
            max_distance_km=max_dist, min_distance_km=0, corridor_deg=corridor,
        )
        out_awy = build_dep_dest_airway(
            rr, awy_graph, source_fir_prefix=src_prefix,
            max_distance_km=awy_dist,
        )
        outputs.append((rr, out_geo, out_awy))

    st.session_state.analysis = {
        "notam_id": notam_id, "src_fir": src_fir, "reroutes": outputs,
        "method": method, "nearest_n": nearest_n,
        "collapse_wild": collapse_wild, "swap_sides": swap_sides,
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

    use_airway = analysis["method"].startswith("Airway")

    def to_wild(prefixes):
        letters = sorted({p[0] for p in prefixes if p})
        return [f"{L}*" for L in letters]

    for i, (rr, out_geo, out_awy) in enumerate(analysis["reroutes"], 1):
        st.markdown(f"---\n### 🛫 Reroute {i}")
        st.code(rr["raw"])

        if rr["closed_airway"]:
            seg = rr["closed_segment"]
            if seg:
                st.info(f"Closed: **{rr['closed_airway']}** between "
                        f"**{seg[0]}** and **{seg[1]}**")
            else:
                st.info(f"Closed: **{rr['closed_airway']}**")

        # ----- pick source of Dep/Dest -----
        if use_airway:
            if out_awy.get("error"):
                st.warning(f"⚠️ Airway network: {out_awy['error']} — "
                           f"showing geometry instead.")
                dep = out_geo["classification"]["prefixes_first"]
                dest = out_geo["classification"]["prefixes_last"]
            else:
                dep = out_awy["dep_prefixes"]
                dest = out_awy["dest_prefixes"]
            first_wpt, last_wpt = out_awy["first_wpt"], out_awy["last_wpt"]
        else:
            dep = out_geo["classification"]["prefixes_first"]
            dest = out_geo["classification"]["prefixes_last"]
            first_wpt = out_geo.get("first_wpt", "?")
            last_wpt  = out_geo.get("last_wpt", "?")

        if analysis["swap_sides"]:
            dep, dest = dest, dep

        dep_show  = to_wild(dep)  if analysis["collapse_wild"] else dep
        dest_show = to_wild(dest) if analysis["collapse_wild"] else dest

        colA, colB = st.columns(2)
        with colA:
            st.markdown(f"**Dep. Airports** ({len(dep_show)})")
            st.code(", ".join(dep_show) or "—")
        with colB:
            st.markdown(f"**Dest. Airports** ({len(dest_show)})")
            st.code(", ".join(dest_show) or "—")

        with st.expander("🔎 Raw FIR prefixes (before wildcard collapse)"):
            e1, e2 = st.columns(2)
            with e1:
                st.markdown("**Dep**"); st.code(", ".join(dep) or "—")
            with e2:
                st.markdown("**Dest**"); st.code(", ".join(dest) or "—")

        summary = (
            f"NOTAM: {analysis['notam_id']}\n"
            f"Route: {rr.get('raw','')}\n"
            f"First WPT: {first_wpt} | Last WPT: {last_wpt}\n"
            f"Dep Airports: {', '.join(dep_show) or '-'}\n"
            f"Dest Airports: {', '.join(dest_show) or '-'}\n"
            f"Bidirectional: {rr['bidirectional']}"
        )
        st.markdown(f"**Copyable output (Reroute {i})**")
        st.code(summary, language="text")

        # ----- Map -----
        coords = [r["coord"] for r in out_geo.get("resolved", []) if r["coord"]]
        if coords:
            center = coords[len(coords)//2]
            m = folium.Map(location=center, zoom_start=4, tiles="cartodbpositron")
            folium.PolyLine(coords, color="black", weight=4).add_to(m)
            folium.Marker(coords[0], icon=folium.Icon(color="green"),
                          popup=f"FIRST: {first_wpt}").add_to(m)
            folium.Marker(coords[-1], icon=folium.Icon(color="red"),
                          popup=f"LAST: {last_wpt}").add_to(m)
            st.caption("🟢 START · 🔴 END · ⚫ reroute path")
            st_folium(m, height=420, key=f"map_{i}", returned_objects=[])