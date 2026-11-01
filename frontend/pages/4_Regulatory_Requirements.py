from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from api_client import get, nice, page_header, select_project  # noqa: E402

page_header("Regulatory Requirements", "Retrieved provisions and their applicability - retrieval is not legal interpretation")
project = select_project(require_screening=True)
if not project:
    st.stop()
pid = project["id"]
reqs = get(f"/projects/{pid}/regulations")
evidence = {e["citation_key"]: e for e in get(f"/projects/{pid}/evidence")}

status_filter = st.multiselect("Applicability", ["potentially_applicable", "requires_clarification",
                                                 "not_established", "not_applicable_verified"],
                               default=["potentially_applicable", "requires_clarification"], format_func=nice)
rows = [r for r in reqs if r["applicability"] in status_filter]
st.dataframe(pd.DataFrame([{
    "ID": r["requirement_id"], "Source": r["regulatory_source"][:70], "Provision": r["relevant_provision"],
    "Potential obligation": r["potential_obligation"], "Permit / approval": r.get("permit_or_approval") or "-",
    "Applicability": nice(r["applicability"]), "Binding": "yes" if r["binding"] else "no (reference)",
    "Relationship": r["jurisdiction_relationship"], "Authority": r["issuing_authority"][:50],
    "Citations": ", ".join(r["supporting_evidence"]), "Verification": nice(r["verification_status"]),
} for r in rows]), hide_index=True, use_container_width=True)

permits = sorted({r["permit_or_approval"] for r in reqs if r.get("permit_or_approval")})
if permits:
    st.subheader("Potential permits and approvals (to be confirmed)")
    for p in permits:
        st.markdown(f"- {p}")

st.subheader("Requirement detail and supporting passage")
if rows:
    choice = st.selectbox("Requirement", [f"{r['requirement_id']} - {r['relevant_provision']}" for r in rows])
    r = next(x for x in rows if choice.startswith(x["requirement_id"]))
    c1, c2 = st.columns(2)
    with c1:
        st.markdown(f"**Source:** {r['regulatory_source']}")
        st.markdown(f"**Issuing authority:** {r['issuing_authority']}")
        st.markdown(f"**Jurisdiction:** {r['jurisdiction']} ({r['jurisdiction_relationship']})")
        st.markdown(f"**Document type:** {nice(r['document_type'])} · **Content:** {nice(r['content_form'])}")
        st.markdown(f"**Legal status:** {r.get('legal_status') or 'unknown'}")
        if r.get("source_url"):
            st.markdown(f"**Official source:** [{r['source_url']}]({r['source_url']})")
        st.markdown(f"**Related activities:** {nice(r['related_activities']) or '-'}")
        st.markdown("**Uncertainty:**")
        for u in r["uncertainty"]:
            st.markdown(f"- {u}")
    with c2:
        for key in r["supporting_evidence"]:
            e = evidence.get(key)
            if not e:
                continue
            st.markdown(f"**[{key}]** {e['title']}, {e.get('section') or ''}"
                        + (f", page {e['page']}" if e.get("page") else "")
                        + f" · version {e.get('version')} · citation check: "
                        + ("✅ verified" if e["verified"] else "❌ failed"))
            if st.button(f"Show original passage for {key}"):
                full = get(f"/projects/{pid}/evidence/{key}")
                st.text_area("Original stored passage", full["original_passage"], height=220)
            else:
                st.info(e["passage"])
else:
    st.caption("No requirements match the filter.")
