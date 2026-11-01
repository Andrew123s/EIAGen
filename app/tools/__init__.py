from app.tools import (
    citation_verification_tool,
    impact_assessment_tool,
    project_information_tool,
    regulatory_search_tool,
    screening_tool,
)
from app.tools.registry import Tool, ToolContext, ToolError, ToolPermissionError, ToolRegistry


def build_registry() -> ToolRegistry:
    registry = ToolRegistry()
    for module in (project_information_tool, impact_assessment_tool, regulatory_search_tool,
                   screening_tool, citation_verification_tool):
        for tool in module.TOOLS:
            registry.register(tool)
    return registry


__all__ = ["Tool", "ToolContext", "ToolError", "ToolPermissionError", "ToolRegistry", "build_registry"]
