from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from api_client import get, nice, page_header, select_project  # noqa: E402

page_header("Screening Results", "Preliminary screening outcome - statutory screening and risk screening are kept separate")
project = select_project(require_screening=True)
if not project:
    st.stop()
pid = project["id"]
history = get(f"/projects/{pid}/history")["runs"]
versions = [r["version"] for r in history if r["status"] == "completed"]
version = st.sidebar.selectbox("Screening version", versions[::-1])
s = get(f"/projects/{pid}/screening", version=version)
a = s["assessment"]

st.subheader("Preliminary screening outcome")
for label in a["outcome_labels"]:
    st.markdown(f"- **{label}**")
st.caption(f"Version {s['version']} · review state: {nice(s['review_state'])} · knowledge base {s['knowledge_base_version']} "
           f"· LLM provider: {s['llm_provider']}")
st.info(s["executive_summary"])

reg = a["regulatory_screening"]
st.subheader("A. Regulatory screening (statutory criteria)")
st.write(reg["summary"])
for n in reg["notes"]:
    st.caption(n)
icon = {"met": "✅ met", "not_met": "➖ not met", "indeterminate": "❔ indeterminate"}
st.dataframe(pd.DataFrame([{"Criterion": c["criterion_id"], "Title": c["title"], "Result": icon[c["result"]],
                            "Explanation": c["explanation"], "Missing data": nice(c["missing_parameters"]) or "-",
                            "Criterion verified": c["verification_status"], "Source": f"{c['source_document_id']} "
                            f"{c['source_section']}", "Evidence": ", ".join(c["evidence"]) or "-"}
                           for c in reg["criteria"]]), hide_index=True, use_container_width=True)

risk = a["environmental_risk_screening"]
st.subheader("B. Environmental risk screening (non-statutory prioritisation)")
c1, c2 = st.columns(2)
with c1:
    st.write("Top potential risks:")
    for t in risk["top_risks"]:
        st.markdown(f"- {t}")
with c2:
    st.dataframe(pd.DataFrame([{"Component": k, "Highest priority": v} for k, v in risk["component_summary"].items()]),
                 hide_index=True, use_container_width=True)

st.subheader("Missing information")
missing = get(f"/projects/{pid}/missing-information")
st.dataframe(pd.DataFrame([{"Importance": m["importance"], "Missing": m["description"],
                            "Question": m["clarification_question"]} for m in missing["missing_information"]]),
             hide_index=True, use_container_width=True)

st.subheader("Recommended further investigations")
st.dataframe(pd.DataFrame([{"ID": i["investigation_id"], "Investigation": i["name"], "Priority": i["priority"],
                            "Reason": i["reason"]} for i in s["investigations"]]), hide_index=True, use_container_width=True)

st.subheader("Preliminary mitigation considerations")
mit = pd.DataFrame([{"ID": m["measure_id"], "Impact": m["impact_id"], "Level": m["hierarchy_level"],
                     "Measure": m["measure"], "Type": nice(m["measure_type"])} for m in s["mitigation"]])
level = st.selectbox("Mitigation hierarchy level", ["all", "avoid", "minimize", "restore", "offset"])
st.dataframe(mit if level == "all" else mit[mit["Level"] == level], hide_index=True, use_container_width=True)
st.caption("Mitigation measures are considerations; they do not demonstrate that impacts are adequately controlled.")

st.subheader("Human verification requirements")
v = s["validation"]
st.write(f"Citations verified: {v['citations_valid']}/{v['citations_checked']} · validation "
         f"{'passed' if v['passed'] else 'raised blocking issues'}")
for issue in v["issues"]:
    if issue["severity"] != "info":
        st.markdown(f"- `{issue['severity']}` **{issue['code']}** {issue.get('finding_ref') or ''}: {issue['message']}")
for u in v["unsupported_claims"]:
    st.error(f"Unsupported item flagged: {u}")
for r in a["rationale"]:
    st.caption(r)
