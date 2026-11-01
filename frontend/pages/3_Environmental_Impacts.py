from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from api_client import get, nice, page_header, select_project  # noqa: E402

page_header("Environmental Impact Assessment", "Potential impacts identified for further investigation - not confirmed impacts")
project = select_project(require_screening=True)
if not project:
    st.stop()
pid = project["id"]
impacts = get(f"/projects/{pid}/impacts")
screening = get(f"/projects/{pid}/screening")
risk = screening["assessment"]["environmental_risk_screening"]

c = st.columns(4)
for col, key in zip(c, ("high", "medium", "low", "beneficial")):
    col.metric(f"{key.title()} priority" if key != "beneficial" else "Beneficial effects", risk["counts"][key])
with st.expander("Risk prioritisation methodology (EIAGen-RS-1)"):
    st.write(risk["methodology_description"])
    st.write("**Assumptions:**", *[f"- {a}" for a in risk["assumptions"]])
    st.write("**Limitations:**", *[f"- {a}" for a in risk["limitations"]])

group = st.radio("Organise by", ["Environmental component", "Project activity", "Project phase", "Potential receptor"],
                 horizontal=True)
df = pd.DataFrame([{
    "ID": i["impact_id"], "Potential impact": i["potential_impact"],
    "Component": nice(i["environmental_component"]), "Category": nice(i["component_category"]),
    "Activity": i["project_activity"], "Phase": nice(i["project_phases"]), "Receptor": i["potential_receptor"],
    "Pathway": i["impact_pathway"],
    "Significance (preliminary)": i.get("risk_priority") or ("beneficial" if i["beneficial"] else "-"),
    "Score": i.get("risk_score"), "Confidence": i["confidence"], "Verification": nice(i["verification_status"]),
} for i in impacts])
key = {"Environmental component": "Component", "Project activity": "Activity", "Project phase": "Phase",
       "Potential receptor": "Receptor"}[group]
for value, sub in df.groupby(key, sort=True):
    with st.expander(f"{value} ({len(sub)})", expanded=False):
        st.dataframe(sub.drop(columns=[key]), hide_index=True, use_container_width=True)

st.subheader("Inspect reasoning and supporting information")
choice = st.selectbox("Impact", [f"{i['impact_id']} - {i['potential_impact']}" for i in impacts])
imp = next(i for i in impacts if choice.startswith(i["impact_id"]))
a, b = st.columns(2)
with a:
    st.markdown(f"**Pathway:** {imp['impact_pathway']}")
    st.markdown(f"**Rationale:** {imp['supporting_rationale']}")
    st.markdown("**Evidence:**")
    for e in imp["evidence"] or ["(none)"]:
        st.markdown(f"- {e}")
    st.markdown(f"**Provenance:** {nice(imp['provenance'])}")
with b:
    st.table(pd.DataFrame({"Attribute": ["Likelihood", "Severity", "Receptor sensitivity", "Extent", "Duration",
                                         "Reversibility", "Confidence"],
                           "Value": [imp["likelihood"], imp["severity"], imp["receptor_sensitivity"],
                                     imp["geographical_extent"], nice(imp["duration"]), nice(imp["reversibility"]),
                                     imp["confidence"]]}))
    for n in imp["scoring_notes"]:
        st.caption(n)
    st.markdown("**Additional information required:**")
    for x in imp["additional_information_required"]:
        st.markdown(f"- {x}")
