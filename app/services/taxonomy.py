"""Access to the impact taxonomy and assessment rules, plus keyword matching."""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Any

from app.core.config import load_config

_NEGATION_RE = re.compile(r"\b(no|not|without|zero|never|nor|none)\b[^.;,]*$", re.IGNORECASE)


def taxonomy() -> dict[str, Any]:
    return load_config("impact_taxonomy")


def rules() -> dict[str, Any]:
    return load_config("assessment_rules")


def component_category(component: str) -> str:
    for category, comps in taxonomy()["components"].items():
        if component in comps:
            return category
    return "other"


def component_label(component: str) -> str:
    for comps in taxonomy()["components"].values():
        if component in comps:
            return comps[component]
    return component.replace("_", " ")


@lru_cache(maxsize=4096)
def _keyword_regex(keyword: str) -> re.Pattern[str]:
    kw = keyword.strip().lower()
    escaped = re.escape(kw).replace(r"\ ", r"[\s\-]+")
    if len(kw) <= 4:
        # Short keywords must be whole words (avoid "dam" in "damage", "ore" in "more").
        return re.compile(rf"\b{escaped}(?:s|es|ing|ed)?\b", re.IGNORECASE)
    return re.compile(rf"\b{escaped}", re.IGNORECASE)


def find_keywords(text: str, keywords: list[str], skip_negated: bool = True) -> list[str]:
    """Return the matched text snippets for any keyword in ``text``.

    A match preceded (within the same clause) by a negation such as "no" or
    "without" is ignored, e.g. "no wastewater will be discharged".
    """
    if not text:
        return []
    hits: list[str] = []
    for kw in keywords:
        for m in _keyword_regex(kw).finditer(text):
            prefix = text[max(0, m.start() - 40):m.start()]
            if skip_negated and _NEGATION_RE.search(prefix):
                continue
            start = max(0, text.rfind(" ", 0, max(0, m.start() - 30)) + 1)
            end = text.find(" ", min(len(text), m.end() + 30))
            snippet = text[start:end if end != -1 else len(text)].strip()
            hits.append(snippet)
            break
    return hits
