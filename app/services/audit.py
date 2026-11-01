"""Append-only audit trail helper."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models import AuditLog


def record(db: Session, *, actor: str, actor_type: str, action: str, entity_type: str,
           entity_id: str | None = None, project_id: int | None = None,
           screening_run_id: int | None = None, field: str | None = None,
           previous_value: Any = None, new_value: Any = None, comment: str | None = None) -> AuditLog:
    entry = AuditLog(actor=actor, actor_type=actor_type, action=action, entity_type=entity_type,
                     entity_id=entity_id, project_id=project_id, screening_run_id=screening_run_id,
                     field=field, previous_value=previous_value, new_value=new_value, comment=comment)
    db.add(entry)
    return entry
