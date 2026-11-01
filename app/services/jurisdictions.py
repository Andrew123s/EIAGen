"""Jurisdiction configuration helpers (config/jurisdictions.yaml)."""

from __future__ import annotations

from typing import Any

from app.core.config import load_config


def all_jurisdictions() -> dict[str, dict[str, Any]]:
    return load_config("jurisdictions")["jurisdictions"]


def get_jurisdiction(key: str) -> dict[str, Any] | None:
    return all_jurisdictions().get(key)


def is_supported(key: str) -> bool:
    return key in all_jurisdictions()


def collection_relationships(jurisdiction: str, include_related: bool = True) -> dict[str, str]:
    """Map collection id -> relationship ('primary' | 'supranational' | 'reference').

    ``"*"`` means every configured collection (library browsing, not project screening).
    """
    if jurisdiction == "*":
        rels = {key: "primary" for key in all_jurisdictions()}
        rels["international"] = "reference"
        return rels
    cfg = get_jurisdiction(jurisdiction)
    if cfg is None:
        return {jurisdiction: "primary"}
    rels = {c["id"]: c["relationship"] for c in cfg.get("collections", [])}
    if not include_related:
        rels = {k: v for k, v in rels.items() if v == "primary"}
    return rels
