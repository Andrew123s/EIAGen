import json

from tests.conftest import PROJECT_PAYLOAD, make_pdf


def _create(client, **overrides):
    r = client.post("/projects", json={**PROJECT_PAYLOAD, **overrides})
    assert r.status_code == 201, r.text
    return r.json()


def test_health_and_metadata(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"
    assert {"ghana", "germany", "eu"} <= set(client.get("/jurisdictions").json())
    tools = {t["name"] for t in client.get("/tools").json()}
    assert {"extract_project_profile", "identify_environmental_impacts", "search_regulatory_documents",
            "retrieve_regulatory_provisions", "evaluate_screening_criteria", "validate_citations"} <= tools
    assert client.get("/taxonomy").json()["risk_methodology"]["version"] == "EIAGen-RS-1"
    assert client.get("/openapi.json").status_code == 200


def test_project_crud_and_validation(client):
    p = _create(client)
    assert p["review_state"] == "draft"
    assert client.get(f"/projects/{p['id']}").json()["name"] == "API Test Plant"
    assert any(x["id"] == p["id"] for x in client.get("/projects").json())
    assert client.get("/projects/9999").status_code == 404
    assert client.post("/projects", json={**PROJECT_PAYLOAD, "jurisdiction": "atlantis"}).status_code == 422
    assert client.post("/projects", json={**PROJECT_PAYLOAD, "description": "short"}).status_code == 422
    assert client.post("/projects", json={**PROJECT_PAYLOAD, "unexpected": 1}).status_code == 422
    bad_coords = {**PROJECT_PAYLOAD, "coordinates": {"latitude": 123, "longitude": 0}}
    assert client.post("/projects", json=bad_coords).status_code == 422


def test_document_upload_validation(client, tmp_path):
    p = _create(client)
    ok = client.post(f"/projects/{p['id']}/documents",
                     files={"file": ("notes.txt", b"Blasting and dredging will be carried out.", "text/plain")},
                     data={"category": "technical_specification"})
    assert ok.status_code == 201 and ok.json()["characters_extracted"] > 0
    pdf = make_pdf(tmp_path / "design.pdf", ["Page one text.", "Boreholes will supply process water."])
    r = client.post(f"/projects/{p['id']}/documents", files={"file": ("design.pdf", pdf.read_bytes(), "application/pdf")})
    assert r.status_code == 201 and r.json()["page_count"] == 2
    assert client.post(f"/projects/{p['id']}/documents",
                       files={"file": ("run.exe", b"MZ...", "application/octet-stream")}).status_code == 415
    assert client.post(f"/projects/{p['id']}/documents",
                       files={"file": ("fake.pdf", b"not a pdf", "application/pdf")}).status_code == 415
    assert client.post(f"/projects/{p['id']}/documents",
                       files={"file": ("empty.txt", b"", "text/plain")}).status_code == 400


def test_upload_size_limit(client, monkeypatch):
    from app.core.config import reset_settings

    monkeypatch.setenv("EIAGEN_MAX_UPLOAD_MB", "1")
    reset_settings()
    p = _create(client)
    big = b"a" * (1024 * 1024 + 10)
    assert client.post(f"/projects/{p['id']}/documents",
                       files={"file": ("big.txt", big, "text/plain")}).status_code == 413


def test_analysis_correction_and_screening_endpoints(client):
    p = _create(client)
    pid = p["id"]
    prof = client.post(f"/projects/{pid}/analyze").json()
    assert prof["activities"] and prof["missing_information"]
    corr = client.patch(f"/projects/{pid}/profile", json={"corrected_by": "Jane Analyst",
                                                         "add_activities": ["hazardous_materials_storage"],
                                                         "remove_activities": ["workforce_employment"],
                                                         "parameters": {"production_capacity": 1500}})
    assert corr.status_code == 200, corr.text
    keys = {a["key"] for a in corr.json()["activities"]}
    assert "hazardous_materials_storage" in keys and "workforce_employment" not in keys
    assert client.patch(f"/projects/{pid}/profile", json={"corrected_by": "Jane", "add_activities": ["flying"]}).status_code == 422

    r = client.post(f"/projects/{pid}/screening")
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "completed" and body["review_state"] == "awaiting_human_review"
    assert any(s["stage"] == "project_information_extraction" and "corrected by Jane Analyst" in (s["message"] or "")
               for s in body["stages"])

    status_ = client.get(f"/projects/{pid}/screening/status").json()
    assert status_["status"] == "completed" and status_["tool_executions"]
    screening = client.get(f"/projects/{pid}/screening").json()
    assert "human_review_required" in screening["assessment"]["outcomes"]
    impacts = client.get(f"/projects/{pid}/impacts").json()
    assert impacts and all(i["status"] == "potential" for i in impacts)
    water = client.get(f"/projects/{pid}/impacts", params={"component": "surface_water"}).json()
    assert water and all(i["environmental_component"] == "surface_water" for i in water)
    reqs = client.get(f"/projects/{pid}/regulations").json()
    assert reqs and all(r["verification_required"] for r in reqs)
    evidence = client.get(f"/projects/{pid}/evidence").json()
    key = evidence[0]["citation_key"]
    inspected = client.get(f"/projects/{pid}/evidence/{key}").json()
    assert inspected["citation"]["passage"] in inspected["original_passage"]
    missing = client.get(f"/projects/{pid}/missing-information").json()
    assert missing["clarification_questions"]
    assert client.get(f"/projects/{pid}/mitigation").json()


def test_report_endpoints_and_exports(client):
    pid = _create(client)["id"]
    client.post(f"/projects/{pid}/screening")
    rep = client.get(f"/projects/{pid}/report").json()
    titles = [s["title"] for s in rep["content"]["sections"]]
    assert len(titles) == 19 and titles[0] == "Cover page" and titles[-1] == "Human review and verification status"
    assert rep["content"]["disclaimer"].startswith("This report is an AI-assisted preliminary")
    assert rep["knowledge_base_version"].startswith("kb-")
    pdf = client.get(f"/projects/{pid}/report/export", params={"format": "pdf"})
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    docx = client.get(f"/projects/{pid}/report/export", params={"format": "docx"})
    assert docx.status_code == 200 and docx.content[:2] == b"PK"
    md = client.get(f"/projects/{pid}/report/export", params={"format": "md"})
    assert "NOT AN APPROVED EIA" in md.text
    assert client.get(f"/projects/{pid}/report/export", params={"format": "exe"}).status_code == 422
    assert client.post(f"/projects/{pid}/report").json()["report_version"] == 2


def test_no_screening_yet_returns_404(client):
    pid = _create(client)["id"]
    assert client.get(f"/projects/{pid}/impacts").status_code == 404
    assert client.get(f"/projects/{pid}/report").status_code == 404
    assert client.get(f"/projects/{pid}/screening/status").json()["status"] == "not_started"


def test_history_and_audit(client):
    pid = _create(client)["id"]
    client.post(f"/projects/{pid}/screening")
    client.post(f"/projects/{pid}/screening")
    runs = client.get(f"/projects/{pid}/history").json()["runs"]
    assert [r["version"] for r in runs] == [1, 2]
    actions = {a["action"] for a in client.get(f"/projects/{pid}/audit").json()}
    assert {"project_registered", "screening_completed", "state_changed"} <= actions


def test_regulation_endpoints(client, tmp_path):
    regs = client.get("/regulations", params={"jurisdiction": "germany"}).json()
    assert len(regs) == 5 and all(r["jurisdiction"] == "germany" for r in regs)
    uvpg = client.get("/regulations/de-uvpg").json()
    assert uvpg["binding"] and uvpg["content_form"] == "curated_summary"
    ifc = client.get("/regulations/intl-ifc-ps").json()
    assert ifc["binding"] is False
    assert client.get("/regulations/nope-doc").status_code == 404
    chunks = client.get("/regulations/de-uvpg/chunks").json()
    assert chunks and "section" in chunks[0]
    hits = client.post("/regulations/search", json={"query": "wind turbines", "jurisdiction": "germany"}).json()
    assert hits[0]["document_id"] == "de-uvpg"

    pdf = make_pdf(tmp_path / "std.pdf", ["Section 1 Effluent\nEffluent discharged to rivers shall meet the limits."])
    meta = {"document_id": "gh-test-effluent-std", "title": "Test Effluent Standard", "issuing_authority": "Test",
            "jurisdiction": "ghana", "document_type": "technical_standard", "version": "2019"}
    r = client.post("/regulations/ingest", files={"file": ("std.pdf", pdf.read_bytes(), "application/pdf")},
                    data={"metadata": json.dumps(meta)})
    assert r.status_code == 201, r.text
    assert r.json()["binding"] is False and r.json()["chunk_count"] >= 1
    dup = client.post("/regulations/ingest", files={"file": ("std.pdf", b"%PDF-1.4 changed", "application/pdf")},
                      data={"metadata": json.dumps({**meta, "version": "2020"})})
    assert dup.status_code in (409, 422)
    bad = client.post("/regulations/ingest", files={"file": ("std.pdf", pdf.read_bytes(), "application/pdf")},
                      data={"metadata": json.dumps({**meta, "document_type": "rumour"})})
    assert bad.status_code == 422

    patched = client.patch("/regulations/gh-test-effluent-std",
                           json={"actor": "Admin", "changes": {"legal_status": "In force", "status_verified": True}})
    assert patched.json()["status_verified"] is True
    assert client.patch("/regulations/gh-test-effluent-std",
                        json={"actor": "Admin", "changes": {"title": "x"}}).status_code == 422
    sup = client.post("/regulations/gh-act490/status",
                      json={"actor": "Admin", "status": "superseded", "superseded_by": "gh-li1652"})
    assert sup.json()["status"] == "superseded"
    withdrawn = client.delete("/regulations/gh-test-effluent-std").json()
    assert withdrawn["deleted"] is False and withdrawn["status"] == "withdrawn"
    assert client.delete("/regulations/gh-test-effluent-std", params={"hard": True}).json()["deleted"] is True


def test_hard_delete_blocked_when_cited(client):
    pid = _create(client)["id"]
    client.post(f"/projects/{pid}/screening")
    cited = client.get(f"/projects/{pid}/evidence").json()[0]["document_id"]
    assert client.delete(f"/regulations/{cited}", params={"hard": True}).status_code == 409


def test_stats(client):
    pid = _create(client)["id"]
    client.post(f"/projects/{pid}/screening")
    s = client.get("/stats").json()
    assert s["projects"] == 1 and s["completed_assessments"] == 1 and s["pending_reviews"] == 1
    assert s["knowledge_base"]["documents"] == 15


def test_api_keys_enforced(client, monkeypatch):
    from app.core.config import reset_settings

    monkeypatch.setenv("EIAGEN_API_KEY", "user-key")
    monkeypatch.setenv("EIAGEN_ADMIN_API_KEY", "admin-key")
    reset_settings()
    assert client.post("/projects", json=PROJECT_PAYLOAD).status_code == 401
    assert client.post("/projects", json=PROJECT_PAYLOAD, headers={"X-API-Key": "user-key"}).status_code == 201
    assert client.get("/projects").status_code == 200    # reads stay open
    assert client.delete("/regulations/gh-act917", headers={"X-API-Key": "user-key"}).status_code == 403
    assert client.delete("/regulations/gh-act917", headers={"X-API-Key": "admin-key"}).status_code == 200
