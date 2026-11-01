from tests.conftest import PROJECT_PAYLOAD

REVIEWER = {"reviewer_name": "Dr. Ama Mensah", "reviewer_role": "Senior EIA Practitioner"}


def _screened(client):
    pid = client.post("/projects", json=PROJECT_PAYLOAD).json()["id"]
    assert client.post(f"/projects/{pid}/screening").status_code == 201
    return pid


def _review(client, pid, **body):
    return client.post(f"/projects/{pid}/review", json={**REVIEWER, **body})


def test_full_review_lifecycle(client):
    pid = _screened(client)
    assert client.get(f"/projects/{pid}").json()["review_state"] == "awaiting_human_review"
    r = _review(client, pid, action="request_changes", comments="Verify water use permit applicability.")
    assert r.status_code == 201 and r.json()["review_state"] == "changes_requested"
    assert "not a regulatory approval" in r.json()["notice"]
    assert _review(client, pid, action="submit_for_review").json()["review_state"] == "awaiting_human_review"
    assert _review(client, pid, action="approve", comments="Preliminary findings reasonable.").json()["review_state"] == "human_reviewed"
    no_confirm = _review(client, pid, action="issue_final")
    assert no_confirm.status_code == 400
    final = _review(client, pid, action="issue_final", confirm_not_regulatory_approval=True)
    assert final.json()["review_state"] == "final_preliminary_report_issued"
    # locked
    assert _review(client, pid, action="comment", comments="late").status_code == 409
    reviews = client.get(f"/projects/{pid}/reviews").json()
    assert [r["action"] for r in reviews] == ["request_changes", "submit_for_review", "approve", "issue_final"]
    assert all(r["reviewer_name"] == "Dr. Ama Mensah" and r["created_at"] for r in reviews)
    report = client.get(f"/projects/{pid}/report").json()["content"]
    assert report["review_state"] == "final_preliminary_report_issued"
    assert "NOT AN APPROVED EIA" in report["status_label"]
    last = report["sections"][-1]["paragraphs"]
    assert any("not a regulatory approval" in p for p in last)


def test_ai_cannot_approve_its_own_assessment(client):
    pid = _screened(client)
    for name in ("system", "AI", "EIAGen", "Claude", "LLM agent", "automated"):
        r = client.post(f"/projects/{pid}/review", json={"reviewer_name": name, "reviewer_role": "auto",
                                                         "action": "approve"})
        assert r.status_code == 403, name
    assert client.get(f"/projects/{pid}").json()["review_state"] == "awaiting_human_review"


def test_invalid_transitions(client):
    pid = _screened(client)
    assert _review(client, pid, action="issue_final", confirm_not_regulatory_approval=True).status_code == 409
    assert _review(client, pid, action="submit_for_review").status_code == 409
    assert _review(client, pid, action="request_changes").status_code == 400     # needs explanation
    assert client.post(f"/projects/{pid}/review", json={**REVIEWER, "action": "self_approve"}).status_code == 422


def test_finding_updates_are_audited(client):
    pid = _screened(client)
    impact = client.get(f"/projects/{pid}/impacts").json()[0]
    req = client.get(f"/projects/{pid}/regulations").json()[0]
    r = _review(client, pid, action="comment", comments="Field visit notes", finding_updates=[
        {"finding_type": "impact", "finding_id": impact["impact_id"], "field": "severity", "new_value": "major",
         "comment": "Downstream intake confirmed"},
        {"finding_type": "requirement", "finding_id": req["requirement_id"], "verification_status": "verified",
         "comment": "Checked against official text"},
        {"finding_type": "requirement", "finding_id": req["requirement_id"], "field": "applicability",
         "new_value": "not_applicable_verified", "comment": "Exemption confirmed by regulator letter"},
    ])
    assert r.status_code == 201, r.text
    updated = next(i for i in client.get(f"/projects/{pid}/impacts").json() if i["impact_id"] == impact["impact_id"])
    assert updated["severity"] == "major" and updated["verification_status"] == "corrected"
    updated_req = next(x for x in client.get(f"/projects/{pid}/regulations").json() if x["requirement_id"] == req["requirement_id"])
    assert updated_req["applicability"] == "not_applicable_verified" and updated_req["provenance"] == "human_review"
    audit = client.get(f"/projects/{pid}/audit").json()
    sev = next(a for a in audit if a["action"] == "finding_corrected" and a["field"] == "severity")
    assert sev["actor"] == "Dr. Ama Mensah" and sev["previous_value"] == impact["severity"] and sev["new_value"] == "major"
    assert any(a["action"] == "finding_verification_changed" for a in audit)


def test_invalid_finding_updates_rejected(client):
    pid = _screened(client)
    assert _review(client, pid, action="comment", finding_updates=[
        {"finding_type": "impact", "finding_id": "IMP-999", "field": "severity", "new_value": "major"}]).status_code == 400
    impact = client.get(f"/projects/{pid}/impacts").json()[0]
    assert _review(client, pid, action="comment", finding_updates=[
        {"finding_type": "impact", "finding_id": impact["impact_id"], "field": "impact_id", "new_value": "X"}]).status_code == 400
    req = client.get(f"/projects/{pid}/regulations").json()[0]
    assert _review(client, pid, action="comment", finding_updates=[
        {"finding_type": "requirement", "finding_id": req["requirement_id"], "field": "applicability",
         "new_value": "definitely_exempt"}]).status_code == 400


def test_edit_after_approval_invalidates_approval(client):
    pid = _screened(client)
    _review(client, pid, action="approve")
    impact = client.get(f"/projects/{pid}/impacts").json()[0]
    r = _review(client, pid, action="comment", finding_updates=[
        {"finding_type": "impact", "finding_id": impact["impact_id"], "field": "likelihood", "new_value": "likely"}])
    assert r.json()["review_state"] == "awaiting_human_review"


def test_rescreening_preserves_reviewed_history(client):
    pid = _screened(client)
    _review(client, pid, action="approve", comments="v1 ok")
    _review(client, pid, action="issue_final", confirm_not_regulatory_approval=True)
    assert client.post(f"/projects/{pid}/screening").status_code == 201
    runs = client.get(f"/projects/{pid}/history").json()["runs"]
    assert runs[0]["review_state"] == "final_preliminary_report_issued"
    assert runs[1]["review_state"] == "awaiting_human_review"
    v1 = client.get(f"/projects/{pid}/screening", params={"version": 1}).json()
    assert v1["review_state"] == "final_preliminary_report_issued"
    assert len(client.get(f"/projects/{pid}/reviews").json()) == 2
