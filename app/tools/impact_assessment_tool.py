"""Environmental impact identification tool."""

from __future__ import annotations

from pydantic import BaseModel

from app.schemas import PotentialImpact, ProjectProfile
from app.services.impact_classifier import identify_impacts
from app.tools.registry import Tool, ToolContext


class ImpactList(BaseModel):
    impacts: list[PotentialImpact]


def identify_environmental_impacts(data: ProjectProfile, ctx: ToolContext) -> ImpactList:
    return ImpactList(impacts=identify_impacts(data))


TOOLS = [
    Tool("identify_environmental_impacts", "Identify potential environmental and social impacts, pathways and "
         "receptors from the validated project profile, with transparent risk prioritisation.",
         ProjectProfile, ImpactList, identify_environmental_impacts, ("environmental_impact_identification",)),
]
