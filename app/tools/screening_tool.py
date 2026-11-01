"""Screening criteria evaluation tool (deterministic; the LLM cannot call it)."""

from __future__ import annotations

from pydantic import BaseModel

from app.schemas import MissingInformation, PotentialImpact, ProjectProfile, ScreeningAssessment
from app.services.screening_engine import assess
from app.tools.registry import Tool, ToolContext


class ScreeningInput(BaseModel):
    profile: ProjectProfile
    impacts: list[PotentialImpact]
    missing_information: list[MissingInformation]
    project_text: str


def evaluate_screening_criteria(data: ScreeningInput, ctx: ToolContext) -> ScreeningAssessment:
    return assess(data.profile, data.impacts, data.project_text, data.missing_information)


TOOLS = [
    Tool("evaluate_screening_criteria", "Evaluate the jurisdiction's encoded statutory screening criteria and "
         "summarise environmental risk screening. Produces controlled, preliminary outcome categories only.",
         ScreeningInput, ScreeningAssessment, evaluate_screening_criteria, ("preliminary_eia_screening",)),
]
