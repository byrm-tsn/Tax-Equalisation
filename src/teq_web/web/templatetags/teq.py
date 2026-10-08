"""Template filters for en-GB display: ``gbp``, ``number``, ``percent``, ``uk_date``."""

from __future__ import annotations

from typing import Any

from django import template

from teq_web import formatting

register = template.Library()


@register.filter
def gbp(value: Any) -> str:
    """``188676`` -> ``£188,676``; ``66000.26`` -> ``£66,000.26``."""
    return formatting.gbp(value)


@register.filter
def number(value: Any) -> str:
    """``188676`` -> ``188,676``."""
    return formatting.number(value)


@register.filter
def percent(value: Any) -> str:
    """``0.47`` -> ``47%``."""
    return formatting.percent(value)


@register.filter
def uk_date(value: Any) -> str:
    """``2026-10-08`` -> ``8 October 2026``."""
    return formatting.uk_date(value)


@register.filter
def short_hash(value: Any) -> str:
    """``sha256:2222d2fd…`` shortened for display (the full value is in the title)."""
    text = str(value)
    return text[:19] + "…" if len(text) > 20 else text
