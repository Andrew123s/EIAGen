"""EIAGen Streamlit front end - Dashboard.

Run:  streamlit run frontend/streamlit_app.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from api_client import get, nice, page_header  # noqa: E402

page_header("Dashboard", "AI-assisted environmental compliance and preliminary EIA screening")

stats = get("/stats")
kb = stats["knowledge_base"]
c1, c2, c3, c4 = st.columns(4)
c1.metric("Registered projects", stats["projects"])
c2.metric("Completed assessments", stats["completed_assessments"])
c3.metric("Pending reviews", stats["pending_reviews"])
c4.metric("Regulatory documents", kb["documents"], help=f"{kb['chunks']} indexed passages")

left, right = st.columns([3, 2])
with left:
    st.subheader("Projects")
    projects = get("/projects")
    if projects:
        df = pd.DataFrame([{"ID": p["id"], "Project": p["name"], "Jurisdiction": p["jurisdiction"],
                            "Sector": nice(p["sector"]), "Review state": nice(p["review_state"]),
                            "Latest screening": p["latest_screening_version"] or "-"} for p in projects])
        st.dataframe(df, hide_index=True, use_container_width=True)
    else:
        st.info("No projects yet - register one on the **New Project** page.")
    st.subheader("Recent screening activity")
    if stats["recent_activity"]:
        st.dataframe(pd.DataFrame([{"Project": a["project"], "Version": a["version"], "Status": a["status"],
                                    "Review state": nice(a["review_state"]), "Started": a["started_at"][:19]}
                                   for a in stats["recent_activity"]]), hide_index=True, use_container_width=True)
    else:
        st.caption("No screenings yet.")

with right:
    st.subheader("Regulatory knowledge base")
    st.caption(f"Version **{kb['knowledge_base_version']}**")
    st.bar_chart(pd.Series(kb["active_by_jurisdiction"], name="active documents"))
    st.write("By status:", kb["by_status"])
    st.write("By content form:", kb["by_content_form"])
    if kb["by_content_form"].get("curated_summary"):
        st.info("The seed knowledge base consists of curated summaries, not official texts. Citations to them are "
                "flagged for verification. Add official documents on the **Regulatory Library** page.")

st.divider()
st.markdown(
    "**Workflow:** New Project → Project Analysis (review & correct the extracted profile, run screening) → "
    "Environmental Impacts → Regulatory Requirements → Screening Results → Report Review (human approval).")
