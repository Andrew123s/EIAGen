"""Project information tools: profile extraction, activities, missing information."""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.schemas import MissingInformation, ProjectActivity, ProjectProfile
from app.services.project_parser import ProjectInput, extract_activities, parse_project
from app.tools.registry import Tool, ToolContext


class ProjectRef(BaseModel):
    project_id: int


class TextInput(BaseModel):
    text: str = Field(min_length=1, max_length=50000)


class ActivityList(BaseModel):
    activities: list[ProjectActivity]


class MissingList(BaseModel):
    missing_information: list[MissingInformation]
    clarification_questions: list[str]


def _project_input(ctx: ToolContext) -> ProjectInput:
    inp = ctx.state.get("project_input")
    if inp is None:
        raise RuntimeError("Project input not loaded in tool context")
    return inp


def extract_project_profile(data: ProjectRef, ctx: ToolContext) -> ProjectProfile:
    """Deterministically extract a structured, preliminary project profile."""
    return parse_project(_project_input(ctx))


def identify_project_activities(data: TextInput, ctx: ToolContext) -> ActivityList:
    from app.services.project_parser import SourceText
    from app.schemas import Provenance

    return ActivityList(activities=extract_activities([SourceText("text", data.text, Provenance.user_input)]))


def identify_missing_information(data: ProjectProfile, ctx: ToolContext) -> MissingList:
    return MissingList(missing_information=data.missing_information,
                       clarification_questions=data.clarification_questions)


TOOLS = [
    Tool("extract_project_profile", "Extract a structured preliminary project profile (activities, parameters, "
         "receptors, missing information) from the registered project and uploaded documents.",
         ProjectRef, ProjectProfile, extract_project_profile, ("project_information_extraction",)),
    Tool("identify_project_activities", "Identify project activities in a piece of text using the activity taxonomy.",
         TextInput, ActivityList, identify_project_activities,
         ("project_information_extraction", "evaluation"), llm_callable=True),
    Tool("identify_missing_information", "List missing project information and targeted clarification questions.",
         ProjectProfile, MissingList, identify_missing_information, ("missing_information_check",)),
]
