"""Potential impact identification and environmental risk prioritisation.

Impacts follow the taxonomy chain activity -> pressure -> impact -> receptor.
One ``PotentialImpact`` is produced per impact type, aggregating the activities
that generate it. All impacts are *potential*: they flag issues to investigate.

Risk scoring (methodology EIAGen-RS-1, config/assessment_rules.yaml):
    score = likelihood(1-3) x severity(1-3) + sensitivity bonus, capped at 9
    priority: 1-2 low, 3-5 medium, 6-9 high
Severity may be raised one level by documented, non-statutory scale escalators.
"""

from __future__ import annotations

from app.schemas import (
    Confidence,
    Duration,
    Extent,
    InfoStatus,
    Likelihood,
    PotentialImpact,
    ProjectPhase,
    ProjectProfile,
    Provenance,
    Reversibility,
    Severity,
    Sensitivity,
)
from app.services.taxonomy import component_category, rules, taxonomy

_SEVERITY_ORDER = [Severity.minor, Severity.moderate, Severity.major]


def _compare(value: float, op: str, target: float) -> bool:
    return {">=": value >= target, ">": value > target, "<=": value <= target,
            "<": value < target, "==": value == target}[op]


def score_impact(likelihood: Likelihood, severity: Severity, sensitivity: Sensitivity) -> tuple[int, str]:
    method = rules()["risk_methodology"]
    score = method["likelihood"][likelihood.value] * method["severity"][severity.value]
    score = min(9, score + method["sensitivity_bonus"][sensitivity.value])
    for band, (lo, hi) in method["bands"].items():
        if lo <= score <= hi:
            return score, band
    return score, "high"


def identify_impacts(profile: ProjectProfile) -> list[PotentialImpact]:
    tax = taxonomy()
    method = rules()["risk_methodology"]
    acts = {a.activity_id: a for a in profile.activities}
    receptors = {r.key: r for r in profile.receptors}

    # impact_key -> set of activity ids / pressure keys
    sources: dict[str, dict[str, list[str]]] = {}
    for pressure in profile.pressures:
        for impact_key in tax["pressures"][pressure.key]["impacts"]:
            entry = sources.setdefault(impact_key, {"activities": [], "pressures": []})
            for aid in pressure.source_activities:
                if aid not in entry["activities"]:
                    entry["activities"].append(aid)
            entry["pressures"].append(pressure.key)

    impacts: list[PotentialImpact] = []
    for impact_key, src in sources.items():
        spec = tax["impacts"][impact_key]
        d = spec["defaults"]
        act_objs = [acts[a] for a in src["activities"] if a in acts]
        confirmed = [receptors[k] for k in spec["receptors"] if k in receptors and receptors[k].presence_confirmed]
        assumed = [receptors[k] for k in spec["receptors"] if k in receptors and not receptors[k].presence_confirmed]
        unconfirmed_keys = [k for k in spec["receptors"] if k not in receptors]

        if confirmed:
            sensitivity = max((r.sensitivity for r in confirmed), key=lambda s: ["low", "medium", "high"].index(s.value))
            receptor_text = ", ".join(r.name for r in confirmed)
        elif assumed:
            sensitivity = Sensitivity.medium
            receptor_text = ", ".join(r.name for r in assumed) + " (generic receptor, not described)"
        else:
            sensitivity = Sensitivity.low
            receptor_text = ", ".join(tax["receptors"][k]["label"] for k in spec["receptors"]) + " (presence not confirmed)"

        severity = Severity(d["severity"])
        notes: list[str] = []
        for esc in method.get("scale_escalators", []):
            value = profile.parameter(esc["parameter"])
            if value is not None and spec["component"] in esc["components"] and _compare(value, esc["op"], esc["value"]):
                idx = min(2, _SEVERITY_ORDER.index(severity) + 1)
                if _SEVERITY_ORDER[idx] != severity:
                    notes.append(f"Severity raised to {_SEVERITY_ORDER[idx].value}: {esc['parameter']} = {value:g} "
                                 f"meets heuristic escalator {esc['op']} {esc['value']}")
                    severity = _SEVERITY_ORDER[idx]

        likelihood = Likelihood(d["likelihood"])
        beneficial = bool(spec.get("beneficial"))
        score, band = (None, None) if beneficial else score_impact(likelihood, severity, sensitivity)
        if not beneficial:
            notes.append(f"Score {score} = likelihood {likelihood.value} x severity {severity.value}"
                         f"{' + 1 (high receptor sensitivity)' if sensitivity == Sensitivity.high else ''}")

        ai_only = act_objs and all(a.provenance == Provenance.ai_interpretation for a in act_objs)
        explicit = any(a.status in (InfoStatus.extracted, InfoStatus.user_provided) for a in act_objs)
        if ai_only:
            confidence = Confidence.low
        elif explicit and confirmed:
            confidence = Confidence.high if any(a.confidence == Confidence.high for a in act_objs) else Confidence.medium
        elif explicit:
            confidence = Confidence.medium if assumed else Confidence.low
        else:
            confidence = Confidence.low

        phases = sorted({p for a in act_objs for p in a.phases}, key=lambda p: list(ProjectPhase).index(p))
        evidence = []
        for a in act_objs[:3]:
            if a.matched_text:
                evidence.append(f'{a.activity_id} {a.name}: "{a.matched_text[0]}" ({a.provenance.value})')
        for r in confirmed[:2]:
            evidence.append(f"Receptor {r.name}: {r.basis}")

        extra = []
        if not confirmed:
            extra.append("Confirm whether " + ", ".join(tax["receptors"][k]["label"].lower()
                                                        for k in spec["receptors"]) + " are present near the site")
        extra += [f"Investigation: {inv}" for inv in spec.get("investigations", [])[:2]]

        impacts.append(PotentialImpact(
            impact_id="", impact_key=impact_key,
            project_activity="; ".join(a.name for a in act_objs) or "Not attributed",
            activity_ids=[a.activity_id for a in act_objs],
            environmental_component=spec["component"], component_category=component_category(spec["component"]),
            potential_impact=spec["label"], potential_receptor=receptor_text,
            receptor_keys=[r.key for r in confirmed] or [r.key for r in assumed] or unconfirmed_keys,
            impact_pathway=spec["pathway"], project_phases=phases,
            geographical_extent=Extent(d["extent"]), duration=Duration(d["duration"]),
            reversibility=Reversibility(d["reversibility"]), likelihood=likelihood, severity=severity,
            receptor_sensitivity=sensitivity, confidence=confidence, beneficial=beneficial,
            risk_score=score, risk_priority=band, scoring_notes=notes, evidence=evidence,
            supporting_rationale=(f"Pathway: {', '.join(tax['pressures'][p]['label'].lower() for p in src['pressures'])} "
                                  f"from {', '.join(a.name.lower() for a in act_objs)} may cause "
                                  f"{spec['label'].lower()} affecting {receptor_text.lower()}."),
            additional_information_required=extra,
            provenance=Provenance.ai_interpretation if ai_only else Provenance.deterministic_rule))

    impacts.sort(key=lambda i: (i.beneficial, -(i.risk_score or 0), i.impact_key))
    for n, imp in enumerate(impacts, start=1):
        imp.impact_id = f"IMP-{n:02d}"
    return impacts
