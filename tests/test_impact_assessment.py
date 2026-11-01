from app.schemas import Confidence, Likelihood, Provenance, Severity, Sensitivity
from app.services.impact_classifier import identify_impacts, score_impact
from app.services.mitigation_service import generate_mitigation, identify_investigations
from app.services.project_parser import ProjectInput, parse_project
from app.services.taxonomy import taxonomy
from tests.conftest import MINERAL_DESCRIPTION


def _profile(description=MINERAL_DESCRIPTION, **kw):
    return parse_project(ProjectInput(name="P", description=description, jurisdiction="ghana", **kw))


def test_expected_impacts_identified():
    impacts = identify_impacts(_profile())
    keys = {i.impact_key for i in impacts}
    for expected in ("surface_water_sedimentation", "surface_water_contamination", "soil_erosion", "habitat_loss",
                     "air_quality_particulates", "noise_nuisance", "water_resource_depletion", "road_safety",
                     "ghg_emissions", "employment_opportunities"):
        assert expected in keys


def test_all_taxonomy_categories_available():
    components = taxonomy()["components"]
    assert set(components) == {"physical", "biological", "social", "climate_ecosystem_services"}


def test_impacts_are_potential_and_linked_to_activities():
    profile = _profile()
    ids = {a.activity_id for a in profile.activities}
    for imp in identify_impacts(profile):
        assert imp.status == "potential"
        assert set(imp.activity_ids) <= ids and imp.activity_ids
        assert imp.impact_pathway and imp.supporting_rationale
        assert imp.impact_id.startswith("IMP-")


def test_risk_score_formula():
    assert score_impact(Likelihood.likely, Severity.major, Sensitivity.high) == (9, "high")
    assert score_impact(Likelihood.possible, Severity.moderate, Sensitivity.medium) == (4, "medium")
    assert score_impact(Likelihood.unlikely, Severity.minor, Sensitivity.low) == (1, "low")
    assert score_impact(Likelihood.possible, Severity.major, Sensitivity.high) == (7, "high")


def test_high_sensitivity_receptor_raises_priority():
    impacts = {i.impact_key: i for i in identify_impacts(_profile())}
    sed = impacts["surface_water_sedimentation"]
    assert sed.receptor_sensitivity == Sensitivity.high     # "near a river"
    assert sed.risk_priority == "high"
    assert sed.confidence in (Confidence.high, Confidence.medium)


def test_scale_escalator_documented():
    impacts = {i.impact_key: i for i in identify_impacts(_profile(MINERAL_DESCRIPTION.replace("45 hectares", "300 hectares")))}
    hab = impacts["habitat_loss"]
    assert hab.severity == Severity.major
    assert any("escalator" in n for n in hab.scoring_notes)


def test_beneficial_effects_not_scored():
    emp = next(i for i in identify_impacts(_profile()) if i.impact_key == "employment_opportunities")
    assert emp.beneficial and emp.risk_score is None and emp.risk_priority is None


def test_unconfirmed_receptor_gives_low_confidence_and_info_request():
    impacts = {i.impact_key: i for i in identify_impacts(_profile("A small office building will be constructed with "
                                                                    "excavation for foundations."))}
    hab = impacts["habitat_loss"]
    assert "not confirmed" in hab.potential_receptor
    assert hab.confidence == Confidence.low
    assert any("Confirm whether" in x for x in hab.additional_information_required)


def test_ai_inferred_activity_marks_impacts_low_confidence():
    profile = _profile("A small office building will be constructed.")
    from app.schemas import ProjectActivity, InfoStatus, ProjectPhase
    from app.services.analysis_service import refresh_derived

    profile.activities.append(ProjectActivity(activity_id="", key="drilling", name="Drilling",
                                              phases=[ProjectPhase.construction], status=InfoStatus.inferred,
                                              provenance=Provenance.ai_interpretation, confidence=Confidence.low))
    profile = refresh_derived(profile, ProjectInput(name="P", description="A small office building will be constructed.",
                                                    jurisdiction="ghana"))
    drill_only = [i for i in identify_impacts(profile) if i.impact_key == "groundwater_drawdown"]
    assert drill_only and drill_only[0].provenance == Provenance.ai_interpretation
    assert drill_only[0].confidence == Confidence.low


def test_mitigation_hierarchy_and_types():
    profile = _profile()
    impacts = identify_impacts(profile)
    measures = generate_mitigation(impacts, profile)
    levels = {m.hierarchy_level.value for m in measures}
    assert {"avoid", "minimize", "restore", "offset"} <= levels
    types = {m.measure_type.value for m in measures}
    assert {"general_good_practice", "project_specific", "specialist_design_required"} <= types
    assert all("not evidence" in m.note for m in measures)
    offsets = [m for m in measures if m.hierarchy_level.value == "offset"]
    assert all("legally and technically justified" in m.rationale for m in offsets)


def test_further_investigations():
    invs = identify_investigations(identify_impacts(_profile()))
    names = {i.name for i in invs}
    assert "Baseline surface water-quality sampling" in names
    assert "Terrestrial biodiversity survey (flora and fauna)" in names
    assert invs[0].priority == "high"
