"""The number check every narrator must pass: no figure that is not in the result.

:func:`figure_set` collects every number that appears anywhere in a result (amounts,
rates, years, dates, codes' digits); :func:`numbers_in` extracts the numbers a piece of
text states. A percentage counts as its fraction (``47%`` is ``0.47``) as well as its
digits, so ``47%`` matches a marginal rate of ``0.47``.

Text the user typed (item labels and the exchange-rate source) is not a figure: its
digits are left out of the figure set, wherever the result repeats that text, and are
ignored where a narrative quotes it. So a label such as ``Bonus 2027`` can be named, but
cannot vouch for an invented ``2,027``.

:func:`panel_figure_set` and :func:`unknown_panel_numbers` apply the same check to the
immigration guidance panel: every number the panel holds (cost amounts and formulas,
subtotals, timeline days and weeks, funds, eligibility and other texts), leaving out the
answers typed into its free-text questions (the occupation code and the nationality).
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Iterator, Mapping
from decimal import Decimal, InvalidOperation
from typing import Any, Final

from teq_engine import CalculationResult

__all__ = [
    "figure_set",
    "numbers_in",
    "panel_figure_set",
    "panel_user_texts",
    "unknown_numbers",
    "unknown_panel_numbers",
    "user_texts",
]

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


def user_texts(result: CalculationResult) -> tuple[str, ...]:
    """The free text the user supplied, longest first: item labels and the FX source."""
    texts = {item.label for item in result.items if item.label}
    fx = result.hypothetical_tax.fx
    if fx is not None and fx.source:
        texts.add(fx.source)
    return tuple(sorted(texts, key=lambda text: (-len(text), text)))


def _without(text: str, texts: Iterable[str]) -> str:
    """``text`` with each whole occurrence of ``texts`` blanked (case-insensitive)."""
    for user_text in texts:
        pattern = rf"(?<!\w){re.escape(user_text)}(?!\w)"
        text = re.sub(pattern, " ", text, flags=re.IGNORECASE)
    return text


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
    """Every number that appears anywhere in ``result`` (and percentages as fractions),
    leaving out the digits of text the user typed (see :func:`user_texts`)."""
    figures: set[Decimal] = set()
    typed = user_texts(result)
    for text in _strings(json.loads(result.to_json())):
        for value, pct in _tokens(_without(text, typed)):
            figures.add(value)
            if pct:
                figures.add(value / 100)
    return figures


def unknown_numbers(paragraphs: Iterable[str], result: CalculationResult) -> set[Decimal]:
    """Numbers stated in ``paragraphs`` that do not appear in ``result``.

    Digits inside a user label the paragraph quotes are ignored; they are not figures.
    """
    known = figure_set(result)
    typed = user_texts(result)
    stated: set[Decimal] = set()
    for paragraph in paragraphs:
        stated |= numbers_in(_without(paragraph, typed))
    return {value for value in stated if value not in known}


def panel_user_texts(panel: Mapping[str, Any]) -> tuple[str, ...]:
    """The answers typed into the panel's free-text questions, longest first."""
    texts = {
        str(question["value"])
        for question in panel["tailoring"]["questions"]
        if question["type"] == "text" and question["answered"] and question["value"]
    }
    return tuple(sorted(texts, key=lambda text: (-len(text), text)))


def panel_figure_set(panel: Mapping[str, Any]) -> set[Decimal]:
    """Every number that appears anywhere in the immigration ``panel`` (and percentages as
    fractions), leaving out the digits of answers typed into free-text questions."""
    figures: set[Decimal] = set()
    typed = panel_user_texts(panel)
    questions = panel["tailoring"]["questions"]
    free_text = [q for q in questions if q["type"] == "text"]
    data = dict(panel)
    # The free-text questions' own values and labels are the user's words: drop them.
    data["tailoring"] = {
        **panel["tailoring"],
        "questions": [
            {k: v for k, v in q.items() if k not in ("value", "value_label")}
            if q in free_text
            else q
            for q in questions
        ],
    }
    for text in _strings(json.loads(json.dumps(data))):
        for value, pct in _tokens(_without(text, typed)):
            figures.add(value)
            if pct:
                figures.add(value / 100)
    return figures


def unknown_panel_numbers(paragraphs: Iterable[str], panel: Mapping[str, Any]) -> set[Decimal]:
    """Numbers stated in ``paragraphs`` that do not appear in the immigration ``panel``."""
    known = panel_figure_set(panel)
    typed = panel_user_texts(panel)
    stated: set[Decimal] = set()
    for paragraph in paragraphs:
        stated |= numbers_in(_without(paragraph, typed))
    return {value for value in stated if value not in known}
