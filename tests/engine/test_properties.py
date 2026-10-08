"""Property-based tests (hypothesis) for the gross-up and the calculator."""

from __future__ import annotations

from datetime import date
from decimal import ROUND_CEILING, Decimal
from typing import Any

from hypothesis import assume, given
from hypothesis import strategies as st

from teq_engine import LineCode, ScenarioInput, calculate, default_provider
from teq_engine.jurisdictions.gb.income_tax import income_tax
from teq_engine.jurisdictions.gb.nic import employee_nic
from teq_engine.ratesets.schemas import UkIncomeTaxData, UkNicData
from teq_engine.solver import (
    GrossUpProblem,
    bisection_gross_up,
    fixed_point_gross_up,
    solve_gross_up,
)

PENNY = Decimal("0.01")
AS_OF = date(2026, 10, 8)


def pounds(low: int, high: int) -> st.SearchStrategy[Decimal]:
    """Amounts in pence between ``low`` and ``high`` pounds, as exact decimals."""
    return st.integers(low * 100, high * 100).map(lambda pence: Decimal(pence).scaleb(-2))


targets = pounds(0, 600_000)
benefits = pounds(0, 150_000)


@given(n=targets, bik=benefits, nic=st.booleans())
def test_exact_gross_meets_the_guarantee_and_a_penny_less_does_not(
    it_rates: UkIncomeTaxData, nic_rates: UkNicData, n: Decimal, bik: Decimal, nic: bool
) -> None:
    problem = GrossUpProblem(n, bik, it_rates, nic_rates, nic_applies=nic)
    solution = solve_gross_up(problem)
    assert solution.method == "segment"
    assert abs(problem.net(solution.gross) - n) <= Decimal("0.0001")
    if solution.gross >= PENNY:
        assert problem.net(solution.gross - PENNY) < n
    rounded = solution.gross.quantize(Decimal("1"), rounding=ROUND_CEILING)
    assert problem.net(rounded) >= n


@given(n=targets, bik=benefits)
def test_segment_equals_fixed_point_and_bisection(
    it_rates: UkIncomeTaxData, nic_rates: UkNicData, n: Decimal, bik: Decimal
) -> None:
    problem = GrossUpProblem(n, bik, it_rates, nic_rates)
    exact = solve_gross_up(problem).gross
    oracle, _ = fixed_point_gross_up(problem)
    bisected, _ = bisection_gross_up(problem)
    assert abs(exact - oracle) < PENNY
    assert abs(exact - bisected) < PENNY
    # Marginal rate below 100%, so the root is unique: all three agree far below a penny.
    assert abs(exact - oracle) < Decimal("0.00001")


@given(n=targets, bik=benefits, step=pounds(0, 50_000).filter(lambda d: d > 0))
def test_gross_strictly_increasing_in_target(
    it_rates: UkIncomeTaxData, nic_rates: UkNicData, n: Decimal, bik: Decimal, step: Decimal
) -> None:
    low = solve_gross_up(GrossUpProblem(n, bik, it_rates, nic_rates)).gross
    high = solve_gross_up(GrossUpProblem(n + step, bik, it_rates, nic_rates)).gross
    assert high > low


@given(n=targets, bik=benefits, step=pounds(0, 50_000).filter(lambda d: d > 0))
def test_gross_increasing_in_benefits(
    it_rates: UkIncomeTaxData, nic_rates: UkNicData, n: Decimal, bik: Decimal, step: Decimal
) -> None:
    low = solve_gross_up(GrossUpProblem(n, bik, it_rates, nic_rates)).gross
    high = solve_gross_up(GrossUpProblem(n, bik + step, it_rates, nic_rates)).gross
    # Adding a taxable benefit never lowers the gross; it raises it strictly once the
    # extra benefit reaches taxable income.
    assert high >= low
    if low + bik + step > it_rates.personal_allowance:
        assert high > low


@given(t=pounds(0, 400_000), h=pounds(0, 10_000))
def test_tax_and_nic_monotone_with_marginal_below_one(
    it_rates: UkIncomeTaxData, nic_rates: UkNicData, t: Decimal, h: Decimal
) -> None:
    assume(h > 0)
    tax_step = income_tax(t + h, it_rates) - income_tax(t, it_rates)
    nic_step = employee_nic(t + h, nic_rates) - employee_nic(t, nic_rates)
    assert 0 <= tax_step < h
    assert 0 <= nic_step < h
    assert tax_step + nic_step < h


@given(cap_first=pounds(0, 12_000), second=pounds(0, 12_000))
def test_relocation_cap_consumed_once_per_move(cap_first: Decimal, second: Decimal) -> None:
    from teq_engine.jurisdictions.gb.benefits import RelocationPayment, allocate_relocation
    from teq_engine.ratesets.schemas import UkBenefitRulesData

    rs = default_provider().get("UK", "UK_BENEFIT_RULES", date(2026, 4, 6))
    assert rs is not None
    rules = rs.data_as(UkBenefitRulesData).relocation
    allocations = allocate_relocation(
        [RelocationPayment(1, "a", cap_first), RelocationPayment(2, "b", second)], rules
    )
    exempt = sum(a.exempt for a in allocations)
    assert exempt == min(cap_first + second, rules.exemption_cap)
    assert sum(a.taxable for a in allocations) == cap_first + second - exempt


