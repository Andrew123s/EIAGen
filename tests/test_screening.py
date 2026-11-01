import pytest
from pydantic import ValidationError

from app.schemas import CriterionResult, ScreeningOutcome
from app.services.impact_classifier import identify_impacts
from app.services.project_parser import ProjectInput, parse_project
from app.services.screening_engine import assess, regulatory_screening


def _screen(description, jurisdiction, **kw):
    inp = ProjectInput(name="P", description=description, jurisdiction=jurisdiction, **kw)
    profile = parse_project(inp)
    text = " ".join(s.text for s in inp.sources())
    return profile, regulatory_screening(profile, text)


def _crit(reg, cid):
    return next(c for c in reg.criteria if c.criterion_id == cid)


WIND = ("A wind farm with {n} wind turbines, each with a total height of 200 m, will be built on farmland near a "
        "village.")


def test_german_wind_thresholds():
    _, reg = _screen(WIND.format(n=8), "germany", sector="renewable_energy")
    assert _crit(reg, "DE-UVPG-1.6.2").result == CriterionResult.met
    assert _crit(reg, "DE-UVPG-1.6.1").result == CriterionResult.not_met
    assert _crit(reg, "DE-UVPG-1.6.3").result == CriterionResult.not_met
    _, reg = _screen(WIND.format(n=25), "germany", sector="renewable_energy")
    assert _crit(reg, "DE-UVPG-1.6.1").result == CriterionResult.met
    _, reg = _screen(WIND.format(n=4), "germany", sector="renewable_energy")
    assert _crit(reg, "DE-UVPG-1.6.3").result == CriterionResult.met


def test_missing_parameter_is_indeterminate_not_not_met():
    _, reg = _screen("A wind farm with several wind turbines will be built on farmland.", "germany",
                     sector="renewable_energy")
    c = _crit(reg, "DE-UVPG-1.6.1")
    assert c.result == CriterionResult.indeterminate
    assert "turbine_count" in c.missing_parameters


def test_eu_quarry_threshold():
    _, reg = _screen("An open-pit quarry of 30 hectares will extract limestone.", "eu", sector="mining")
    assert _crit(reg, "EU-EIA-I-19").result == CriterionResult.met
    _, reg = _screen("An open-pit quarry of 20 hectares will extract limestone.", "eu", sector="mining")
    assert _crit(reg, "EU-EIA-I-19").result == CriterionResult.not_met


def test_eu_road_requires_both_parameters():
    _, reg = _screen("Construction of a new four-lane road of 12 km length.", "eu", sector="road_construction")
    assert _crit(reg, "EU-EIA-I-7C").result == CriterionResult.met
    _, reg = _screen("Construction of a new four-lane road.", "eu", sector="road_construction")
    assert _crit(reg, "EU-EIA-I-7C").result == CriterionResult.indeterminate


def test_eu_livestock_any_parameter():
    _, reg = _screen("An intensive poultry installation with 90,000 broilers in animal housing.", "eu",
                     sector="agriculture")
    assert _crit(reg, "EU-EIA-I-17").result == CriterionResult.met


def test_only_selected_jurisdiction_criteria_evaluated():
    _, reg = _screen(WIND.format(n=25), "ghana", sector="renewable_energy")
    assert all(c.jurisdiction == "ghana" for c in reg.criteria)
    assert not any(c.criterion_id.startswith("DE-") for c in reg.criteria)


def test_unconfigured_jurisdiction_cannot_be_determined():
    profile = parse_project(ProjectInput(name="P", description="A quarry of 30 hectares.", jurisdiction="atlantis"))
    reg = regulatory_screening(profile, "A quarry of 30 hectares.")
    assert reg.criteria == [] and not reg.determinable
    assert "cannot be determined reliably" in reg.summary


def test_ghana_sensitive_area_flag_and_unverified_flagging():
    _, reg = _screen("A processing plant beside a wetland and a forest reserve.", "ghana", sector="manufacturing")
    c = _crit(reg, "GH-LI1652-S5")
    assert c.result == CriterionResult.met and c.verification_status == "unverified"
    assert "human regulatory review" in c.explanation


def test_assessment_outcomes_always_include_human_review():
    inp = ProjectInput(name="P", description=WIND.format(n=8), jurisdiction="germany", sector="renewable_energy")
    profile = parse_project(inp)
    a = assess(profile, identify_impacts(profile), inp.description, profile.missing_information)
    assert ScreeningOutcome.human_review_required in a.outcomes
    assert a.preliminary is True and a.human_review_required is True
    assert ScreeningOutcome.regulatory_category_identified in a.outcomes


def test_assessment_schema_rejects_missing_human_review():
    inp = ProjectInput(name="P", description=WIND.format(n=8), jurisdiction="germany")
    profile = parse_project(inp)
    a = assess(profile, identify_impacts(profile), inp.description, profile.missing_information)
    data = a.model_dump()
    data["outcomes"] = ["regulatory_category_identified"]
    with pytest.raises(ValidationError):
        type(a).model_validate(data)


def test_risk_screening_never_creates_legal_category():
    # Many high-risk impacts but no statutory criterion for the sector in Germany beyond the generic check.
    desc = ("A factory will discharge wastewater into a river, store hazardous chemicals, clear forest and "
            "excavate near a wetland.")
    inp = ProjectInput(name="P", description=desc, jurisdiction="germany", sector="manufacturing")
    profile = parse_project(inp)
    impacts = identify_impacts(profile)
    a = assess(profile, impacts, desc, profile.missing_information)
    assert a.environmental_risk_screening.counts["high"] > 0
    met_eia = [c for c in a.regulatory_screening.criteria if c.result == CriterionResult.met
               and c.screening_category == "environmental_impact_assessment"]
    assert met_eia == []
    assert "NOT a finding" in a.regulatory_screening.summary or a.regulatory_screening.categories_identified
