from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from api_client import ApiError, get, nice, page_header, post, select_project  # noqa: E402

page_header("Report Review", "Human review of the preliminary screening report - mandatory before issuance")
project = select_project(require_screening=True)
if not project:
    st.stop()
pid = project["id"]
if "flash" in st.session_state:
    st.success(st.session_state.pop("flash"))
report = get(f"/projects/{pid}/report")
content = report["content"]
state = content["review_state"]

st.markdown(f"**Report v{report['report_version']}** · screening v{content['screening_version']} · "
            f"generated {report['generated_at'][:19]} · KB {report['knowledge_base_version']} · "
            f"current state: **{nice(get(f'/projects/{pid}')['review_state'])}**")
st.error(f"{content['status_label']}\n\n{content['disclaimer']}")

d1, d2, d3 = st.columns(3)
for col, fmt, label in ((d1, "pdf", "Download PDF"), (d2, "docx", "Download DOCX"), (d3, "md", "Download Markdown")):
    try:
        data = get(f"/projects/{pid}/report/export", format=fmt)
        col.download_button(label, data, file_name=f"eiagen_report_p{pid}_v{report['report_version']}.{fmt}")
    except ApiError as exc:
        col.caption(str(exc))

tab_report, tab_findings, tab_decision, tab_history = st.tabs(
    ["Inspect report", "Review findings", "Decision", "Review history & audit"])

with tab_report:
    for s in content["sections"]:
        with st.expander(f"{s['number']}. {s['title']}", expanded=s["number"] in (2, 13)):
            for p in s["paragraphs"]:
                st.write(p)
            if s.get("table") and s["table"]["rows"]:
                st.dataframe(pd.DataFrame(s["table"]["rows"], columns=s["table"]["columns"]),
                             hide_index=True, use_container_width=True)

REVIEWABLE = {"impact": ("/impacts", "impact_id", "potential_impact",
                         ["likelihood", "severity", "confidence", "potential_receptor", "impact_pathway"]),
              "requirement": ("/regulations", "requirement_id", "relevant_provision",
                              ["applicability", "potential_obligation", "permit_or_approval"]),
              "mitigation": ("/mitigation", "measure_id", "measure", ["measure", "hierarchy_level", "measure_type"])}

with tab_findings:
    st.caption("Mark findings verified / rejected or correct them. Every change is recorded with your identity, "
               "the previous value and the new value.")
    reviewer = st.text_input("Reviewer name *", key="rev_name")
    role = st.text_input("Reviewer role / qualification *", key="rev_role")
    ftype = st.selectbox("Finding type", list(REVIEWABLE))
    path, id_key, label_key, fields = REVIEWABLE[ftype]
    items = get(f"/projects/{pid}{path}")
    choice = st.selectbox("Finding", [f"{i[id_key]} - {str(i[label_key])[:90]}" for i in items])
    item = next(i for i in items if choice.startswith(i[id_key] + " "))
    st.json({k: item[k] for k in fields + ["verification_status"] if k in item}, expanded=False)
    verification = st.selectbox("Verification status", ["(no change)", "verified", "rejected", "unverified"])
    field = st.selectbox("Correct field", ["(none)"] + fields)
    new_value = st.text_input("New value")
    comment = st.text_area("Professional comment")
    if st.button("Save finding review"):
        upd = {"finding_type": ftype, "finding_id": item[id_key], "comment": comment or None}
        if verification != "(no change)":
            upd["verification_status"] = verification
        if field != "(none)":
            upd["field"], upd["new_value"] = field, new_value
        try:
            res = post(f"/projects/{pid}/review", json={"reviewer_name": reviewer, "reviewer_role": role,
                                                        "action": "comment", "comments": comment or None,
                                                        "finding_updates": [upd]})
            st.session_state["flash"] = f"Finding review saved. Review state: {nice(res['review_state'])}"
            st.rerun()
        except ApiError as exc:
            st.error(str(exc))

with tab_decision:
    st.info("Approval here records a professional review of a PRELIMINARY screening. It is never a regulatory "
            "approval, permit or EIA determination.")
    reviewer = st.text_input("Reviewer name *", key="dec_name")
    role = st.text_input("Reviewer role / qualification *", key="dec_role")
    org = st.text_input("Organisation", key="dec_org")
    action = st.selectbox("Action", ["approve", "request_changes", "request_further_analysis", "reject",
                                     "submit_for_review", "issue_final", "comment"], format_func=nice)
    comments = st.text_area("Comments")
    unresolved = st.text_area("Unresolved issues (one per line)")
    confirm = st.checkbox("I confirm that issuing this document does not constitute regulatory approval, an "
                          "environmental permit or an EIA determination") if action == "issue_final" else False
    if st.button("Submit decision", type="primary"):
        try:
            res = post(f"/projects/{pid}/review", json={
                "reviewer_name": reviewer, "reviewer_role": role, "reviewer_organisation": org or None,
                "action": action, "comments": comments or None,
                "unresolved_issues": [u.strip() for u in unresolved.splitlines() if u.strip()],
                "confirm_not_regulatory_approval": confirm})
            st.session_state["flash"] = f"Recorded. New state: {nice(res['review_state'])}. {res['notice']}"
            st.rerun()
        except ApiError as exc:
            st.error(str(exc))

with tab_history:
    reviews = get(f"/projects/{pid}/reviews")
    if reviews:
        st.dataframe(pd.DataFrame([{"When": r["created_at"][:19], "Reviewer": r["reviewer_name"], "Role": r["reviewer_role"],
                                    "Action": nice(r["action"]), "From": nice(r["previous_state"]),
                                    "To": nice(r["new_state"]), "Comments": r["comments"],
                                    "Unresolved": "; ".join(r["unresolved_issues"])} for r in reviews]),
                     hide_index=True, use_container_width=True)
    st.markdown("**Audit trail**")
    st.dataframe(pd.DataFrame([{"When": a["timestamp"][:19], "Actor": a["actor"], "Type": a["actor_type"],
                                "Action": a["action"], "Entity": f"{a['entity_type']} {a['entity_id'] or ''}",
                                "Field": a["field"], "Previous": str(a["previous_value"])[:60],
                                "New": str(a["new_value"])[:60], "Comment": a["comment"]}
                               for a in get(f"/projects/{pid}/audit")]), hide_index=True, use_container_width=True)
    st.markdown("**Assessment history**")
    st.dataframe(pd.DataFrame(get(f"/projects/{pid}/history")["runs"]), hide_index=True, use_container_width=True)
