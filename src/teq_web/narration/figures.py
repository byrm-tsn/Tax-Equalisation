"""The number check every narrator must pass: no figure that is not in the result.

:func:`figure_set` collects every number that appears anywhere in a result (amounts,
rates, years, dates, codes' digits); :func:`numbers_in` extracts the numbers a piece of
text states. A percentage counts as its fraction (``47%`` is ``0.47``) as well as its
digits, so ``47%`` matches a marginal rate of ``0.47``.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Iterator
from decimal import Decimal, InvalidOperation
from typing import Final

from teq_engine import CalculationResult

__all__ = ["figure_set", "numbers_in", "unknown_numbers"]

_NUMBER: Final = re.compile(r"(?<![\w.])(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?(%?)")


def _tokens(text: str) -> Iterator[tuple[Decimal, bool]]:
    for match in _NUMBER.finditer(text):
        whole, fraction, pct = match.groups()
        try:
            value = Decimal(whole.replace(",", "") + (fraction or ""))
        except InvalidOperation:  # pragma: no cover - the pattern only matches digits
            continue
        yield value, bool(pct)


def numbers_in(text: str) -> set[Decimal]:
    """Every number stated in ``text``; a percentage is returned as its fraction."""
    return {value / 100 if pct else value for value, pct in _tokens(text)}


def _strings(data: object) -> Iterable[str]:
    if isinstance(data, dict):
        for value in data.values():
            yield from _strings(value)
    elif isinstance(data, list):
        for value in data:
            yield from _strings(value)
    elif isinstance(data, bool) or data is None:
        return
    else:
        yield str(data)


def figure_set(result: CalculationResult) -> set[Decimal]:
    """Every number that appears anywhere in ``result`` (and percentages as fractions)."""
    figures: set[Decimal] = set()
    for text in _strings(json.loads(result.to_json())):
        for value, pct in _tokens(text):
            figures.add(value)
            if pct:
                figures.add(value / 100)
    return figures


def unknown_numbers(paragraphs: Iterable[str], result: CalculationResult) -> set[Decimal]:
    """Numbers stated in ``paragraphs`` that do not appear in ``result``."""
    known = figure_set(result)
    stated: set[Decimal] = set()
    for paragraph in paragraphs:
        stated |= numbers_in(paragraph)
    return {value for value in stated if value not in known}
