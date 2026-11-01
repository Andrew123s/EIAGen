"""Preliminary mitigation considerations and further investigations.

Measures are organised by the mitigation hierarchy (avoid, minimise, restore,
offset) and typed as general good practice, project-specific, regulatory
requirement (from retrieved provisions, still requiring verification) or
requiring specialist design. They are considerations, never evidence that an
impact is controlled.
"""

from __future__ import annotations

import re

from app.schemas import (
    FurtherInvestigation,
    MeasureType,
    MitigationLevel,
    MitigationMeasure,
    PotentialImpact,
    ProjectProfile,
    Provenance,
    RegulatoryRequirement,
)
from app.services.taxonomy import taxonomy

_SPECIALIST_RE = re.compile(r"design|dam safety|liner|geotechnical|dispersion|modelling|curtailment|"
                            r"environmental flows|fish passage|sized|offset", re.I)
_PRIORITY_RANK = {"low": 0, "medium": 1, "high": 2}


def generate_mitigation(impacts: list[PotentialImpact], profile: ProjectProfile,
                        requirements: list[RegulatoryRequirement] | None = None) -> list[MitigationMeasure]:
    tax = taxonomy()
    receptors = {r.key: r for r in profile.receptors}
    measures: list[MitigationMeasure] = []

    def add(impact_id: str, level: MitigationLevel, text: str, mtype: MeasureType, rationale: str,
            provenance: Provenance = Provenance.deterministic_rule) -> None:
        measures.append(MitigationMeasure(
            measure_id=f"MIT-{len(measures) + 1:03d}", impact_id=impact_id, hierarchy_level=level, measure=text,
            measure_type=mtype, rationale=rationale, provenance=provenance))

    for imp in impacts:
        spec = tax["impacts"][imp.impact_key]
        confirmed = [receptors[k] for k in imp.receptor_keys if k in receptors and receptors[k].presence_confirmed]
        for level_name, items in spec.get("mitigation", {}).items():
            level = MitigationLevel(level_name)
            for text in items:
                if level == MitigationLevel.offset:
                    mtype = MeasureType.specialist_design_required
                    rationale = ("Offsets are a last resort after avoidance, minimisation and restoration, and only "
                                 "where legally and technically justified.")
                elif _SPECIALIST_RE.search(text):
                    mtype = MeasureType.specialist_design_required
                    rationale = "Requires specialist design or validation before it can be relied upon."
                else:
                    mtype = MeasureType.general_good_practice
                    rationale = f"General good practice for {spec['label'].lower()}."
                add(imp.impact_id, level, text, mtype, rationale)
        # Project-specific: tie avoidance to receptors actually named for this project.
        if confirmed and not imp.beneficial:
            r = confirmed[0]
            add(imp.impact_id, MitigationLevel.avoid,
                f"Define the project footprint and working areas to maintain separation from the {r.name.lower()} "
                f"identified for this project, informed by baseline survey results.",
                MeasureType.project_specific, f"Receptor identified for this project - {r.basis}")

    # Regulatory obligations mapped to impacts by environmental component.
    for req in requirements or []:
        related = [i for i in impacts if i.environmental_component in req.environmental_components and not i.beneficial]
        if not related:
            continue
        add(related[0].impact_id, MitigationLevel.minimize, req.potential_obligation,
            MeasureType.regulatory_requirement,
            f"Potential obligation retrieved from {', '.join(req.supporting_evidence)} "
            f"({req.applicability.value.replace('_', ' ')}); requires verification against the official text.",
            Provenance.retrieved_legislation)
    return measures


def identify_investigations(impacts: list[PotentialImpact]) -> list[FurtherInvestigation]:
    tax = taxonomy()
    agg: dict[str, dict] = {}
    for imp in impacts:
        if imp.beneficial:
            continue
        for name in tax["impacts"][imp.impact_key].get("investigations", []):
            entry = agg.setdefault(name, {"impacts": [], "labels": [], "priority": "low"})
            entry["impacts"].append(imp.impact_id)
            entry["labels"].append(imp.potential_impact.lower())
            if _PRIORITY_RANK[imp.risk_priority or "low"] > _PRIORITY_RANK[entry["priority"]]:
                entry["priority"] = imp.risk_priority
    out = []
    for name, entry in sorted(agg.items(), key=lambda kv: (-_PRIORITY_RANK[kv[1]["priority"]], kv[0])):
        labels = list(dict.fromkeys(entry["labels"]))
        out.append(FurtherInvestigation(
            investigation_id=f"INV-{len(out) + 1:02d}", name=name,
            reason="Needed to characterise: " + "; ".join(labels[:4]) + ("; ..." if len(labels) > 4 else ""),
            related_impacts=entry["impacts"], priority=entry["priority"]))
    return out