@given(salary=pounds(0, 1_000_000), rate=st.integers(20_000_000, 200_000_000))
def test_lira_round_trip_within_rounding(salary: Decimal, rate: int) -> None:
    fx = Decimal(rate).scaleb(-6)
    data = _scenario(salary, Decimal("0"), [], "UK_NIC", None)
    data["fx"] = {"rate": str(fx), "as_of": "2026-10-08"}
    as_try = ScenarioInput.model_validate(data).annual_salary_try()
    assert as_try is not None
    data["salary"] = {"amount": str(as_try), "currency": "TRY"}
    back = ScenarioInput.model_validate(data).annual_salary_gbp()
    assert abs(back - salary) <= PENNY


def _scenario(
    salary: Decimal,
    hypo_share: Decimal,
    items: list[dict[str, Any]],
    social: str,
    fx_rate: Decimal | None,
    years: int = 2,
) -> dict[str, Any]:
    override = (salary * hypo_share).quantize(PENNY)
    if salary > 0 and override >= salary:
        override = Decimal("0.00")
    data: dict[str, Any] = {
        "route": {"home": "TR", "host": "GB", "region": "ENG"},
        "assignment": {"length_years": years},
        "salary": {"amount": str(salary), "currency": "GBP"},
        "hypothetical_tax": {"method": "OVERRIDE", "override": str(override if salary else 0)},
        "items": items,
        "assumptions": {"social_security": social},
    }
    if fx_rate is not None:
        data["fx"] = {"rate": str(fx_rate), "as_of": "2026-10-08"}
    return data


@st.composite
def scenarios(draw: st.DrawFn) -> dict[str, Any]:
    years = draw(st.integers(1, 4))
    salary = draw(pounds(0, 400_000))
    share = Decimal(draw(st.integers(0, 45))).scaleb(-2)
    items: list[dict[str, Any]] = []
    if draw(st.booleans()):
        items.append(
            {
                "id": "cola",
                "kind": "COLA",
                "amount": str(draw(pounds(0, 30_000))),
                "currency": "GBP",
            }
        )
    if draw(st.booleans()):
        items.append(
            {
                "id": "housing",
                "kind": "HOUSING",
                "amount": str(draw(pounds(0, 80_000))),
                "currency": "GBP",
                "years": sorted(draw(st.sets(st.integers(1, years), min_size=1))),
            }
        )
    if draw(st.booleans()):
        items.append(
            {
                "id": "relocation",
                "kind": "RELOCATION",
                "amount": str(draw(pounds(0, 20_000))),
                "currency": "GBP",
                "frequency": "ONE_OFF",
                "years": sorted(draw(st.sets(st.integers(1, years), min_size=1, max_size=2))),
            }
        )
    if draw(st.booleans()):
        items.append(
            {
                "id": "pension",
                "kind": "PENSION_EMPLOYER",
                "amount": str(draw(pounds(0, 20_000))),
                "currency": "GBP",
            }
        )
    social = draw(st.sampled_from(["UK_NIC", "HOME_SCHEME_AGREEMENT"]))
    fx = (
        Decimal(draw(st.integers(30_000_000, 120_000_000))).scaleb(-6)
        if social != "UK_NIC" or draw(st.booleans())
        else None
    )
    return _scenario(salary, share, items, social, fx, years)


@given(data=scenarios())
def test_calculation_invariants(data: dict[str, Any]) -> None:
    inputs = ScenarioInput.model_validate(data)
    result = calculate(inputs, default_provider(), rates_as_of=AS_OF)
    total = Decimal(0)
    for year in result.years:
        # both decompositions foot
        assert year.decomposition.foots
        assert year.line(LineCode.TOTAL_EMPLOYER_COST) == year.decomposition.recipient_view
        # after rounding the guarantee is met
        assert year.gross_up.net_delivered >= year.net_guarantee.net_cash_target
        assert year.gross_up.gross_rounded >= year.gross_up.gross_exact
        assert year.gross_up.method == "segment"
        total += year.line(LineCode.TOTAL_EMPLOYER_COST)
    assert result.totals.total_employer_cost == total
    # determinism, and no float anywhere
    again = calculate(ScenarioInput.model_validate(data), default_provider(), rates_as_of=AS_OF)
    assert again.to_json() == result.to_json()
    assert again.inputs_hash == result.inputs_hash
    _no_float(result.model_dump())


def _no_float(node: object) -> None:
    assert not isinstance(node, float)
    if isinstance(node, dict):
        for value in node.values():
            _no_float(value)
    elif isinstance(node, list | tuple):
        for value in node:
            _no_float(value)
