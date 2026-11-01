"""Thin HTTP client used by all Streamlit pages."""

from __future__ import annotations

import os
from typing import Any

import requests
import streamlit as st

API_URL = os.environ.get("EIAGEN_API_URL", "http://127.0.0.1:8000").rstrip("/")
DISCLAIMER_SHORT = ("Preliminary AI-assisted screening. Not a legally validated EIA determination, permit or "
                    "regulatory approval. All findings require verification by qualified practitioners.")


class ApiError(RuntimeError):
    pass


def _headers() -> dict[str, str]:
    key = st.session_state.get("api_key") or os.environ.get("EIAGEN_API_KEY")
    return {"X-API-Key": key} if key else {}


def _handle(r: requests.Response) -> Any:
    if r.status_code >= 400:
        try:
            detail = r.json().get("detail")
        except ValueError:
            detail = r.text
        raise ApiError(f"{r.status_code}: {detail}")
    ctype = r.headers.get("content-type", "")
    return r.json() if "application/json" in ctype else r.content


def get(path: str, **params) -> Any:
    return _handle(requests.get(f"{API_URL}{path}", params=params, headers=_headers(), timeout=60))


def post(path: str, json: Any = None, files=None, data=None, timeout: int = 600, **params) -> Any:
    return _handle(requests.post(f"{API_URL}{path}", json=json, files=files, data=data, params=params,
                                 headers=_headers(), timeout=timeout))


def patch(path: str, json: Any) -> Any:
    return _handle(requests.patch(f"{API_URL}{path}", json=json, headers=_headers(), timeout=60))


def delete(path: str, **params) -> Any:
    return _handle(requests.delete(f"{API_URL}{path}", params=params, headers=_headers(), timeout=60))


def backend_ok() -> bool:
    try:
        return get("/health").get("status") == "ok"
    except Exception:
        return False


def page_header(title: str, subtitle: str | None = None) -> None:
    st.set_page_config(page_title=f"EIAGen - {title}", page_icon="🌿", layout="wide")
    st.title(title)
    if subtitle:
        st.caption(subtitle)
    st.warning(DISCLAIMER_SHORT, icon="⚠️")
    with st.sidebar:
        st.markdown("### EIAGen")
        st.caption(f"API: {API_URL}")
        st.text_input("API key (if configured)", type="password", key="api_key")
        if not backend_ok():
            st.error("Backend not reachable. Start it with:\n\n`uvicorn app.main:app`")
            st.stop()


def select_project(require_screening: bool = False) -> dict | None:
    projects = get("/projects")
    if not projects:
        st.info("No projects registered yet. Use **New Project** to register one.")
        return None
    if require_screening:
        projects = [p for p in projects if p["latest_screening_version"]]
        if not projects:
            st.info("No project has a completed screening yet. Run one on **Project Analysis**.")
            return None
    labels = {f"#{p['id']} - {p['name']} ({p['jurisdiction']})": p for p in projects}
    ids = [p["id"] for p in projects]
    current = st.session_state.get("project_id")
    index = ids.index(current) if current in ids else 0
    choice = st.sidebar.selectbox("Project", list(labels), index=index)
    project = labels[choice]
    st.session_state["project_id"] = project["id"]
    return project


def nice(value: Any) -> str:
    if isinstance(value, list):
        return ", ".join(str(v).replace("_", " ") for v in value)
    return str(value).replace("_", " ") if value is not None else "-"
