"""Project-analysis helpers: build parser input from the database and apply user corrections."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import Project
from app.schemas import (
    Confidence,
    InfoStatus,
    ProfileCorrection,
    ProfileField,
    ProjectActivity,
    ProjectParameter,
    ProjectPhase,
    ProjectProfile,
    Provenance,
)
from app.services import audit
from app.services.project_parser import ProjectInput, SourceText, derive_pressures, identify_missing
from app.services.taxonomy import rules, taxonomy


def build_project_input(project: Project) -> ProjectInput:
    docs = [SourceText(f"uploaded document '{d.original_filename}'", d.extracted_text or "", Provenance.uploaded_document)
            for d in project.documents if d.extracted_text]
    return ProjectInput(
        name=project.name, description=project.description, jurisdiction=project.jurisdiction,
        sector=project.sector, phase=project.phase, location=project.location, region=project.region,
        latitude=project.latitude, longitude=project.longitude, objectives=project.objectives,
        characteristics=dict(project.characteristics or {}), environmental_setting=project.environmental_setting,
        documents=docs)


def refresh_derived(profile: ProjectProfile, inp: ProjectInput) -> ProjectProfile:
    """Recompute pressures and missing information after activities/parameters change."""
    for i, act in enumerate(profile.activities, start=1):
        act.activity_id = f"ACT-{i:02d}"
    profile.pressures = derive_pressures(profile.activities)
    profile.phases = sorted({p for a in profile.activities for p in a.phases}, key=lambda p: list(ProjectPhase).index(p))
    profile.missing_information = identify_missing(inp, inp.sources(), profile.parameters, profile.activities,
                                                   profile.receptors, profile.sector)
    profile.clarification_questions = [m.clarification_question for m in profile.missing_information
                                       if m.importance != "useful"]
    return ProjectProfile.model_validate(profile.model_dump())


def apply_corrections(db: Session, project: Project, profile: ProjectProfile,
                      corr: ProfileCorrection) -> ProjectProfile:
    tax = taxonomy()
    defs = rules()["parameters"]
    actor = corr.corrected_by
    inp = build_project_input(project)

    def log(field: str, prev, new) -> None:
        audit.record(db, actor=actor, actor_type="human", action="profile_corrected", entity_type="project_profile",
                     entity_id=str(project.id), project_id=project.id, field=field, previous_value=prev, new_value=new)

    if corr.sector is not None:
        key = corr.sector.strip().lower().replace(" ", "_")
        if key not in tax["sectors"]:
            raise ValueError(f"Unknown sector '{corr.sector}'. Known: {', '.join(tax['sectors'])}")
        log("sector", profile.sector.value, key)
        profile.sector = ProfileField(value=key, status=InfoStatus.user_provided, provenance=Provenance.user_input)
        project.sector = key
        inp.sector = key
    for name in ("location", "region", "phase", "environmental_setting"):
        value = getattr(corr, name)
        if value is not None:
            log(name, getattr(profile, name).value, value)
            setattr(profile, name, ProfileField(value=value, status=InfoStatus.user_provided,
                                                provenance=Provenance.user_input))
            setattr(project, name, value)
            setattr(inp, name, value)
    if corr.remove_activities:
        before = [a.key for a in profile.activities]
        profile.activities = [a for a in profile.activities
                              if a.key not in corr.remove_activities and a.activity_id not in corr.remove_activities]
        log("activities_removed", before, [a.key for a in profile.activities])
    existing = {a.key for a in profile.activities}
    for key in corr.add_activities:
        if key not in tax["activities"]:
            raise ValueError(f"Unknown activity '{key}'")
        if key in existing:
            continue
        spec = tax["activities"][key]
        profile.activities.append(ProjectActivity(
            activity_id="", key=key, name=spec["label"], phases=[ProjectPhase(p) for p in spec["phases"]],
            matched_text=[f"Added by {actor}"], status=InfoStatus.user_provided, provenance=Provenance.user_input,
            confidence=Confidence.high))
        log("activity_added", None, key)
    for name, value in corr.parameters.items():
        if name not in defs:
            raise ValueError(f"Unknown parameter '{name}'")
        prev = profile.parameter(name)
        profile.parameters = [p for p in profile.parameters if p.name != name]
        profile.parameters.append(ProjectParameter(
            name=name, label=defs[name]["label"], value=float(value), unit=defs[name]["unit"],
            source_text=f"Entered by {actor}", status=InfoStatus.user_provided, provenance=Provenance.user_input))
        chars = dict(project.characteristics or {})
        chars[name] = float(value)
        project.characteristics = chars
        inp.characteristics = chars
        log(f"parameter:{name}", prev, value)
    profile.parameters.sort(key=lambda p: p.name)
    order = list(tax["activities"])
    profile.activities.sort(key=lambda a: order.index(a.key))
    profile = refresh_derived(profile, inp)
    project.profile_json = profile.model_dump(mode="json")
    project.profile_corrected_by = actor
    db.commit()
    return profile
