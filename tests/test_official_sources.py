"""Official-text ingestion, OCR sidecars and summary -> official crosswalk.

The real official PDFs are not committed, so these tests use small fixture PDFs
that contain the crosswalk anchor phrases (synthetic text, not the real law).
"""

import json

import pymupdf

from app.agents.orchestrator import run_screening
from app.schemas import RegulationMetadata
from app.services.crosswalk import crosswalk, official_passage_for
from app.services.document_processor import extract_pages
from app.services.regulatory_library import ingest_file, update_metadata
from tests.conftest import make_pdf, make_project

FIXTURE_UVPG = [
    "§ 7 Vorprüfung bei Neuvorhaben\n(1) Bei einem Neuvorhaben, das in Anlage 1 Spalte 2 mit dem Buchstaben A gekennzeichnet ist, "
    "führt die zuständige Behörde eine allgemeine Vorprüfung zur Feststellung der UVP-Pflicht durch.",
    "Anlage 1\n1.6 Errichtung und Betrieb einer Windfarm mit Anlagen mit einer Gesamthöhe von jeweils mehr als 50 Metern mit\n"
    "1.6.1 20 oder mehr Windkraftanlagen, X\n1.6.2 6 bis weniger als 20 Windkraftanlagen, A\n1.6.3 3 bis weniger als 6 Windkraftanlagen; S",
    "§ 2 Begriffsbestimmungen\n(1) Schutzgüter im Sinne dieses Gesetzes sind Menschen, Tiere, Pflanzen und die biologische "
    "Vielfalt, Fläche, Boden, Wasser, Luft, Klima und Landschaft.",
    "Anlage 3\nKriterien für die Vorprüfung im Rahmen einer Umweltverträglichkeitsprüfung: Merkmale der Vorhaben, Standort "
    "der Vorhaben und Art und Merkmale der möglichen Auswirkungen.",
]


def _ingest_fixture_uvpg(db, tmp_path):
    pdf = make_pdf(tmp_path / "uvpg_fixture.pdf", FIXTURE_UVPG)
    meta = RegulationMetadata(document_id="de-uvpg-official", title="UVPG (fixture)", issuing_authority="Test",
                              jurisdiction="germany", document_type="legislation", version="fixture")
    doc = ingest_file(db, meta, pdf)
    update_metadata(db, "de-uvpg", {"official_counterpart": "de-uvpg-official"}, "tester")
    return doc


def test_crosswalk_entries_cover_existing_summary_sections(kb):
    from app.models import RegulatoryDocument

    for summary_id, entry in crosswalk().items():
        doc = kb.query(RegulatoryDocument).filter_by(document_id=summary_id).one()
        sections = {c.section for c in doc.chunks}
        for section in entry["sections"]:
            assert section in sections, f"{summary_id}: crosswalk section {section!r} not in summary"


def test_summary_passage_links_to_official_provision(kb, tmp_path):
    _ingest_fixture_uvpg(kb, tmp_path)
    p = official_passage_for(kb, "de-uvpg", "Annex 1, No. 1.6 - Wind farms")
    assert p is not None and p.document_id == "de-uvpg-official" and p.page == 2
    assert p.text.startswith("Windfarm mit Anlagen")
    assert official_passage_for(kb, "de-uvpg", "No such section") is None


def test_screening_cites_official_text_for_criteria_and_requirements(kb, tmp_path):
    _ingest_fixture_uvpg(kb, tmp_path)
    project = make_project(kb, name="Wind", jurisdiction="germany", region="Bayern", sector="renewable_energy",
                           description="A wind farm with 8 wind turbines, each with a total height of 200 m, plus "
                                       "access roads and foundation excavation.",
                           environmental_setting="Farmland; nearest dwelling 900 m away.")
    result = run_screening(kb, project)
    assert result.status == "completed", result.error
    keys = {e.citation_key: e for e in result.evidence}
    crit = next(c for c in result.assessment.regulatory_screening.criteria if c.criterion_id == "DE-UVPG-1.6.2")
    assert crit.result.value == "met"
    official = [keys[k] for k in crit.evidence if keys[k].document_id == "de-uvpg-official"]
    assert official and official[0].verified and official[0].page == 2
    linked = [r for r in result.requirements if r.document_id == "de-uvpg" and len(r.supporting_evidence) > 1]
    assert linked, "summary-based requirement should also cite the official provision"
    assert all(keys[k].verified for r in linked for k in r.supporting_evidence)
    assert result.validation.citations_valid == result.validation.citations_checked


def test_ocr_sidecar_used_for_scanned_pages(tmp_path):
    pdf = tmp_path / "scan.pdf"
    doc = pymupdf.open()
    doc.new_page()          # page without a text layer, as in a scan
    doc.new_page().insert_text((72, 72), "Page two has a text layer.")
    doc.save(str(pdf))
    doc.close()
    sidecar = tmp_path / "scan.pdf.ocr.json"
    sidecar.write_text(json.dumps({"engine": "test", "pages": {"1": "29. Environmental assessment (OCR text)"}}),
                       encoding="utf-8")
    pages = extract_pages(pdf)
    assert pages[0].page == 1 and "Environmental assessment" in pages[0].text
    assert "text layer" in pages[1].text


def test_ocr_document_flagged(db, tmp_path):
    pdf = tmp_path / "act.pdf"
    doc = pymupdf.open()
    doc.new_page()
    doc.save(str(pdf))
    doc.close()
    (tmp_path / "act.pdf.ocr.json").write_text(json.dumps({"pages": {
        "1": "Environmental assessment\n29. (1) A person shall not commence an undertaking without an environmental permit."}}),
        encoding="utf-8")
    meta = RegulationMetadata(document_id="gh-test-act-ocr", title="Test Act (scanned)", issuing_authority="Test",
                              jurisdiction="ghana", document_type="legislation", version="1")
    ingested = ingest_file(db, meta, pdf)
    assert ingested.extraction_method == "ocr" and ingested.chunks[0].page == 1
