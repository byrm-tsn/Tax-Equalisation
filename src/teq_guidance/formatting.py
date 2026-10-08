"""Formatting of amounts for panels and formula text. No floats anywhere.

All decimal arithmetic in the guidance package runs inside :func:`decimal_context`, a
local context with 28 significant digits and ``ROUND_HALF_UP``, so the host process's
global decimal context (a low precision, another rounding mode, extra traps) can never
change a figure or make a panel fail.
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from contextlib import AbstractContextManager
from decimal import (
    ROUND_HALF_UP,
    Context,
    Decimal,
    DivisionByZero,
    InvalidOperation,
    Overflow,
    localcontext,
)
from typing import Final

_PENNY = Decimal("0.01")

DECIMAL_PRECISION: Final = 28
"""Significant digits for every guidance calculation."""

_CONTEXT: Final = Context(
    prec=DECIMAL_PRECISION,
    rounding=ROUND_HALF_UP,
    traps=[InvalidOperation, DivisionByZero, Overflow],
)


def decimal_context() -> AbstractContextManager[Context]:
    """A local decimal context: precision 28, ``ROUND_HALF_UP``, fixed traps."""
    return localcontext(_CONTEXT)


def in_decimal_context[**P, R](fn: Callable[P, R]) -> Callable[P, R]:
    """Decorate a function so that it always runs inside :func:`decimal_context`."""

    @functools.wraps(fn)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        with decimal_context():
            return fn(*args, **kwargs)

    return wrapper


@in_decimal_context
def decimal_string(amount: Decimal) -> str:
    """An amount as a plain two-decimal string, for example ``"1320.00"``."""
    return str(amount.quantize(_PENNY, rounding=ROUND_HALF_UP))


@in_decimal_context
def format_money(amount: Decimal, currency: str = "GBP") -> str:
    """An amount for display: ``£1,320``, ``£12.50`` or ``TRY 3,000``.

    Whole amounts are shown without pence.
    """
    value = amount.quantize(_PENNY, rounding=ROUND_HALF_UP)
    text = f"{value:,.0f}" if value == value.to_integral_value() else f"{value:,.2f}"
    if currency == "GBP":
        return f"-£{text[1:]}" if text.startswith("-") else f"£{text}"
    return f"{currency} {text}"


@in_decimal_context
def format_range(low: Decimal, high: Decimal, currency: str = "GBP") -> str:
    """A range for display, for example ``£150 to £200``."""
    return f"{format_money(low, currency)} to {format_money(high, currency)}"


def plural(count: int, singular: str, plural_form: str | None = None) -> str:
    """``1 year``, ``2 years``; ``1 child``, ``2 children``."""
    word = singular if count == 1 else (plural_form or f"{singular}s")
    return f"{count} {word}"
