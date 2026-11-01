from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from api_client import ApiError, delete, get, page_header, patch, post  # noqa: E402

page_header("Regulatory Library", "Jurisdiction-specific regulatory knowledge bases")
jurisdictions = get("/jurisdictions")
options = ["(all)"] + list(jurisdictions) + ["international"]
jur = st.sidebar.selectbox("Jurisdiction collection", options)
docs = get("/regulations", **({} if jur == "(all)" else {"jurisdiction": jur}))

st.dataframe(pd.DataFrame([{"Document ID": d["document_id"], "Title": d["title"][:80], "Jurisdiction": d["jurisdiction"],
                            "Type": d["document_type"], "Binding": d["binding"], "Content": d["content_form"],
                            "Version": d["version"], "Status": d["status"], "Legal status": d["legal_status"],
                            "Verified": d["status_verified"], "Last verified": d["date_last_verified"],
                            "Passages": d["chunk_count"]} for d in docs]), hide_index=True, use_container_width=True)

tab_inspect, tab_search, tab_upload, tab_manage = st.tabs(["Inspect", "Search", "Upload document", "Versions & status"])

with tab_inspect:
    if docs:
        did = st.selectbox("Document", [d["document_id"] for d in docs])
        d = get(f"/regulations/{did}")
        st.json(d, expanded=False)
        if d.get("source_url"):
            st.markdown(f"Official source: [{d['source_url']}]({d['source_url']})")
        for c in get(f"/regulations/{did}/chunks"):
            with st.expander(f"#{c['index']} {c['section'] or '(no section)'}" + (f" - page {c['page']}" if c["page"] else "")):
                if c["injection_flag"]:
                    st.warning("Instruction-like text detected in this passage; it is never followed.")
                st.write(c["text"])

with tab_search:
    q = st.text_input("Query", "water abstraction permit")
    j = st.selectbox("Search jurisdiction", list(jurisdictions) + ["(all collections)"])
    if st.button("Search"):
        hits = post("/regulations/search", json={"query": q, "jurisdiction": None if j.startswith("(") else j, "top_k": 8})
        for h in hits:
            st.markdown(f"**{h['score']:.2f}** · {h['title'][:80]} · {h['section'] or ''} · "
                        f"{h['jurisdiction']} ({h['relationship']}, {h['content_form']})")
            st.caption(h["text"][:500])

with tab_upload:
    st.caption("Prefer official documents from government repositories, environmental agencies and regulators. "
               "Record accurate metadata - the system never infers legal status.")
    with st.form("upload_reg"):
        f = st.file_uploader("Document (PDF, TXT, MD, DOCX)", type=["pdf", "txt", "md", "docx"])
        c1, c2 = st.columns(2)
        meta = {
            "document_id": c1.text_input("Document ID (lowercase, e.g. gh-li1652-official)"),
            "title": c2.text_input("Title"),
            "issuing_authority": c1.text_input("Issuing authority"),
            "jurisdiction": c2.selectbox("Jurisdiction", list(jurisdictions) + ["international"]),
            "document_type": c1.selectbox("Document type", ["legislation", "regulation", "guideline",
                                                            "technical_standard", "international_framework",
                                                            "non_binding_recommendation"]),
            "content_form": c2.selectbox("Content form", ["official_text", "curated_summary"]),
            "version": c1.text_input("Version"),
            "publication_date": c2.text_input("Publication date") or None,
            "effective_date": c1.text_input("Effective date") or None,
            "source_url": c2.text_input("Official source URL") or None,
            "legal_status": c1.text_input("Legal status (as verified)") or None,
            "date_last_verified": c2.text_input("Date last verified") or None,
            "supersedes": c1.text_input("Supersedes document ID (optional)") or None,
        }
        actor = st.text_input("Your name *")
        if st.form_submit_button("Ingest"):
            if f is None:
                st.error("Choose a file")
            else:
                try:
                    res = post("/regulations/ingest", files={"file": (f.name, f.getvalue())},
                               data={"metadata": json.dumps(meta), "actor": actor or "administrator"})
                    st.success(f"Ingested {res['document_id']} ({res['chunk_count']} passages)")
                except ApiError as exc:
                    st.error(str(exc))

with tab_manage:
    if docs:
        did = st.selectbox("Document to manage", [d["document_id"] for d in docs], key="manage")
        actor = st.text_input("Your name", key="manage_actor")
        c1, c2, c3 = st.columns(3)
        with c1:
            st.markdown("**Update verification metadata**")
            legal = st.text_input("Legal status")
            verified_on = st.text_input("Date last verified (YYYY-MM-DD)")
            if st.button("Save metadata"):
                try:
                    patch(f"/regulations/{did}", {"actor": actor or "administrator",
                                                  "changes": {"legal_status": legal or None, "status_verified": bool(verified_on),
                                                              "date_last_verified": verified_on or None}})
                    st.success("Saved")
                except ApiError as exc:
                    st.error(str(exc))
        with c2:
            st.markdown("**Mark superseded**")
            by = st.selectbox("Superseded by", [d["document_id"] for d in docs if d["document_id"] != did])
            if st.button("Mark superseded"):
                try:
                    post(f"/regulations/{did}/status", json={"actor": actor or "administrator", "status": "superseded",
                                                             "superseded_by": by})
                    st.success("Marked superseded")
                except ApiError as exc:
                    st.error(str(exc))
        with c3:
            st.markdown("**Remove obsolete document**")
            hard = st.checkbox("Delete permanently (only if never cited)")
            if st.button("Withdraw / delete"):
                try:
                    st.success(str(delete(f"/regulations/{did}", hard=hard, actor=actor or "administrator")))
                except ApiError as exc:
                    st.error(str(exc))
