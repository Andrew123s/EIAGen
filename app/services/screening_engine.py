"""Preliminary EIA screening engine.

Two strictly separated dimensions:

A. Regulatory screening - deterministic evaluation of the jurisdiction's
   encoded statutory criteria (config/assessment_rules.yaml). Only criteria of
   the selected jurisdiction are evaluated. Missing parameters give
   ``indeterminate``, never ``not_met``. If no criterion can be decided, the
   result says that regulatory screening cannot be determined reliably.
B. Environmental risk screening - summary of the transparent risk
   prioritisation (methodology EIAGen-RS-1). It is never used as a legal
   classification.

The final outcome is always preliminary and always requires human review.
"""

from __future__ import annotations

from app.schemas import (
    OUTCOME_LABELS,
    CriterionResult,
    MissingInformation,
    PotentialImpact,
    ProjectProfile,
    RegulatoryScreening,
    RiskScreening,
    ScreeningAssessment,
    ScreeningCriterion,
    ScreeningOutcome,
)
from app.services.taxonomy import component_label, find_keywords, rules

_OPS = {">=": lambda a, b: a >= b, ">": lambda a, b: a > b, "<=": lambda a, b: a <= b, "<": lambda a, b: a < b,
        "==": lambda a, b: a == b}


def _eval_param(cond: dict, profile: ProjectProfile) -> tuple[bool | None, str, float | None]:
    value = profile.parameter(cond["name"])
    if value is None:
        return None, f"{cond['name']} not provided", None
    if cond["op"] == "between":
        ok = cond["min"] <= value <= cond["max"]
        return ok, f"{cond['name']} = {value:g} ({'within' if ok else 'outside'} {cond['min']}-{cond['max']})", value
    ok = _OPS[cond["op"]](value, cond["value"])
    return ok, f"{cond['name']} = {value:g} ({'meets' if ok else 'does not meet'} {cond['op']} {cond['value']})", value


def evaluate_criterion(spec: dict, profile: ProjectProfile, text: str,
                       setting_described: bool) -> ScreeningCriterion:
    cond = spec.get("condition", {})
    reasons: list[str] = []
    used: dict[str, float | None] = {}
    missing: list[str] = []
    result: CriterionResult | None = None

    sector = profile.sector.value
    if "sectors_any" in cond:
        if sector is None:
            result = CriterionResult.indeterminate
            missing.append("sector")
            reasons.append("Project sector not established")
        elif sector not in cond["sectors_any"]:
            result = CriterionResult.not_met
            reasons.append(f"Sector '{sector}' not within criterion scope")
    if result is None and "activities_any" in cond:
        hits = profile.activity_keys() & set(cond["activities_any"])
        if not hits:
            result = CriterionResult.not_met
            reasons.append("No relevant project activity identified")
        else:
            reasons.append("Relevant activities: " + ", ".join(sorted(hits)))
    if result is None and "keywords_any" in cond and not find_keywords(text, cond["keywords_any"]):
        result = CriterionResult.not_met
        reasons.append("Project description does not indicate the specific project type")
    if result is None and "flags_any" in cond:
        hits = set(profile.setting_flags) & set(cond["flags_any"])
        if hits:
            reasons.append("Setting indicators: " + ", ".join(sorted(hits)))
        elif setting_described:
            result = CriterionResult.not_met
            reasons.append("No indication of a relevant sensitive area in the information supplied "
                           "(absence not verified)")
        else:
            result = CriterionResult.indeterminate
            missing.append("environmental_setting")
            reasons.append("Environmental setting not described")
    if result is None and cond.get("always_indeterminate"):
        result = CriterionResult.indeterminate
        reasons.append("Applicability depends on undertaking type / scale entries that are not encoded; "
                       "manual determination against the official text is required")
    if result is None:
        outcomes: list[bool | None] = []
        for key in ("parameter", "parameter_2"):
            if key in cond:
                ok, why, val = _eval_param(cond[key], profile)
                used[cond[key]["name"]] = val
                reasons.append(why)
                if ok is None:
                    missing.append(cond[key]["name"])
                outcomes.append(ok)
        if "any_parameter" in cond:
            any_results = []
            for sub in cond["any_parameter"]:
                ok, why, val = _eval_param(sub, profile)
                used[sub["name"]] = val
                if ok is not None:
                    reasons.append(why)
                any_results.append(ok)
            if any(r is True for r in any_results):
                outcomes.append(True)
            elif all(r is None for r in any_results):
                missing.extend(s["name"] for s in cond["any_parameter"])
                outcomes.append(None)
            else:
                outcomes.append(False if all(r is not None for r in any_results) else None)
        if any(o is False for o in outcomes):
            result = CriterionResult.not_met
        elif any(o is None for o in outcomes):
            result = CriterionResult.indeterminate
        else:
            result = CriterionResult.met

    verification = spec.get("verification_status", "unverified")
    explanation = "; ".join(reasons) or "Conditions evaluated"
    if result == CriterionResult.met and verification != "verified":
        explanation += ". Criterion not yet verified against the official text - human regulatory review required"
    return ScreeningCriterion(
        criterion_id=spec["id"], jurisdiction=spec["jurisdiction"], title=spec["title"],
        description=" ".join(spec["description"].split()), source_document_id=spec["source_document_id"],
        source_section=spec["source_section"], screening_category=spec["screening_category"],
        verification_status=verification, result=result, outcome_if_met=spec["outcome_if_met"],
        explanation=explanation, parameters_used=used, missing_parameters=sorted(set(missing)))


