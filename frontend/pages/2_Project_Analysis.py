from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from api_client import ApiError, get, nice, page_header, patch, post, select_project  # noqa: E402

page_header("Project Analysis", "Extracted project profile - review and correct before screening")
project = select_project()
if not project:
    st.stop()
pid = project["id"]
detail = get(f"/projects/{pid}")

c1, c2 = st.columns([3, 1])
c1.markdown(f"**{detail['name']}** · {detail['jurisdiction']} · {nice(detail['sector'])} · "
            f"state: *{nice(detail['review_state'])}*")
if c2.button("Analyse / re-analyse description"):
    with st.spinner("Extracting project profile..."):
        post(f"/projects/{pid}/analyze")
    st.rerun()

profile = detail.get("profile")
if not profile:
    st.info("This project has not been analysed yet. Click **Analyse** above.")
    st.stop()

st.caption(profile["interpretation_note"])
for w in profile.get("injection_warnings", []):
    st.error(f"Instruction-like text detected and ignored: {w}")
for n in profile.get("geospatial_notes", []):
    st.caption(f"🗺️ {n}")
for n in profile.get("ai_notes", []):
    st.caption(f"🤖 {n}")

col1, col2 = st.columns(2)
with col1:
    st.subheader("Profile fields")
    rows = []
    for key in ("sector", "location", "region", "coordinates", "phase", "environmental_setting"):
        f = profile[key]
        rows.append({"Field": key.replace("_", " "), "Value": nice(f["value"]), "Status": nice(f["status"]),
                     "Provenance": nice(f.get("provenance"))})
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
    st.subheader("Parameters")
    if profile["parameters"]:
        st.dataframe(pd.DataFrame([{"Parameter": p["label"], "Value": p["value"], "Unit": p["unit"],
                                    "Source": p.get("source_text"), "Status": nice(p["status"]),
                                    "Provenance": nice(p["provenance"])} for p in profile["parameters"]]),
                     hide_index=True, use_container_width=True)
    else:
        st.caption("No quantitative parameters found.")
with col2:
    st.subheader("Project phases")
    st.write(nice(profile["phases"]) or "-")
    st.subheader("Potential receptors")
    st.dataframe(pd.DataFrame([{"Receptor": r["name"], "Sensitivity": r["sensitivity"],
                                "Confirmed": "yes" if r["presence_confirmed"] else "no", "Basis": r["basis"]}
                               for r in profile["receptors"]]), hide_index=True, use_container_width=True)
    st.write("Setting indicators:", nice(profile["setting_flags"]) or "none")

st.subheader("Identified activities")
st.dataframe(pd.DataFrame([{"ID": a["activity_id"], "Activity": a["name"], "Phases": nice(a["phases"]),
                            "Evidence": (a["matched_text"] or ["-"])[0][:140], "Status": nice(a["status"]),
                            "Provenance": nice(a["provenance"]), "Confidence": a["confidence"]}
                           for a in profile["activities"]]), hide_index=True, use_container_width=True)
st.write("Pressures:", nice([p["name"] for p in profile["pressures"]]))

st.subheader("Missing information and clarification questions")
for m in profile["missing_information"]:
    icon = {"critical": "🔴", "important": "🟠", "useful": "🟡"}[m["importance"]]
    st.markdown(f"{icon} **{m['description']}** - {m['clarification_question']}")

st.divider()
st.subheader("Correct the extracted information")
tax = get("/taxonomy")
with st.form("corrections"):
    reviewer = st.text_input("Your name *")
    sectors = ["(no change)"] + list(tax["sectors"])
    new_sector = st.selectbox("Sector", sectors)
    current = [a["key"] for a in profile["activities"]]
    add = st.multiselect("Add activities", [k for k in tax["activities"] if k not in current],
                         format_func=lambda k: tax["activities"][k])
    remove = st.multiselect("Remove activities", current, format_func=lambda k: tax["activities"][k])
    setting = st.text_area("Environmental setting (replace)", value=profile["environmental_setting"]["value"] or "")
    param_key = st.selectbox("Set parameter", ["(none)"] + list(tax["parameters"]))
    param_val = st.number_input("Parameter value", min_value=0.0, value=0.0)
    if st.form_submit_button("Apply corrections"):
        body = {"corrected_by": reviewer, "add_activities": add, "remove_activities": remove}
        if new_sector != "(no change)":
            body["sector"] = new_sector
        if setting != (profile["environmental_setting"]["value"] or ""):
            body["environmental_setting"] = setting
        if param_key != "(none)":
            body["parameters"] = {param_key: param_val}
        try:
            patch(f"/projects/{pid}/profile", body)
            st.success("Corrections saved and audited.")
            st.rerun()
        except ApiError as exc:
            st.error(str(exc))

st.divider()
if st.button("▶ Run preliminary screening workflow", type="primary"):
    with st.spinner("Running the 9-stage screening workflow (retrieval, screening, validation, report)..."):
        try:
            result = post(f"/projects/{pid}/screening")
            st.success(f"Screening version {result['version']} completed - state: {nice(result['review_state'])}")
            st.dataframe(pd.DataFrame([{"Stage": nice(s["stage"]), "Status": s["status"],
                                        "ms": s["duration_ms"], "Message": s["message"]} for s in result["stages"]]),
                         hide_index=True, use_container_width=True)
        except ApiError as exc:
            st.error(str(exc))
