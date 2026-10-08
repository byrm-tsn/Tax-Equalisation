"""Boundary tests at every UK threshold and one penny either side.

Thresholds are read from the rate sets, never typed in, so the tests follow the data.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from teq_engine import LineCode, ScenarioInput, calculate, default_provider
from teq_engine.jurisdictions.gb.income_tax import income_tax, personal_allowance
from teq_engine.jurisdictions.gb.nic import class_1a, employee_nic, employer_nic
from teq_engine.ratesets.schemas import UkIncomeTaxData, UkNicData
from teq_engine.solver import GrossUpProblem, fixed_point_gross_up, solve_gross_up

PENNY = Decimal("0.01")


def _kink(f: Callable[[Decimal], Decimal], at: Decimal) -> tuple[Decimal, Decimal]:
    """Slopes just left and right of ``at`` (measured over one penny)."""
    return (f(at) - f(at - PENNY)) / PENNY, (f(at + PENNY) - f(at)) / PENNY


def test_income_tax_at_allowance(it_rates: UkIncomeTaxData) -> None:
    pa = it_rates.personal_allowance
    basic = it_rates.bands[0].rate
    assert income_tax(pa, it_rates) == 0
    assert income_tax(pa - PENNY, it_rates) == 0
    assert income_tax(pa + PENNY, it_rates) == PENNY * basic
    assert _kink(lambda t: income_tax(t, it_rates), pa) == (0, basic)


def test_income_tax_at_basic_rate_limit(it_rates: UkIncomeTaxData) -> None:
    pa = it_rates.personal_allowance
    basic_limit = it_rates.bands[0].upto
    assert basic_limit is not None
    at = pa + basic_limit  # 50,270
    assert at == Decimal("50270")
    assert income_tax(at, it_rates) == basic_limit * it_rates.bands[0].rate  # 7,540
    assert _kink(lambda t: income_tax(t, it_rates), at) == (
        it_rates.bands[0].rate,
        it_rates.bands[1].rate,
    )


def test_income_tax_at_taper_start(it_rates: UkIncomeTaxData) -> None:
    start = it_rates.taper_start
    higher = it_rates.bands[1].rate
    assert personal_allowance(start, it_rates) == it_rates.personal_allowance
    assert personal_allowance(start + PENNY, it_rates) == it_rates.personal_allowance - Decimal(
        "0.005"
    )
    left, right = _kink(lambda t: income_tax(t, it_rates), start)
    assert left == higher
    assert right == higher * (1 + it_rates.taper_rate)  # 60%


def test_income_tax_at_taper_end(it_rates: UkIncomeTaxData) -> None:
    end = it_rates.taper_end
    assert end == Decimal("125140")
    assert personal_allowance(end, it_rates) == 0
    assert personal_allowance(end - PENNY, it_rates) == Decimal("0.005")
    assert personal_allowance(end + PENNY, it_rates) == 0
    higher, additional = it_rates.bands[1].rate, it_rates.bands[2].rate
    left, right = _kink(lambda t: income_tax(t, it_rates), end)
    assert left == higher * (1 + it_rates.taper_rate)
    assert right == additional
    assert income_tax(end, it_rates) == Decimal("42516")


def test_employee_nic_thresholds(nic_rates: UkNicData) -> None:
    ee = nic_rates.employee
    f = lambda g: employee_nic(g, nic_rates)  # noqa: E731
    assert f(ee.primary_threshold) == 0
    assert f(ee.primary_threshold - PENNY) == 0
    assert _kink(f, ee.primary_threshold) == (0, ee.main_rate)
    assert f(ee.upper_earnings_limit) == (ee.upper_earnings_limit - ee.primary_threshold) * (
        ee.main_rate
    )
    assert _kink(f, ee.upper_earnings_limit) == (ee.main_rate, ee.upper_rate)


def test_employer_nic_threshold(nic_rates: UkNicData) -> None:
    st = nic_rates.employer.secondary_threshold
    assert st == Decimal("5000")
    f = lambda g: employer_nic(g, nic_rates)  # noqa: E731
    assert f(st) == 0
    assert f(st - PENNY) == 0
    assert f(st + PENNY) == PENNY * nic_rates.employer.rate
    assert class_1a(Decimal("30000"), nic_rates) == Decimal("4500")


def _thresholds(it_rates: UkIncomeTaxData, nic_rates: UkNicData) -> list[Decimal]:
    return [
        it_rates.personal_allowance,
        nic_rates.employee.upper_earnings_limit,
        it_rates.taper_start,
        it_rates.taper_end,
        nic_rates.employer.secondary_threshold,
    ]


@pytest.mark.parametrize("offset", [Decimal("-0.01"), Decimal("0"), Decimal("0.01")])
@pytest.mark.parametrize("index", range(5))
def test_solver_lands_on_each_threshold(
    it_rates: UkIncomeTaxData, nic_rates: UkNicData, index: int, offset: Decimal
) -> None:
    gross = _thresholds(it_rates, nic_rates)[index] + offset
    probe = GrossUpProblem(Decimal("0"), Decimal("0"), it_rates, nic_rates)
    target = probe.net(gross)
    problem = GrossUpProblem(target, Decimal("0"), it_rates, nic_rates)
    solution = solve_gross_up(problem)
    assert solution.method == "segment"
    assert abs(solution.gross - gross) < Decimal("0.000001")
    oracle, _ = fixed_point_gross_up(problem)
    assert abs(oracle - gross) < Decimal("0.00001")


def _one_year(
    salary: str, override: str, items: list[dict[str, Any]] | None = None
) -> ScenarioInput:
    return ScenarioInput.model_validate(
        {
            "route": {"home": "TR", "host": "GB", "region": "ENG"},
            "assignment": {"length_years": 1},
            "salary": {"amount": salary, "currency": "GBP"},
            "hypothetical_tax": {"method": "OVERRIDE", "override": override},
            "items": items or [],
        }
    )


@pytest.mark.parametrize(
    ("net", "gross", "employer_nic_line"),
    [
        ("4999.99", "5000", "0"),
        ("5000.00", "5000", "0"),
        ("5000.01", "5001", "0"),
        ("12569.99", "12570", "1136"),
        ("12570.00", "12570", "1136"),
        ("12570.01", "12571", "1136"),
    ],
)
def test_full_calculation_at_low_thresholds(net: str, gross: str, employer_nic_line: str) -> None:
    result = calculate(_one_year(net, "0.00"), default_provider(), rates_as_of=date(2026, 10, 8))
    y1 = result.year(1)
    assert y1.line(LineCode.GROSS_CASH) == Decimal(gross)
    assert y1.line(LineCode.EMPLOYER_NIC) == Decimal(employer_nic_line)
    assert y1.gross_up.net_delivered >= Decimal(net)


def test_rounding_across_the_upper_earnings_limit(
    it_rates: UkIncomeTaxData, nic_rates: UkNicData
) -> None:
    # A gross just below the UEL that ceils exactly onto it: lines are recomputed from
    # the rounded gross with the full functions, so crossing the breakpoint is harmless.
    uel = nic_rates.employee.upper_earnings_limit
    probe = GrossUpProblem(Decimal("0"), Decimal("0"), it_rates, nic_rates)
    target = probe.net(uel - Decimal("0.40"))
    result = calculate(
        _one_year(str(target.quantize(PENNY)), "0.00"),
        default_provider(),
        rates_as_of=date(2026, 10, 8),
    )
    y1 = result.year(1)
    assert y1.line(LineCode.GROSS_CASH) == uel
    assert y1.line(LineCode.EMPLOYEE_NIC) == Decimal("3016")
    assert y1.line(LineCode.INCOME_TAX) == Decimal("7540")
    assert y1.gross_up.net_delivered >= target.quantize(PENNY)


def test_zero_salary_and_one_and_ten_years() -> None:
    zero = calculate(_one_year("0.00", "0.00"), default_provider(), rates_as_of=date(2026, 10, 8))
    assert zero.year(1).line(LineCode.TOTAL_EMPLOYER_COST) == 0
    assert not zero.year(1).has_line(LineCode.MULTIPLE_OF_SALARY)
    data = _one_year("90000.00", "30000.00").model_dump(mode="json")
    data["assignment"]["length_years"] = 10
    ten = calculate(
        ScenarioInput.model_validate(data), default_provider(), rates_as_of=date(2026, 10, 8)
    )
    assert len(ten.years) == 10
    assert ten.year(10).uk_tax_year == "2035-36"
    assert ten.year(10).tr_calendar_year == 2035
    assert len({y.line(LineCode.TOTAL_EMPLOYER_COST) for y in ten.years}) == 1


def test_benefit_only_scenario_grosses_up_the_tax_on_the_benefit() -> None:
    # No cash promised at all, but a taxable benefit: the employer still bears the tax.
    inputs = _one_year(
        "0.00",
        "0.00",
        [{"id": "housing", "kind": "HOUSING", "amount": "30000.00", "currency": "GBP"}],
    )
    result = calculate(inputs, default_provider(), rates_as_of=date(2026, 10, 8))
    y1 = result.year(1)
    assert y1.line(LineCode.GROSS_CASH) > 0
    assert y1.gross_up.net_delivered >= 0
    assert y1.line(LineCode.CLASS_1A) == Decimal("4500")