def regulatory_screening(profile: ProjectProfile, text: str) -> RegulatoryScreening:
    specs = [c for c in rules()["regulatory_screening_criteria"] if c["jurisdiction"] == profile.jurisdiction]
    setting_described = profile.environmental_setting.value is not None or bool(profile.setting_flags)
    criteria = [evaluate_criterion(s, profile, text, setting_described) for s in specs]
    met = [c for c in criteria if c.result == CriterionResult.met]
    decided = [c for c in criteria if c.result != CriterionResult.indeterminate]
    notes = []
    if not specs:
        summary = (f"No verified or encoded regulatory screening criteria exist for jurisdiction "
                   f"'{profile.jurisdiction}'. Regulatory screening cannot be determined reliably.")
    elif not met:
        summary = ("No encoded screening criterion was met. This is NOT a finding that no assessment or permit "
                   "is required: " + (f"{len(criteria) - len(decided)} criteria could not be decided from the "
                                      "information available." if len(decided) < len(criteria)
                                      else "the encoded criteria cover only part of the regulatory framework."))
    else:
        summary = (f"{len(met)} encoded screening criteria were met, indicating potentially relevant regulatory "
                   f"categories: " + "; ".join(dict.fromkeys(c.outcome_if_met for c in met)) + ".")
    if any(c.verification_status != "verified" for c in criteria):
        notes.append("Encoded criteria are marked 'unverified': they were transcribed from the maintainers' reading "
                     "of the legislation and have not been checked against the current official text by a "
                     "qualified reviewer.")
    notes.append("Regulatory screening is separate from environmental risk prioritisation; risk scores are never "
                 "used as a substitute for statutory criteria.")
    return RegulatoryScreening(jurisdiction=profile.jurisdiction, determinable=bool(met) or bool(decided and specs),
                               criteria=criteria, categories_identified=list(dict.fromkeys(c.outcome_if_met for c in met)),
                               summary=summary, notes=notes)


def risk_screening(impacts: list[PotentialImpact]) -> RiskScreening:
    method = rules()["risk_methodology"]
    scored = [i for i in impacts if not i.beneficial]
    counts = {"high": 0, "medium": 0, "low": 0, "beneficial": len(impacts) - len(scored)}
    comp: dict[str, str] = {}
    rank = {"low": 0, "medium": 1, "high": 2}
    for i in scored:
        counts[i.risk_priority] += 1
        label = component_label(i.environmental_component)
        if label not in comp or rank[i.risk_priority] > rank[comp[label]]:
            comp[label] = i.risk_priority
    top = [f"{i.impact_id} {i.potential_impact} (score {i.risk_score}, {i.risk_priority})"
           for i in sorted(scored, key=lambda x: -(x.risk_score or 0))[:6]]
    return RiskScreening(methodology_version=method["version"], methodology_description=" ".join(method["description"].split()),
                         counts=counts, top_risks=top, component_summary=dict(sorted(comp.items())),
                         assumptions=method["assumptions"], limitations=method["limitations"])


def assess(profile: ProjectProfile, impacts: list[PotentialImpact], text: str,
           missing: list[MissingInformation]) -> ScreeningAssessment:
    reg = regulatory_screening(profile, text)
    risk = risk_screening(impacts)
    outcomes: list[ScreeningOutcome] = []
    rationale: list[str] = []
    met = [c for c in reg.criteria if c.result == CriterionResult.met]
    if met:
        outcomes.append(ScreeningOutcome.regulatory_category_identified)
        rationale.append("Encoded criteria met: " + ", ".join(c.criterion_id for c in met))
    eia_like = [c for c in met if c.screening_category in ("environmental_impact_assessment", "preliminary_assessment")]
    if risk.counts["high"] or eia_like:
        outcomes.append(ScreeningOutcome.further_assessment_potentially_warranted)
        rationale.append(f"{risk.counts['high']} high-priority potential impacts identified"
                         + (f"; EIA-related screening categories potentially relevant ({len(eia_like)})" if eia_like else ""))
    critical = [m for m in missing if m.importance == "critical"]
    indeterminate_missing = [c for c in reg.criteria if c.result == CriterionResult.indeterminate and c.missing_parameters]
    if critical or indeterminate_missing:
        outcomes.append(ScreeningOutcome.additional_information_required)
        rationale.append(f"{len(critical)} critical information gaps; {len(indeterminate_missing)} criteria "
                         "indeterminate for lack of project data")
    if not reg.determinable or not met:
        outcomes.append(ScreeningOutcome.no_determination_possible)
        rationale.append("The regulatory screening category could not be determined from the encoded criteria and "
                         "available information")
    outcomes.append(ScreeningOutcome.human_review_required)
    rationale.append("All findings are preliminary and require review by a qualified practitioner and, where "
                     "applicable, the competent authority")
    limitations = [
        "Screening is based solely on the information supplied and the regulatory documents in the knowledge base.",
        "Regulatory criteria are encoded for part of each framework only and are unverified.",
        "No field data, modelling or site visit informs this assessment.",
        "Absence of an identified impact or requirement does not mean that none exists.",
    ]
    return ScreeningAssessment(regulatory_screening=reg, environmental_risk_screening=risk, outcomes=outcomes,
                               outcome_labels=[OUTCOME_LABELS[o] for o in outcomes], rationale=rationale,
                               limitations=limitations)
