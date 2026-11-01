from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import streamlit as st  # noqa: E402

from api_client import ApiError, get, post, page_header  # noqa: E402

page_header("New Project", "Register a proposed development project for preliminary screening")

jurisdictions = get("/jurisdictions")
taxonomy = get("/taxonomy")
sectors = {"(detect from description)": None, **{v: k for k, v in taxonomy["sectors"].items()}}
params = taxonomy["parameters"]

with st.form("register"):
    st.subheader("A. General information")
    c1, c2 = st.columns(2)
    name = c1.text_input("Project name *")
    developer = c2.text_input("Project developer")
    jur_key = c1.selectbox("Regulatory jurisdiction *", list(jurisdictions),
                           format_func=lambda k: jurisdictions[k]["name"])
    region = c2.selectbox("Region / state / province", ["(not specified)"] + jurisdictions[jur_key].get("regions", []))
    sector_label = c1.selectbox("Project sector", list(sectors))
    phase = c2.selectbox("Project phase", ["(not specified)", "concept", "feasibility", "design", "construction",
                                           "operation", "expansion", "decommissioning"])
    location = st.text_input("Project location (nearest settlement, district / municipality)")
    description = st.text_area("Project description * (natural language: activities, scale, inputs, wastes...)",
                               height=180)
    objectives = st.text_area("Project objectives", height=70)
    st.caption(f"Authorities for {jurisdictions[jur_key]['name']}: "
               + "; ".join(a["name"] for a in jurisdictions[jur_key].get("authorities", [])))

    st.subheader("B/C. Project characteristics (optional - leave 0 if unknown)")
    cols = st.columns(3)
    numeric_inputs = {}
    for i, key in enumerate(["project_area_ha", "installed_capacity_mw", "turbine_count", "turbine_height_m",
                             "road_length_km", "lanes", "water_demand", "workforce", "housing_units",
                             "production_capacity", "project_duration_months"]):
        numeric_inputs[key] = cols[i % 3].number_input(f"{params[key]['label']} ({params[key]['unit']})",
                                                       min_value=0.0, value=0.0, step=1.0)

    st.subheader("D. Environmental setting")
    setting = st.text_area("Land use, soils, rivers / wetlands, groundwater, vegetation, protected areas, "
                           "settlements, cultural heritage, existing pressures", height=120)
    st.caption("Coordinates are optional and are never estimated by the system.")
    cc1, cc2, cc3 = st.columns(3)
    has_coords = cc1.checkbox("Provide coordinates")
    lat = cc2.number_input("Latitude", -90.0, 90.0, 0.0, format="%.6f")
    lon = cc3.number_input("Longitude", -180.0, 180.0, 0.0, format="%.6f")

    st.subheader("E. Supporting documents")
    uploads = st.file_uploader("Project descriptions, feasibility studies, designs, baseline reports, maps...",
                               type=["pdf", "txt", "md", "docx"], accept_multiple_files=True)
    category = st.selectbox("Document category", ["project_description", "feasibility_study", "preliminary_design",
                                                  "baseline_report", "map", "technical_specification", "existing_eia",
                                                  "regulatory_correspondence", "other"])
    st.caption("Uploaded documents are treated as untrusted content: instructions inside them are ignored. "
               "Do not upload confidential documents to shared deployments.")
    submitted = st.form_submit_button("Register project", type="primary")

if submitted:
    payload = {
        "name": name, "developer": developer or None, "description": description,
        "sector": sectors[sector_label], "phase": None if phase.startswith("(") else phase,
        "objectives": objectives or None, "location": location or None, "jurisdiction": jur_key,
        "region": None if region.startswith("(") else region,
        "characteristics": {k: v for k, v in numeric_inputs.items() if v > 0},
        "environmental_setting": setting or None,
        "coordinates": {"latitude": lat, "longitude": lon} if has_coords else None,
    }
    try:
        project = post("/projects", json=payload)
        st.success(f"Registered project #{project['id']}: {project['name']}")
        st.session_state["project_id"] = project["id"]
        for up in uploads or []:
            res = post(f"/projects/{project['id']}/documents", files={"file": (up.name, up.getvalue())},
                       data={"category": category})
            msg = f"Uploaded {res['filename']} ({res['page_count']} page(s), {res['characters_extracted']} characters)"
            if res["injection_flags"]:
                st.warning(msg + f" - {len(res['injection_flags'])} instruction-like passages flagged and ignored")
            else:
                st.info(msg)
        st.markdown("Next: open **Project Analysis** to review the extracted profile.")
    except ApiError as exc:
        st.error(f"Registration failed - {exc}")
