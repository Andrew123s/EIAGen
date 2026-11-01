"""Tool registry.

Each tool has a defined purpose, Pydantic input/output schemas and a list of
workflow stages in which it may run. Every invocation is validated on the way
in and out and logged to the ``tool_executions`` table. Tool calls requested by
an LLM go through ``invoke_from_llm``, which only permits tools on the current
stage's allowlist - an LLM cannot reach arbitrary code, the filesystem, or a
tool that issues decisions.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models import ToolExecution

log = get_logger("tools")


class ToolError(RuntimeError):
    pass


class ToolPermissionError(ToolError):
    pass


@dataclass
class ToolContext:
    db: Session
    project_id: int | None = None
    screening_run_id: int | None = None
    stage: str = "unspecified"
    state: dict[str, Any] = field(default_factory=dict)   # shared, in-process objects (registry, documents)


@dataclass
class Tool:
    name: str
    purpose: str
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    fn: Callable[[BaseModel, ToolContext], BaseModel]
    stages: tuple[str, ...]
    llm_callable: bool = False

    def schema(self) -> dict[str, Any]:
        return {"name": self.name, "description": self.purpose, "stages": list(self.stages),
                "llm_callable": self.llm_callable, "input_schema": self.input_model.model_json_schema(),
                "output_schema": self.output_model.model_json_schema()}


def _summary(obj: Any, limit: int = 400) -> str:
    try:
        text = obj.model_dump_json() if isinstance(obj, BaseModel) else json.dumps(obj, default=str)
    except Exception:
        text = str(obj)
    return text if len(text) <= limit else text[:limit] + f"... ({len(text)} chars)"


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool:
        if name not in self._tools:
            raise ToolError(f"Unknown tool '{name}'")
        return self._tools[name]

    def names(self) -> list[str]:
        return sorted(self._tools)

    def schemas(self) -> list[dict[str, Any]]:
        return [self._tools[n].schema() for n in self.names()]

    def invoke(self, name: str, payload: BaseModel | dict, ctx: ToolContext) -> BaseModel:
        tool = self.get(name)
        if ctx.stage not in tool.stages:
            ctx.db.add(ToolExecution(screening_run_id=ctx.screening_run_id, project_id=ctx.project_id,
                                     stage=ctx.stage, tool_name=name, status="refused",
                                     input_summary=_summary(payload),
                                     output_summary="Tool not permitted in this workflow stage"))
            raise ToolPermissionError(f"Tool '{name}' is not permitted in stage '{ctx.stage}'")
        started = time.perf_counter()
        status, out_summary = "failed", None
        try:
            data = tool.input_model.model_validate(payload.model_dump() if isinstance(payload, BaseModel) else payload)
            result = tool.fn(data, ctx)
            result = tool.output_model.model_validate(result.model_dump() if isinstance(result, BaseModel) else result)
            status, out_summary = "completed", _summary(result)
            return result
        except ValidationError as exc:
            out_summary = f"schema validation failed: {exc.error_count()} errors"
            raise ToolError(f"Tool '{name}' rejected invalid data: {exc.error_count()} validation errors") from exc
        except Exception as exc:
            out_summary = f"{type(exc).__name__}: {exc}"[:400]
            raise
        finally:
            elapsed = (time.perf_counter() - started) * 1000
            ctx.db.add(ToolExecution(screening_run_id=ctx.screening_run_id, project_id=ctx.project_id,
                                     stage=ctx.stage, tool_name=name, status=status,
                                     input_summary=_summary(payload), output_summary=out_summary,
                                     duration_ms=round(elapsed, 2)))
            log.info("tool=%s stage=%s status=%s %.0fms", name, ctx.stage, status, elapsed)

    def invoke_from_llm(self, name: str, arguments: dict, ctx: ToolContext) -> BaseModel:
        """Entry point for tool calls proposed by an LLM: allowlist enforced."""
        tool = self._tools.get(name)
        if tool is None or not tool.llm_callable or ctx.stage not in tool.stages:
            ctx.db.add(ToolExecution(screening_run_id=ctx.screening_run_id, project_id=ctx.project_id,
                                     stage=ctx.stage, tool_name=name, status="refused",
                                     input_summary=_summary(arguments),
                                     output_summary="LLM-requested tool not permitted in this stage"))
            raise ToolPermissionError(f"LLM requested tool '{name}', which is not permitted in stage '{ctx.stage}'")
        return self.invoke(name, arguments, ctx)
