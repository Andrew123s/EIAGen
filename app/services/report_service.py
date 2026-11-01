"""Preliminary screening report: structured content + DOCX, PDF and Markdown export.

The report always carries the mandatory disclaimer, the generation date, the
knowledge-base version and the review status, and is labelled as a
preliminary AI-assisted screening - never as an approved EIA.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import RegulatoryDocument, Report, ScreeningRun
from app.schemas import DISCLAIMER, OUTCOME_LABELS, ReportSection, ReviewState, ScreeningOutcome, ScreeningReport
from app.services.findings_service import load_findings
from app.services.jurisdictions import collection_relationships, get_jurisdiction

REVIEW_LABELS = {
    "draft": "Draft",
    "ai_analysis_completed": "AI analysis completed",
    "awaiting_human_review": "Awaiting human review",
    "changes_requested": "Changes requested",
    "human_reviewed": "Human reviewed (preliminary report approved for issuance by reviewer)",
    "final_preliminary_report_issued": "Final preliminary report issued",
}


def _t(value: Any) -> str:
    if value is None or value == "" or value == []:
        return "-"
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    if isinstance(value, float):
        return f"{value:g}"
    return str(value).replace("_", " ") if isinstance(value, str) and "_" in value and " " not in value else str(value)


def _table(columns: list[str], rows: list[list[Any]]) -> dict[str, Any]:
    return {"columns": columns, "rows": [[_t(c) for c in r] for r in rows]}


def build_report(db: Session, run: ScreeningRun, report_version: int) -> ScreeningReport:
    f = load_findings(db, run)
    project, profile, assessment = f["project"], f["profile"], f["assessment"]
    jcfg = get_jurisdiction(project.jurisdiction) or {"name": project.jurisdiction}
    reg = assessment.get("regulatory_screening", {})
    risk = assessment.get("environmental_risk_screening", {})
    S: list[ReportSection] = []

    def sec(title: str, paragraphs: list[str] | None = None, table: dict | None = None) -> None:
        S.append(ReportSection(number=len(S) + 1, title=title, paragraphs=paragraphs or [], table=table))

    sec("Cover page", [
        f"Preliminary Environmental Screening Report - {project.name}",
        f"Developer: {project.developer or 'not stated'}",
        f"Jurisdiction: {jcfg.get('name')}" + (f" / {project.region}" if project.region else ""),
        f"Screening version: {run.version} | Report version: {report_version}",
        f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        f"Regulatory knowledge-base version: {run.knowledge_base_version}",
        f"Taxonomy {run.taxonomy_version} | Rules {run.rules_version} | LLM provider: {run.llm_provider}",
        f"Review status: {REVIEW_LABELS.get(run.review_state, run.review_state)}",
        "STATUS: PRELIMINARY - AI-ASSISTED SCREENING - NOT AN APPROVED EIA",
        DISCLAIMER,
    ])
    sec("Executive summary", [f["narrative"].get("executive_summary", "Not available."),
                              f"(Summary source: {f['narrative'].get('source', 'deterministic_template').replace('_', ' ')})"])
    sec("Project background", [
        f"Sector: {_t((profile.get('sector') or {}).get('value')) } ({_t((profile.get('sector') or {}).get('status'))})",
        f"Development phase: {project.phase or 'not stated'}",
        f"Objectives: {project.objectives or 'not stated'}",
    ])
    params = profile.get("parameters", [])
    sec("Project description", [project.description],
        _table(["Parameter", "Value", "Unit", "Source text", "Status", "Provenance"],
               [[p["label"], p["value"], p["unit"], p.get("source_text"), p["status"], p["provenance"]] for p in params]))
    coords = f"{project.latitude:.5f}, {project.longitude:.5f}" if project.latitude is not None else "not supplied"
    sec("Project location", [f"Location: {project.location or 'not stated'}", f"Region: {project.region or 'not stated'}",
                             f"Coordinates: {coords}",
                             f"Environmental setting (as supplied): {project.environmental_setting or 'not described'}"]
        + profile.get("geospatial_notes", []))
    sec("Project activities", ["Activities identified from the information supplied; they are interpretations, "
                               "not verified facts."],
        _table(["ID", "Activity", "Phases", "Status", "Provenance", "Evidence", "Verification"],
               [[a["activity_id"], a["name"], a["phases"], a["status"], a["provenance"],
                 (a.get("matched_text") or ["-"])[0][:120], a["verification_status"]] for a in f["activities"]]))
    rels = collection_relationships(project.jurisdiction)
    sec("Regulatory jurisdiction", [
        f"Selected jurisdiction: {jcfg.get('name')}.",
        "Competent authorities (configured): " + "; ".join(f"{a['name']} - {a['role']}" for a in jcfg.get("authorities", [])),
        "Assessment framework(s): " + "; ".join(jcfg.get("assessment_frameworks", [])),
        "Collections searched: " + ", ".join(f"{k} ({v})" for k, v in rels.items())
        + ". Reference collections are non-binding international frameworks; supranational collections apply "
          "through national transposition.",
    ])
    docs = db.scalars(select(RegulatoryDocument).where(RegulatoryDocument.jurisdiction.in_(list(rels)),
                                                       RegulatoryDocument.status == "active")
                      .order_by(RegulatoryDocument.jurisdiction, RegulatoryDocument.document_id)).all()
    sec("Applicable regulatory framework", [
        "Documents in the knowledge base for this jurisdiction. Their presence does not establish applicability."],
        _table(["Document", "Authority", "Type", "Relationship", "Content form", "Version", "Legal status"],
               [[d.title, d.issuing_authority, d.document_type, rels.get(d.jurisdiction), d.content_form, d.version,
                 d.legal_status] for d in docs]))
    sec("Potential environmental and social receptors", [],
        _table(["Receptor", "Component", "Sensitivity", "Presence confirmed", "Basis"],
               [[r["name"], r["component"], r["sensitivity"], "yes" if r["presence_confirmed"] else "no", r["basis"]]
                for r in profile.get("receptors", [])]))
    imps = f["impacts"]
    sec("Potential environmental impacts", ["All impacts are potential impacts identified for further investigation, "
                                            "not confirmed impacts."],
        _table(["ID", "Potential impact", "Component", "Activities", "Receptor", "Pathway", "Phases"],
               [[i["impact_id"], i["potential_impact"], i["environmental_component"], i["activity_ids"],
                 i["potential_receptor"], i["impact_pathway"], i["project_phases"]] for i in imps]))
    sec("Preliminary impact assessment", [
        f"Methodology {risk.get('methodology_version')}: {risk.get('methodology_description')}",
        "Counts: " + ", ".join(f"{k} {v}" for k, v in (risk.get("counts") or {}).items()),
    ], _table(["ID", "Likelihood", "Severity", "Sensitivity", "Score", "Priority", "Confidence", "Extent",
               "Duration", "Reversibility", "Verification"],
              [[i["impact_id"], i["likelihood"], i["severity"], i["receptor_sensitivity"], i.get("risk_score"),
                i.get("risk_priority") or ("beneficial" if i.get("beneficial") else "-"), i["confidence"],
                i["geographical_extent"], i["duration"], i["reversibility"], i["verification_status"]] for i in imps]))
    sec("Potential regulatory requirements", [
        "Retrieved provisions with applicability status. Retrieval is not an authoritative legal interpretation; "
        "every item requires verification."],
        _table(["ID", "Source", "Provision", "Potential obligation", "Permit/approval", "Applicability", "Binding",
                "Citations", "Verification"],
               [[r["requirement_id"], r["regulatory_source"], r["relevant_provision"], r["potential_obligation"],
                 r.get("permit_or_approval"), r["applicability"], "yes" if r["binding"] else "no",
                 r["supporting_evidence"], r["verification_status"]] for r in f["requirements"]]))
    outcome_labels = [OUTCOME_LABELS[ScreeningOutcome(o)] for o in assessment.get("outcomes", [])]
    sec("Preliminary screening outcome", [
        "Preliminary outcome: " + "; ".join(outcome_labels) + ".",
        "A. Regulatory screening (statutory criteria): " + reg.get("summary", ""),
        *reg.get("notes", []),
        "B. Environmental risk screening is reported in section 11 and is kept separate from statutory "
        "classification.",
        *[f"Rationale: {r}" for r in assessment.get("rationale", [])],
    ], _table(["Criterion", "Title", "Result", "Explanation", "Criterion verification", "Evidence"],
              [[c["criterion_id"], c["title"], c["result"], c["explanation"], c["verification_status"], c["evidence"]]
               for c in reg.get("criteria", [])]))
    sec("Information gaps", [], _table(["Importance", "Missing information", "Clarification question"],
                                       [[m["importance"], m["description"], m["clarification_question"]]
                                        for m in f["missing_information"]]))
    sec("Recommended further investigations", [],
        _table(["ID", "Investigation", "Priority", "Reason", "Related impacts"],
               [[i["investigation_id"], i["name"], i["priority"], i["reason"], i["related_impacts"]]
                for i in f["investigations"]]))
    sec("Preliminary mitigation considerations", [
        "Organised by the mitigation hierarchy (avoid, minimise, restore, offset). Measures are considerations; "
        "they do not demonstrate that any impact is adequately controlled."],
        _table(["ID", "Impact", "Level", "Measure", "Type", "Verification"],
               [[m["measure_id"], m["impact_id"], m["hierarchy_level"], m["measure"], m["measure_type"],
                 m["verification_status"]] for m in f["mitigation"]]))
    sec("Evidence and references", ["Each citation maps to a stored passage; 'verified' means the citation was "
                                    "matched to the stored passage, not that the legal content was verified."],
        _table(["Key", "Source", "Section", "Page", "Version", "Citation check", "Passage (excerpt)"],
               [[e["citation_key"], e["title"], e.get("section"), e.get("page"), e.get("version"),
                 "verified" if e.get("verified") else "FAILED", (e.get("passage") or "")[:220]] for e in f["evidence"]]))
    issues = f["validation"].get("issues", [])
    sec("Assumptions and limitations", [
        *assessment.get("limitations", []),
        *[f"Risk methodology assumption: {a}" for a in risk.get("assumptions", [])],
        *[f"Risk methodology limitation: {a}" for a in risk.get("limitations", [])],
        f"Validation: {f['validation'].get('citations_valid', 0)}/{f['validation'].get('citations_checked', 0)} "
        f"citations verified; {sum(1 for i in issues if i['severity'] == 'error')} errors, "
        f"{sum(1 for i in issues if i['severity'] == 'warning')} warnings.",
        *[f"Unsupported item flagged: {u}" for u in f["validation"].get("unsupported_claims", [])],
    ])
    unresolved = [u for r in f["reviews"] for u in r.get("unresolved_issues", [])]
    sec("Human review and verification status", [
        f"Current review state: {REVIEW_LABELS.get(run.review_state, run.review_state)}.",
        "Human review is mandatory. Approval of this preliminary report by a reviewer is a professional review step "
        "within EIAGen; it is not a regulatory approval, environmental permit or EIA determination.",
        *[f"{r['created_at'][:16]} - {r['reviewer_name']} ({r['reviewer_role']}): {r['action']} "
          f"[{r['previous_state']} -> {r['new_state']}]" + (f" - {r['comments']}" if r.get("comments") else "")
          for r in f["reviews"]],
        *([f"Unresolved issue: {u}" for u in unresolved] or ["No unresolved issues recorded."]),
    ])
    return ScreeningReport(
        report_title=f"Preliminary Environmental Screening Report - {project.name}", project_id=project.id,
        project_name=project.name, screening_version=run.version, report_version=report_version,
        generated_at=datetime.now(timezone.utc), jurisdiction=project.jurisdiction,
        knowledge_base_version=run.knowledge_base_version or "unknown", taxonomy_version=run.taxonomy_version or "",
        rules_version=run.rules_version or "", llm_provider=run.llm_provider or "", review_state=ReviewState(run.review_state),
        sections=S)


# ------------------------------------------------------------- renderers


def risk_matrix_png(impacts: list[dict], path: Path) -> Path | None:
    scored = [i for i in impacts if not i.get("beneficial")]
    if not scored:
        return None
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    L = ["unlikely", "possible", "likely"]
    Sv = ["minor", "moderate", "major"]
    grid = [[0] * 3 for _ in range(3)]
    for i in scored:
        grid[L.index(i["likelihood"])][Sv.index(i["severity"])] += 1
    fig, ax = plt.subplots(figsize=(4.2, 3.4), dpi=150)
    colours = [[(l + 1) * (s + 1) for s in range(3)] for l in range(3)]
    ax.imshow(colours, cmap="YlOrRd", vmin=0, vmax=10, origin="lower")
    for li in range(3):
        for si in range(3):
            ax.text(si, li, str(grid[li][si]), ha="center", va="center", fontsize=12, fontweight="bold")
    ax.set_xticks(range(3), Sv)
    ax.set_yticks(range(3), L)
    ax.set_xlabel("Severity")
    ax.set_ylabel("Likelihood")
    ax.set_title("Potential impacts by likelihood x severity\n(EIAGen-RS-1, before sensitivity)", fontsize=8)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
    return path


def render_markdown(report: ScreeningReport, path: Path) -> Path:
    lines = [f"# {report.report_title}", "", f"**{report.status_label}**", "", f"> {report.disclaimer}", ""]
    for s in report.sections:
        lines += [f"## {s.number}. {s.title}", ""]
        lines += [p + "\n" for p in s.paragraphs]
        if s.table and s.table["rows"]:
            cols = s.table["columns"]
            lines.append("| " + " | ".join(cols) + " |")
            lines.append("|" + "---|" * len(cols))
            for row in s.table["rows"]:
                lines.append("| " + " | ".join(c.replace("|", "/").replace("\n", " ") for c in row) + " |")
            lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def render_docx(report: ScreeningReport, path: Path, figure: Path | None) -> Path:
    from docx import Document
    from docx.enum.section import WD_ORIENT
    from docx.shared import Pt, RGBColor, Cm

    doc = Document()
    section = doc.sections[0]
    section.orientation = WD_ORIENT.LANDSCAPE
    section.page_width, section.page_height = section.page_height, section.page_width
    for m in ("left_margin", "right_margin", "top_margin", "bottom_margin"):
        setattr(section, m, Cm(1.6))
    style = doc.styles["Normal"]
    style.font.name = "Calibri"
    style.font.size = Pt(9.5)
    header = section.header.paragraphs[0]
    header.text = f"{report.status_label} | KB {report.knowledge_base_version}"
    for s in report.sections:
        if s.number == 1:
            doc.add_heading(report.report_title, level=0)
            banner = doc.add_paragraph()
            run = banner.add_run(report.status_label)
            run.bold = True
            run.font.color.rgb = RGBColor(0xB0, 0x30, 0x00)
            for p in s.paragraphs[1:-1]:
                doc.add_paragraph(p)
            disc = doc.add_paragraph()
            r = disc.add_run("Mandatory disclaimer: " + report.disclaimer)
            r.bold = True
            doc.add_page_break()
            continue
        doc.add_heading(f"{s.number}. {s.title}", level=1)
        for p in s.paragraphs:
            doc.add_paragraph(p)
        if s.number == 11 and figure is not None:
            doc.add_picture(str(figure), width=Cm(9))
        if s.table and s.table["rows"]:
            cols = s.table["columns"]
            table = doc.add_table(rows=1, cols=len(cols))
            table.style = "Table Grid"
            for i, c in enumerate(cols):
                cell = table.rows[0].cells[i]
                cell.text = c
                cell.paragraphs[0].runs[0].bold = True
            for row in s.table["rows"]:
                cells = table.add_row().cells
                for i, c in enumerate(row):
                    cells[i].text = c
            for row in table.rows:
                for cell in row.cells:
                    for par in cell.paragraphs:
                        for run_ in par.runs:
                            run_.font.size = Pt(7.5)
        elif s.table is not None:
            doc.add_paragraph("No entries.")
    footer = section.footer.paragraphs[0]
    footer.text = "AI-assisted preliminary screening - requires verification by qualified practitioners."
    doc.save(str(path))
    return path


def render_pdf(report: ScreeningReport, path: Path, figure: Path | None) -> Path:
    from xml.sax.saxutils import escape

    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.platypus import Image, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    styles = getSampleStyleSheet()
    body = ParagraphStyle("body", parent=styles["BodyText"], fontSize=9, leading=11.5)
    cell = ParagraphStyle("cell", parent=body, fontSize=6.8, leading=8.2)
    head = ParagraphStyle("head", parent=cell, fontName="Helvetica-Bold")
    banner = ParagraphStyle("banner", parent=body, fontName="Helvetica-Bold", textColor=colors.HexColor("#B03000"), fontSize=11)
    disc = ParagraphStyle("disc", parent=body, fontName="Helvetica-Bold", backColor=colors.HexColor("#FFF4E5"),
                          borderColor=colors.HexColor("#B03000"), borderWidth=0.8, borderPadding=6)
    page = landscape(A4)
    story: list = []

    def footer(canvas, _doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.drawString(1.5 * cm, 0.8 * cm, f"{report.status_label} | KB {report.knowledge_base_version} | "
                                              f"Report v{report.report_version}")
        canvas.drawRightString(page[0] - 1.5 * cm, 0.8 * cm, f"Page {canvas.getPageNumber()}")
        canvas.restoreState()

    for s in report.sections:
        if s.number == 1:
            story += [Paragraph(escape(report.report_title), styles["Title"]), Paragraph(escape(report.status_label), banner),
                      Spacer(1, 8)]
            story += [Paragraph(escape(p), body) for p in s.paragraphs[1:-1]]
            story += [Spacer(1, 14), Paragraph("Mandatory disclaimer: " + escape(report.disclaimer), disc), PageBreak()]
            continue
        story.append(Paragraph(f"{s.number}. {escape(s.title)}", styles["Heading2"]))
        story += [Paragraph(escape(p), body) for p in s.paragraphs]
        if s.number == 11 and figure is not None:
            story.append(Image(str(figure), width=8 * cm, height=6.5 * cm))
        if s.table and s.table["rows"]:
            cols = s.table["columns"]
            data = [[Paragraph(escape(c), head) for c in cols]] + \
                   [[Paragraph(escape(c[:600]), cell) for c in row] for row in s.table["rows"]]
            width = page[0] - 3 * cm
            t = Table(data, colWidths=[width / len(cols)] * len(cols), repeatRows=1)
            t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.3, colors.grey),
                                   ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E6EEF3")),
                                   ("VALIGN", (0, 0), (-1, -1), "TOP")]))
            story.append(t)
        elif s.table is not None:
            story.append(Paragraph("No entries.", body))
        story.append(Spacer(1, 6))
    doc = SimpleDocTemplate(str(path), pagesize=page, leftMargin=1.5 * cm, rightMargin=1.5 * cm, topMargin=1.4 * cm,
                            bottomMargin=1.4 * cm, title=report.report_title, author="EIAGen (AI-assisted, preliminary)")
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return path


def generate_report(db: Session, run: ScreeningRun) -> Report:
    settings = get_settings()
    version = (db.scalar(select(func.max(Report.version)).where(Report.project_id == run.project_id)) or 0) + 1
    content = build_report(db, run, version)
    out_dir = settings.reports_dir / f"project_{run.project_id}"
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"eiagen_screening_p{run.project_id}_s{run.version}_r{version}"
    impacts = load_findings(db, run)["impacts"]
    figure = risk_matrix_png(impacts, out_dir / f"{stem}_risk_matrix.png")
    md = render_markdown(content, out_dir / f"{stem}.md")
    docx = render_docx(content, out_dir / f"{stem}.docx", figure)
    pdf = render_pdf(content, out_dir / f"{stem}.pdf", figure)
    report = Report(project_id=run.project_id, screening_run_id=run.id, version=version,
                    review_state_at_generation=run.review_state, knowledge_base_version=run.knowledge_base_version,
                    content_json=content.model_dump(mode="json"), docx_path=str(docx), pdf_path=str(pdf),
                    markdown_path=str(md))
    db.add(report)
    db.flush()
    return report
