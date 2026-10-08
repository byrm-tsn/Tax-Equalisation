"""Formatting of amounts for panels and formula text. No floats anywhere."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

_PENNY = Decimal("0.01")


def decimal_string(amount: Decimal) -> str:
    """An amount as a plain two-decimal string, for example ``"1320.00"``."""
    return str(amount.quantize(_PENNY, rounding=ROUND_HALF_UP))


def format_money(amount: Decimal, currency: str = "GBP") -> str:
    """An amount for display: ``£1,320``, ``£12.50`` or ``TRY 3,000``.

    Whole amounts are shown without pence.
    """
    value = amount.quantize(_PENNY, rounding=ROUND_HALF_UP)
    text = f"{value:,.0f}" if value == value.to_integral_value() else f"{value:,.2f}"
    if currency == "GBP":
        return f"-£{text[1:]}" if text.startswith("-") else f"£{text}"
    return f"{currency} {text}"


def format_range(low: Decimal, high: Decimal, currency: str = "GBP") -> str:
    """A range for display, for example ``£150 to £200``."""
    return f"{format_money(low, currency)} to {format_money(high, currency)}"


def plural(count: int, singular: str, plural_form: str | None = None) -> str:
    """``1 year``, ``2 years``; ``1 child``, ``2 children``."""
    word = singular if count == 1 else (plural_form or f"{singular}s")
    return f"{count} {word}"
