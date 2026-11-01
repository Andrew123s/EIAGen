"""Deterministic project-description parser.

Extracts project activities, parameters (with units), environmental receptors,
setting flags and missing information from the registered project and any
uploaded documents. Every item records where it came from:

* user-entered structured fields  -> status ``user_provided``, provenance ``user_input``
* text in the user's description  -> status ``extracted``,     provenance ``user_input``
* text in uploaded documents      -> status ``extracted``,     provenance ``uploaded_document``
* derived from other items        -> status ``inferred``,      provenance ``deterministic_rule``

Nothing that was not supplied is filled in: absent items become
``MissingInformation`` entries with clarification questions.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.core.security import detect_injection
from app.schemas import (
    Confidence,
    EnvironmentalPressure,
    EnvironmentalReceptor,
    InfoStatus,
    MissingInformation,
    ProfileField,
    ProjectActivity,
    ProjectParameter,
    ProjectPhase,
    ProjectProfile,
    Provenance,
    Sensitivity,
)
from app.services.geo_service import assess_location
from app.services.taxonomy import find_keywords, rules, taxonomy

# --------------------------------------------------------------- inputs


@dataclass
class SourceText:
    label: str
    text: str
    provenance: Provenance


@dataclass
class ProjectInput:
    name: str
    description: str
    jurisdiction: str
    sector: str | None = None
    phase: str | None = None
    location: str | None = None
    region: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    objectives: str | None = None
    characteristics: dict = field(default_factory=dict)
    environmental_setting: str | None = None
    documents: list[SourceText] = field(default_factory=list)

    def sources(self) -> list[SourceText]:
        user_text = " \n".join(t for t in [self.description, self.objectives or "",
                                           self.environmental_setting or ""] if t)
        chars = "; ".join(f"{k}: {v}" for k, v in self.characteristics.items())
        out = [SourceText("project description", user_text, Provenance.user_input)]
        if chars:
            out.append(SourceText("project characteristics", chars, Provenance.user_input))
        return out + self.documents


# ----------------------------------------------------------- parameters

_NUM = r"(\d{1,3}(?:[,\s]\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
_WORD_NUMBERS = {"two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
                 "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20, "thirty": 30}


def _num(s: str) -> float:
    s = s.strip().lower()
    if s in _WORD_NUMBERS:
        return float(_WORD_NUMBERS[s])
    return float(re.sub(r"[,\s]", "", s))


def _time_factor(per: str) -> float:
    per = per.lower()
    if per.startswith(("h",)):
        return 24.0
    if per.startswith(("y", "a")):
        return 1 / 365.0
    if per.startswith("m") and per.startswith("mo"):
        return 1 / 30.0
    if per.startswith("w"):
        return 1 / 7.0
    return 1.0


_WORDNUM = r"(\d+|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|fifteen|twenty|thirty)"

_PATTERNS: dict[str, list[tuple[re.Pattern[str], callable]]] = {
    "project_area_ha": [
        (re.compile(_NUM + r"\s*(?:hectares?|ha)\b", re.I), lambda m: _num(m.group(1))),
        (re.compile(_NUM + r"\s*acres?\b", re.I), lambda m: round(_num(m.group(1)) * 0.404686, 2)),
        (re.compile(_NUM + r"\s*(?:km2|km²|sq\.?\s*km|square\s+kilomet(?:re|er)s?)", re.I), lambda m: _num(m.group(1)) * 100),
    ],
    "installed_capacity_mw": [
        (re.compile(_NUM + r"\s*(?:MWp?|MWac|MWdc|megawatts?)\b", re.I), lambda m: _num(m.group(1))),
        (re.compile(_NUM + r"\s*(?:GWp?|gigawatts?)\b", re.I), lambda m: _num(m.group(1)) * 1000),
    ],
    "turbine_count": [
        (re.compile(_WORDNUM + r"\s+(?:\w+\s+){0,2}?(?:wind\s+)?turbines", re.I), lambda m: _num(m.group(1))),
    ],
    "turbine_height_m": [
        (re.compile(r"(?:total|tip|overall)\s+height\s+(?:of\s+)?(?:up\s+to\s+|approximately\s+)?" + _NUM + r"\s*m\b", re.I),
         lambda m: _num(m.group(1))),
        (re.compile(_NUM + r"\s*m(?:etres|eters)?\s+(?:total|tip)\s+height", re.I), lambda m: _num(m.group(1))),
    ],
    "road_length_km": [
        (re.compile(_NUM + r"[\s-]*(?:km|kilomet(?:re|er)s?)(?:[\s-]+long|\s+(?:of\s+)?(?:new\s+)?(?:road|highway|motorway|carriageway|bypass|expressway|stretch|section))", re.I),
         lambda m: _num(m.group(1))),
        (re.compile(r"(?:length|stretch|section)\s+of\s+(?:approximately\s+|about\s+)?" + _NUM + r"\s*(?:km|kilomet)", re.I),
         lambda m: _num(m.group(1))),
        (re.compile(_NUM + r"\s*(?:km|kilomet(?:re|er)s?)\s+(?:in\s+)?length", re.I), lambda m: _num(m.group(1))),
    ],
    "lanes": [
        (re.compile(_WORDNUM + r"[\s-]+lanes?\b", re.I), lambda m: _num(m.group(1))),
        (re.compile(r"\bdual[\s-]+carriageway\b", re.I), lambda m: 4.0),
    ],
    "water_demand": [
        (re.compile(_NUM + r"\s*(?:m3|m³|cubic\s+met(?:re|er)s?)\s*(?:per|/|a)\s*(day|d|hour|h|year|yr|annum|month)", re.I),
         lambda m: round(_num(m.group(1)) * _time_factor(m.group(2)), 2)),
        (re.compile(_NUM + r"\s*(?:litres?|liters?|l)\s*(?:per|/|a)\s*(day|d|hour|h|year|annum)", re.I),
         lambda m: round(_num(m.group(1)) * _time_factor(m.group(2)) / 1000.0, 3)),
    ],
    "workforce": [
        (re.compile(_NUM + r"\s+(?:\w+\s+){0,2}?(?:workers|employees|staff|personnel|jobs)\b", re.I), lambda m: _num(m.group(1))),
    ],
    "housing_units": [
        (re.compile(_NUM + r"\s+(?:\w+\s+){0,2}?(?:housing\s+units|residential\s+units|units|houses|dwellings|homes|apartments|flats)\b", re.I),
         lambda m: _num(m.group(1))),
    ],
    "production_capacity": [
        (re.compile(_NUM + r"\s*(?:million\s+)?(?:tonnes|tons|t)\s*(?:per|/|a)\s*(?:day|year|annum|hour|month)", re.I),
         lambda m: _num(m.group(1)) * (1e6 if "million" in m.group(0).lower() else 1)),
        (re.compile(_NUM + r"\s*(?:tpd|tpa|mtpa)\b", re.I),
         lambda m: _num(m.group(1)) * (1e6 if m.group(0).lower().endswith("mtpa") else 1)),
    ],
    "broiler_count": [(re.compile(_NUM + r"\s+(?:\w+\s+)?broilers?\b", re.I), lambda m: _num(m.group(1)))],
    "hen_count": [(re.compile(_NUM + r"\s+(?:laying\s+)?(?:hens|layers)\b", re.I), lambda m: _num(m.group(1)))],
    "pig_count": [(re.compile(_NUM + r"\s+(?:production\s+|fattening\s+|finishing\s+)?pigs\b", re.I), lambda m: _num(m.group(1)))],
    "sow_count": [(re.compile(_NUM + r"\s+sows\b", re.I), lambda m: _num(m.group(1)))],
    "project_duration_months": [
        (re.compile(r"(?:construction|duration|period|programme|program)[^.]{0,40}?" + _NUM + r"\s*months?", re.I),
         lambda m: _num(m.group(1))),
        (re.compile(r"(?:construction|duration|period)[^.]{0,40}?" + _NUM + r"\s*years?", re.I),
         lambda m: _num(m.group(1)) * 12),
    ],
    "energy_demand": [
        (re.compile(_NUM + r"\s*(?:MWh|GWh|kWh)", re.I), lambda m: _num(m.group(1))),
    ],
}
# Parameters whose occurrences should be summed or maximised.
_TAKE_MAX = {"project_area_ha", "road_length_km", "workforce", "installed_capacity_mw", "housing_units",
             "turbine_count", "water_demand", "production_capacity"}

_WASTEWATER_RE = re.compile(
    r"(?:wastewater|waste\s+water|effluent)[^.]{0,60}?" + _NUM + r"\s*(?:m3|m³|cubic\s+met(?:re|er)s?)\s*(?:per|/|a)\s*(day|d|hour|h|year|annum)"
    r"|" + _NUM + r"\s*(?:m3|m³)\s*(?:per|/|a)\s*(day|d|hour|h|year|annum)\s+of\s+(?:wastewater|effluent)", re.I)


def extract_parameters(sources: list[SourceText], characteristics: dict) -> list[ProjectParameter]:
    defs = rules()["parameters"]
    found: dict[str, ProjectParameter] = {}

    # 1. Structured fields entered by the user take precedence.
    for key, value in characteristics.items():
        norm = re.sub(r"[^a-z0-9]+", "_", str(key).lower()).strip("_")
        if norm in defs:
            try:
                val = float(value)
            except (TypeError, ValueError):
                continue
            found[norm] = ProjectParameter(name=norm, label=defs[norm]["label"], value=val, unit=defs[norm]["unit"],
                                           source_text=f"{key}: {value}", status=InfoStatus.user_provided,
                                           provenance=Provenance.user_input)

    # 2. Values stated in free text.
    for src in sources:
        if src.label == "project characteristics":
            continue
        text = src.text
        for name, patterns in _PATTERNS.items():
            if name in found and found[name].status == InfoStatus.user_provided:
                continue
            values: list[tuple[float, str]] = []
            for pattern, conv in patterns:
                for m in pattern.finditer(text):
                    try:
                        values.append((conv(m), m.group(0).strip()))
                    except (ValueError, IndexError):
                        continue
            if not values:
                continue
            value, quote = max(values, key=lambda v: v[0]) if name in _TAKE_MAX else values[0]
            if value <= 0:
                continue
            current = found.get(name)
            if current is None or (name in _TAKE_MAX and value > current.value):
                found[name] = ProjectParameter(name=name, label=defs[name]["label"], value=value,
                                               unit=defs[name]["unit"], source_text=quote,
                                               status=InfoStatus.extracted, provenance=src.provenance)
        m = _WASTEWATER_RE.search(text)
        if m and "wastewater_volume" not in found:
            num = m.group(1) or m.group(3)
            per = m.group(2) or m.group(4)
            found["wastewater_volume"] = ProjectParameter(
                name="wastewater_volume", label=defs["wastewater_volume"]["label"],
                value=round(_num(num) * _time_factor(per), 2), unit=defs["wastewater_volume"]["unit"],
                source_text=m.group(0), status=InfoStatus.extracted, provenance=src.provenance)

    # Livestock total (inferred) from species-specific counts.
    species = [found[k].value for k in ("broiler_count", "hen_count", "pig_count", "sow_count") if k in found]
    if species and "livestock_count" not in found:
        found["livestock_count"] = ProjectParameter(
            name="livestock_count", label=defs["livestock_count"]["label"], value=sum(species),
            unit=defs["livestock_count"]["unit"], source_text="sum of species-specific counts",
            status=InfoStatus.inferred, provenance=Provenance.deterministic_rule)
    return sorted(found.values(), key=lambda p: p.name)


# ---------------------------------------------------------------- sector


def detect_sector(user_sector: str | None, text: str) -> ProfileField:
    sectors = taxonomy()["sectors"]
    if user_sector:
        key = user_sector.strip().lower().replace(" ", "_")
        if key in sectors:
            return ProfileField(value=key, status=InfoStatus.user_provided, provenance=Provenance.user_input)
    scores = {k: len(find_keywords(text, v["keywords"])) for k, v in sectors.items()}
    best = max(scores.values()) if scores else 0
    if best == 0:
        return ProfileField(value=None, status=InfoStatus.missing)
    winners = [k for k, v in scores.items() if v == best]
    if len(winners) > 1:
        return ProfileField(value=None, status=InfoStatus.missing,
                            provenance=Provenance.deterministic_rule)
    return ProfileField(value=winners[0], status=InfoStatus.inferred, provenance=Provenance.deterministic_rule)


# ------------------------------------------------------------ activities


def extract_activities(sources: list[SourceText]) -> list[ProjectActivity]:
    tax = taxonomy()
    acts: dict[str, ProjectActivity] = {}
    for src in sources:
        for key, spec in tax["activities"].items():
            hits = find_keywords(src.text, spec["keywords"])
            if not hits:
                continue
            if key in acts:
                acts[key].matched_text.extend(h for h in hits if h not in acts[key].matched_text)
                continue
            acts[key] = ProjectActivity(
                activity_id="", key=key, name=spec["label"],
                phases=[ProjectPhase(p) for p in spec["phases"]], matched_text=hits[:3],
                status=InfoStatus.extracted, provenance=src.provenance,
                confidence=Confidence.high if len(hits) > 1 else Confidence.medium)
    ordered = sorted(acts.values(), key=lambda a: list(tax["activities"]).index(a.key))
    for i, act in enumerate(ordered, start=1):
        act.activity_id = f"ACT-{i:02d}"
    return ordered


def derive_pressures(activities: list[ProjectActivity]) -> list[EnvironmentalPressure]:
    tax = taxonomy()
    pressures: dict[str, EnvironmentalPressure] = {}
    for act in activities:
        for pkey in tax["activities"][act.key]["pressures"]:
            spec = tax["pressures"][pkey]
            if pkey not in pressures:
                pressures[pkey] = EnvironmentalPressure(key=pkey, name=spec["label"], source_activities=[])
            pressures[pkey].source_activities.append(act.activity_id)
    return list(pressures.values())


# ------------------------------------------------------------- receptors

_PROXIMITY_RE = re.compile(
    r"\b(near|nearby|adjacent|next to|within|along|borders?|bordering|close to|upstream|downstream|"
    r"neighbouring|neighboring|surround\w*|\d+\s*(?:m|km|metres|meters)\s+(?:from|of|away))\b", re.I)


def extract_receptors(sources: list[SourceText]) -> list[EnvironmentalReceptor]:
    tax = taxonomy()
    receptors: list[EnvironmentalReceptor] = []
    combined = [(s, s.text) for s in sources]
    for key, spec in tax["receptors"].items():
        found_src, hits = None, []
        for src, text in combined:
            hits = find_keywords(text, spec["keywords"])
            if hits:
                found_src = src
                break
        if hits:
            near = any(_PROXIMITY_RE.search(h) for h in hits)
            high = near or key in ("protected_areas", "cultural_heritage_sites")
            receptors.append(EnvironmentalReceptor(
                key=key, name=spec["label"], component=spec["component"],
                sensitivity=Sensitivity.high if high else Sensitivity.medium, presence_confirmed=True,
                basis=f'Mentioned in {found_src.label}: "{hits[0]}"', provenance=found_src.provenance))
        elif spec.get("always_present"):
            receptors.append(EnvironmentalReceptor(
                key=key, name=spec["label"], component=spec["component"], sensitivity=Sensitivity.medium,
                presence_confirmed=False, basis="Generic receptor assumed present for any development; not described",
                provenance=Provenance.deterministic_rule))
    return receptors


def extract_setting_flags(sources: list[SourceText]) -> list[str]:
    flags = []
    for flag, keywords in rules()["setting_flags"].items():
        if any(find_keywords(s.text, keywords) for s in sources):
            flags.append(flag)
    return flags


# ------------------------------------------------------- missing information

_DISTANCE_RE = re.compile(r"\d+(?:\.\d+)?\s*(?:m|km|metres|meters|kilomet(?:re|er)s?)\s+(?:from|to|of|away|north|south|east|west|upstream|downstream)", re.I)
_TENURE_RE = re.compile(r"\b(land\s*owner|ownership|lease|leased|acquired|customary|stool\s+land|tenure|purchased|freehold|"
                        r"government\s+land|state\s+land|owned\s+by|compensation)\b", re.I)
_WASTE_RE = re.compile(r"\bwaste\b|\brefuse\b|\btailings\b|\boverburden\b|\bspoil\b", re.I)
_HAZMAT_RE = re.compile(r"\b(?:stor\w*|inventory|stock\w*)\b[^.]{0,80}\b\d[\d,.]*\s*(?:litres?|liters?|l|tonnes?|t|kg|m3)\b", re.I)
_PHASE_RE = re.compile(r"\b(feasibility|concept|design\s+stage|pre-?construction|planning\s+stage|detailed\s+design)\b", re.I)


def identify_missing(inp: ProjectInput, sources: list[SourceText], params: list[ProjectParameter],
                     activities: list[ProjectActivity], receptors: list[EnvironmentalReceptor],
                     sector: ProfileField) -> list[MissingInformation]:
    r = rules()["missing_information"]
    defs = rules()["parameters"]
    text = " ".join(s.text for s in sources)
    have_params = {p.name for p in params}
    act_keys = {a.key for a in activities}
    missing: dict[str, MissingInformation] = {}

    def add(key: str, description: str, importance: str, question: str) -> None:
        if key not in missing:
            missing[key] = MissingInformation(key=key, description=description, importance=importance,
                                              clarification_question=question)

    present = {
        "location": bool(inp.location or inp.region),
        "coordinates": inp.latitude is not None and inp.longitude is not None,
        "project_area_ha": "project_area_ha" in have_params,
        "environmental_setting": bool(inp.environmental_setting and inp.environmental_setting.strip())
        or any(rc.presence_confirmed and rc.key not in ("soil", "workers", "air_shed", "global_climate") for rc in receptors),
        "distance_to_receptors": bool(_DISTANCE_RE.search(text)),
        "project_duration_months": "project_duration_months" in have_params,
        "workforce": "workforce" in have_params,
        "waste_streams": bool(_WASTE_RE.search(text)),
        "land_tenure": bool(_TENURE_RE.search(text)),
        "project_phase": bool(inp.phase) or bool(_PHASE_RE.search(text)),
    }
    for item in r["general"]:
        if not present.get(item["key"], False):
            add(item["key"], item["description"], item["importance"], item["question"])
    for item in r["conditional"]:
        if act_keys & set(item["when_activities_any"]):
            key = item["key"]
            has = key in have_params if key in defs else bool(_HAZMAT_RE.search(text))
            if not has:
                add(key, item["description"], item["importance"], item["question"])

    tax = taxonomy()
    required: list[str] = []
    if sector.value:
        required += tax["sectors"][sector.value].get("required_parameters", [])
    else:
        add("sector", "Project sector", "critical",
            "Which sector does the project belong to (e.g. mining, agriculture, renewable energy, road construction)?")
    for act in act_keys:
        required += tax.get("activity_required_parameters", {}).get(act, [])
    if "power_generation_renewable" in act_keys and find_keywords(text, ["wind"]):
        required += ["turbine_count", "turbine_height_m"]
    for pname in dict.fromkeys(required):
        if pname not in have_params and pname in defs:
            d = defs[pname]
            add(pname, f"{d['label']} ({d['unit']})", "critical",
                f"What is the {d['label'].lower()} ({d['unit']})? This value may determine regulatory screening thresholds.")
    if not activities:
        add("activities", "Project activities", "critical",
            "Which activities will the project involve (e.g. land clearing, excavation, construction, water abstraction, processing, discharges, transport)?")
    order = {"critical": 0, "important": 1, "useful": 2}
    return sorted(missing.values(), key=lambda m: order[m.importance])


# ------------------------------------------------------------------ main


def parse_project(inp: ProjectInput) -> ProjectProfile:
    sources = inp.sources()
    all_text = " ".join(s.text for s in sources)
    sector = detect_sector(inp.sector, all_text)
    activities = extract_activities(sources)
    params = extract_parameters(sources, inp.characteristics)
    receptors = extract_receptors(sources)
    flags = extract_setting_flags(sources)
    geo = assess_location(inp.jurisdiction, inp.latitude, inp.longitude)
    for f in geo.flags:
        if f not in flags:
            flags.append(f)
    missing = identify_missing(inp, sources, params, activities, receptors, sector)

    phases = sorted({p for a in activities for p in a.phases}, key=lambda p: list(ProjectPhase).index(p))
    injection = [f"{s.label}: {sent[:200]}" for s in sources for sent in detect_injection(s.text)]

    def pf(value, provenance=Provenance.user_input) -> ProfileField:
        return ProfileField(value=value, status=InfoStatus.user_provided if value else InfoStatus.missing,
                            provenance=provenance if value else None)

    coords = {"latitude": inp.latitude, "longitude": inp.longitude} if inp.latitude is not None else None
    return ProjectProfile(
        project_name=inp.name, jurisdiction=inp.jurisdiction, sector=sector, location=pf(inp.location),
        region=pf(inp.region), coordinates=pf(coords), phase=pf(inp.phase),
        environmental_setting=pf(inp.environmental_setting),
        activities=activities, phases=phases, parameters=params, pressures=derive_pressures(activities),
        receptors=receptors, setting_flags=sorted(flags), missing_information=missing,
        clarification_questions=[m.clarification_question for m in missing if m.importance != "useful"],
        injection_warnings=injection, geospatial_notes=geo.notes)
