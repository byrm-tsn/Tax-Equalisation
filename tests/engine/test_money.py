"""Money helpers, the rounding policy and decimal-context isolation."""

from __future__ import annotations

import decimal
from datetime import date
from decimal import Decimal

import pytest

from teq_engine import BundledProvider, calculate, reference_example
from teq_engine.money import (
    DEFAULT_ROUNDING,
    PRECISION,
    MoneyTypeError,
    engine_context,
    money,
    to_decimal,
)


@pytest.mark.parametrize(
    "bad", [1.5, 0.1, float("nan"), True, None, object(), "", "abc", "NaN", "Infinity"]
)
def test_to_decimal_refuses_non_decimals(bad: object) -> None:
    with pytest.raises(MoneyTypeError):
        to_decimal(bad)


@pytest.mark.parametrize(
    ("good", "expected"),
    [
        ("90000.00", Decimal("90000.00")),
        (Decimal("1.5"), Decimal("1.5")),
        (12, Decimal(12)),
        (" 7 ", Decimal(7)),
    ],
)
def test_to_decimal_accepts_exact_values(good: object, expected: Decimal) -> None:
    assert to_decimal(good) == expected
    assert money(expected) == expected


def test_rounding_policy() -> None:
    r = DEFAULT_ROUNDING
    assert r.round_gross(Decimal("127761.51")) == Decimal("127762")
    assert r.round_gross(Decimal("127761.00")) == Decimal("127761")
    assert r.round_gross(Decimal("127761.0000001")) == Decimal("127762")
    assert r.round_line(Decimal("57195.5")) == Decimal("57196")
    assert r.round_line(Decimal("57195.49")) == Decimal("57195")
    assert r.round_line(Decimal("4565.84")) == Decimal("4566")
    assert r.round_ratio(Decimal("2.0964")) == Decimal("2.10")
    assert r.round_ratio(Decimal("1.0175")) == Decimal("1.02")
    assert r.round_minor(Decimal("41871.2976")) == Decimal("41871.30")


def test_engine_context_is_fixed() -> None:
    with decimal.localcontext() as outer:
        outer.prec = 3
        outer.rounding = decimal.ROUND_DOWN
        with engine_context() as ctx:
            assert ctx.prec == PRECISION
            assert ctx.rounding == decimal.ROUND_HALF_EVEN
            assert Decimal(1) / Decimal(3) == Decimal("0.3333333333333333333333333333")


def test_host_decimal_context_cannot_change_results(provider: BundledProvider) -> None:
    inputs = reference_example()
    baseline = calculate(inputs, provider, rates_as_of=date(2026, 10, 8)).to_json()
    with decimal.localcontext() as ctx:
        ctx.prec = 4
        ctx.rounding = decimal.ROUND_FLOOR
        hostile = calculate(inputs, provider, rates_as_of=date(2026, 10, 8)).to_json()
    assert hostile == baseline


def test_engine_traps_division_by_zero() -> None:
    with engine_context(), pytest.raises(decimal.DivisionByZero):
        _ = Decimal(1) / Decimal(0)
