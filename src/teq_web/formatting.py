"""en-GB display formatting shared by templates, the narrator and the command line.

Formatting never rounds a figure the engine produced: whole amounts are shown without
pence and anything else to the penny, exactly as the result holds it.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Final

__all__ = [
    "as_decimal",
    "gbp",
    "number",
    "percent",
    "plain_number",
    "uk_date",
    "year_list",
    "year_span",
]

_MONTHS: Final = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)


def as_decimal(value: object) -> Decimal | None:
    """A ``Decimal`` from a decimal, an int or a decimal string; ``None`` otherwise."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int | str):
        try:
            result = Decimal(str(value).strip())
        except InvalidOperation:
            return None
        return result if result.is_finite() else None
    return None


def number(value: object) -> str:
    """``188676`` -> ``188,676``; ``127761.51`` -> ``127,761.51``. Never rounds."""
    amount = as_decimal(value)
    if amount is None:
        return "" if value is None else str(value)
    if amount == amount.to_integral_value():
        return f"{amount:,.0f}"
    exponent = amount.as_tuple().exponent
    places = -exponent if isinstance(exponent, int) and exponent < 0 else 0
    return f"{amount:,.{max(places, 2)}f}"


def gbp(value: object) -> str:
    """Pounds sterling: ``£188,676``, ``£66,000.26``, ``-£1,200``."""
    amount = as_decimal(value)
    if amount is None:
        return "" if value is None else str(value)
    text = number(abs(amount))
    return f"-£{text}" if amount < 0 else f"£{text}"


def plain_number(value: object) -> str:
    """A figure as entered, without trailing zeros: ``55.250000`` -> ``55.25``.

    For values the engine stores at a fixed precision (an exchange rate is kept to six
    decimal places) but the user typed more briefly. Never rounds.
    """
    amount = as_decimal(value)
    if amount is None:
        return "" if value is None else str(value)
    normalised = amount.normalize()
    exponent = normalised.as_tuple().exponent
    if isinstance(exponent, int) and exponent > 0:  # 1E+2 -> 100
        normalised = normalised.quantize(Decimal(1))
    return f"{normalised:,f}"


def percent(value: object) -> str:
    """A rate as a percentage: ``0.47`` -> ``47%``, ``0.075`` -> ``7.5%``."""
    rate = as_decimal(value)
    if rate is None:
        return "" if value is None else str(value)
    scaled = (rate * 100).normalize()
    return f"{scaled:f}%"


def uk_date(value: object) -> str:
    """``2026-10-08`` -> ``8 October 2026``."""
    if isinstance(value, str):
        try:
            parsed = date.fromisoformat(value)
        except ValueError:
            return value
        return uk_date(parsed)
    if isinstance(value, date):
        return f"{value.day} {_MONTHS[value.month - 1]} {value.year}"
    return "" if value is None else str(value)


def year_list(years: object) -> str:
    """``"ALL"`` -> ``every year``; ``(1,)`` -> ``year 1``; ``(1, 2)`` -> ``years 1 and 2``."""
    if years == "ALL":
        return "every year"
    if isinstance(years, list | tuple) and years:
        numbers = [str(year) for year in years]
        if len(numbers) == 1:
            return f"year {numbers[0]}"
        return "years " + ", ".join(numbers[:-1]) + " and " + numbers[-1]
    return str(years)


def year_span(years: Iterable[int]) -> str:
    """Assignment years as a phrase: ``year 2``, ``years 1 and 2``, ``years 2 to 10``.

    Three or more consecutive years read as a range; anything else is listed.
    """
    numbers = sorted(set(years))
    if not numbers:
        return ""
    if len(numbers) >= 3 and numbers == list(range(numbers[0], numbers[-1] + 1)):
        return f"years {numbers[0]} to {numbers[-1]}"
    return year_list(tuple(numbers))
