"""Decimal money helpers and the rounding policy.

Every amount in the engine is a :class:`decimal.Decimal`. Floats are refused at every
entry point, because a binary float cannot represent most pound-and-penny amounts
exactly and the result would depend on the order of operations.

All engine arithmetic runs inside :func:`engine_context`, a local decimal context with
a fixed precision and rounding mode, so the global decimal context of the host process
(a Django worker, a test runner) can never change a result.
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from decimal import (
    ROUND_CEILING,
    ROUND_HALF_EVEN,
    ROUND_HALF_UP,
    Context,
    Decimal,
    DivisionByZero,
    InvalidOperation,
    Overflow,
    localcontext,
)
from typing import Final

__all__ = [
    "DEFAULT_ROUNDING",
    "MICRO",
    "PENNY",
    "PRECISION",
    "UNIT",
    "ZERO",
    "MoneyTypeError",
    "RoundingPolicy",
    "engine_context",
    "in_engine_context",
    "money",
    "to_decimal",
]

PRECISION: Final = 28
"""Significant digits for every engine calculation."""

ZERO: Final = Decimal("0")
UNIT: Final = Decimal("1")
PENNY: Final = Decimal("0.01")
MICRO: Final = Decimal("0.000001")

# The context is built once and copied by ``localcontext`` on every entry.
_ENGINE_CONTEXT: Final = Context(
    prec=PRECISION,
    rounding=ROUND_HALF_EVEN,
    traps=[InvalidOperation, DivisionByZero, Overflow],
)


class MoneyTypeError(TypeError):
    """Raised when something that is not an exact decimal is offered as money."""


def engine_context() -> AbstractContextManager[Context]:
    """Return a local decimal context with the engine's fixed precision and traps.

    Precision is 28 significant digits; intermediate rounding is banker's rounding,
    and every policy rounding (gross ceiling, line half-up) is explicit through
    :class:`RoundingPolicy`. Invalid operations, division by zero and overflow raise.
    """
    return localcontext(_ENGINE_CONTEXT)


def in_engine_context[**P, R](fn: Callable[P, R]) -> Callable[P, R]:
    """Decorate a public function so that it always runs inside :func:`engine_context`."""

    @functools.wraps(fn)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        with engine_context():
            return fn(*args, **kwargs)

    return wrapper


def to_decimal(value: object) -> Decimal:
    """Convert ``value`` to a finite ``Decimal``, refusing floats and booleans.

    Accepted: ``Decimal``, ``int`` and decimal strings such as ``"90000.00"``.
    Refused: ``float`` (inexact), ``bool`` (an ``int`` subclass that is never money),
    NaN and infinities, and anything else.
    """
    if isinstance(value, bool):
        raise MoneyTypeError("a boolean is not an amount")
    if isinstance(value, float):
        raise MoneyTypeError(
            "floats are not accepted as amounts; pass a decimal string such as '90000.00'"
        )
    if isinstance(value, Decimal):
        result = value
    elif isinstance(value, int):
        result = Decimal(value)
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            raise MoneyTypeError("an empty string is not an amount")
        try:
            result = Decimal(text)
        except InvalidOperation as exc:
            raise MoneyTypeError(f"{value!r} is not a decimal number") from exc
    else:
        raise MoneyTypeError(f"{type(value).__name__} is not accepted as an amount")
    if not result.is_finite():
        raise MoneyTypeError("NaN and infinite values are not amounts")
    return result


def money(value: Decimal | int | str) -> Decimal:
    """Typed alias of :func:`to_decimal` for amounts written in engine code."""
    return to_decimal(value)


@dataclass(frozen=True, slots=True)
class RoundingPolicy:
    """How the engine rounds, stated once and reported in every result.

    * Solve the gross-up exactly, then **ceil gross cash to the pound** so the net
      guarantee is never under-delivered.
    * Recompute every line from the rounded gross with the full tax functions and
      round each line **half-up to the pound**.
    * Totals are **sums of rounded lines**, so every table foots.
    * Ratios (multiple of salary, marginal cost) are rounded half-up to 2 dp.
    * Foreign-currency components (Turkish lira, converted pounds) are rounded
      half-up to the minor unit (kuruş, penny).
    """

    gross_cash: str = "CEIL_TO_UNIT"
    lines: str = "HALF_UP_TO_UNIT"
    totals: str = "SUM_OF_ROUNDED_LINES"
    ratios: str = "HALF_UP_TO_0.01"

    @staticmethod
    def round_gross(value: Decimal) -> Decimal:
        """Ceil gross cash to the whole pound."""
        with engine_context():
            return value.quantize(UNIT, rounding=ROUND_CEILING)

    @staticmethod
    def round_line(value: Decimal) -> Decimal:
        """Round a result line half-up to the whole pound."""
        with engine_context():
            return value.quantize(UNIT, rounding=ROUND_HALF_UP)

    @staticmethod
    def round_ratio(value: Decimal) -> Decimal:
        """Round a ratio half-up to two decimal places."""
        with engine_context():
            return value.quantize(PENNY, rounding=ROUND_HALF_UP)

    @staticmethod
    def round_minor(value: Decimal) -> Decimal:
        """Round an amount half-up to the minor currency unit (0.01)."""
        with engine_context():
            return value.quantize(PENNY, rounding=ROUND_HALF_UP)

    @staticmethod
    def round_precise(value: Decimal) -> Decimal:
        """Round an intermediate value half-up to six decimal places for display."""
        with engine_context():
            return value.quantize(MICRO, rounding=ROUND_HALF_UP)


DEFAULT_ROUNDING: Final = RoundingPolicy()
