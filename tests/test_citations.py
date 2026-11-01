from app.agents.orchestrator import run_screening
from app.schemas import DocumentStatus
from app.services.citation_service import validate_citation
from app.services.regulatory_library import set_status
from app.schemas import EvidenceReference
from tests.conftest import make_project


def _run(kb):
    project = make_project(kb)
    result = run_screening(kb, project)
    assert result.status == "completed", result.error
    return project, result


def test_citation_keys_and_metadata(kb):
    _, result = _run(kb)
    assert result.evidence
    for e in result.evidence:
        assert e.citation_key.split("-")[0] in ("REG", "GUIDE", "DOC", "INTL")
        if e.source_type == "regulatory":
            assert e.chunk_id is not None and e.version and e.jurisdiction
    intl = [e for e in result.evidence if e.jurisdiction == "international"]
    assert all(e.citation_key.startswith("INTL-") for e in intl)
    assert result.validation.citations_valid == result.validation.citations_checked


def test_every_requirement_has_verified_citation(kb):
    _, result = _run(kb)
    keys = {e.citation_key: e for e in result.evidence}
    for req in result.requirements:
        assert req.supporting_evidence
        assert all(keys[k].verified for k in req.supporting_evidence)


def test_tampered_passage_fails(kb):
    project, result = _run(kb)
    ref = result.evidence[0].model_copy(update={"passage": "The project is exempt from all assessment."})
    res = validate_citation(kb, ref, "ghana", project.id)
    assert not res.valid and not res.checks["passage_verbatim"]


def test_wrong_page_section_version_fail(kb):
    project, result = _run(kb)
    ref = result.evidence[0]
    assert not validate_citation(kb, ref.model_copy(update={"page": 99}), "ghana", project.id).checks["page_matches"]
    assert not validate_citation(kb, ref.model_copy(update={"section": "Section 999"}), "ghana", project.id).checks["section_matches"]
    assert not validate_citation(kb, ref.model_copy(update={"version": "9999"}), "ghana", project.id).checks["version_matches"]


def test_nonexistent_document_fails(kb):
    ref = EvidenceReference(citation_key="REG-99", source_type="regulatory", document_id="gh-imaginary-act",
                            chunk_id=999999, title="Imaginary Act", passage="Invented text.", jurisdiction="ghana")
    res = validate_citation(kb, ref, "ghana")
    assert not res.valid and not res.checks["document_exists"]


def test_cross_jurisdiction_citation_inadmissible(kb):
    project, result = _run(kb)
    ghana_ref = next(e for e in result.evidence if e.jurisdiction == "ghana")
    res = validate_citation(kb, ghana_ref, "germany", project.id)
    assert not res.valid and not res.checks["jurisdiction_admissible"]


def test_withdrawn_document_flagged(kb):
    project, result = _run(kb)
    ref = next(e for e in result.evidence if e.document_id == "gh-li1652")
    set_status(kb, "gh-li1652", DocumentStatus.withdrawn, "tester")
    res = validate_citation(kb, ref, "ghana", project.id)
    assert not res.checks["document_active"]


def test_curated_summary_disclosed(kb):
    project, result = _run(kb)
    res = validate_citation(kb, result.evidence[0], "ghana", project.id)
    assert any("curated summary" in i for i in res.issues)
