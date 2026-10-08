"""Solver internals: breakpoints, the fallback path and the failure path."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

import teq_engine.calculator as calculator_module
import teq_engine.solver as solver_module
from teq_engine import SolverError, calculate, default_provider, reference_example
from teq_engine.piecewise import (
    SegmentSolveError,
    bisect_increasing,
    fixed_point,
    solve_piecewise_linear,
)
from teq_engine.ratesets.schemas import UkIncomeTaxData, UkNicData
from teq_engine.solver import GrossUpProblem, gross_breakpoints, solve_gross_up


def test_reference_breakpoints(it_rates: UkIncomeTaxData, nic_rates: UkNicData) -> None:
    problem = GrossUpProblem(Decimal("66000"), Decimal("30000"), it_rates, nic_rates)
    # 12,570 - BIK is negative and dropped; 50,270 - BIK, 100,000 - BIK, 125,140 - BIK;
    # NIC thresholds 12,570 and 50,270 in cash space.
    assert gross_breakpoints(problem) == (
        Decimal("12570"),
        Decimal("20270"),
        Decimal("50270"),
        Decimal("70000"),
        Decimal("95140"),
    )


def test_breakpoints_without_benefits(it_rates: UkIncomeTaxData, nic_rates: UkNicData) -> None:
    problem = GrossUpProblem(Decimal("1"), Decimal("0"), it_rates, nic_rates)
    assert gross_breakpoints(problem) == (
        Decimal("12570"),
        Decimal("50270"),
        Decimal("100000"),
        Decimal("125140"),
    )


def test_home_scheme_breakpoints_exclude_nic(
    it_rates: UkIncomeTaxData, nic_rates: UkNicData
) -> None:
    problem = GrossUpProblem(Decimal("1"), Decimal("30000"), it_rates, nic_rates, nic_applies=False)
    assert Decimal("12570") not in gross_breakpoints(problem)


def test_wrong_breakpoints_fall_back_to_bisection(
    it_rates: UkIncomeTaxData, nic_rates: UkNicData
) -> None:
    problem = GrossUpProblem(Decimal("66000"), Decimal("30000"), it_rates, nic_rates)
    solution = solve_gross_up(problem, breakpoints=[Decimal("1")])
    assert solution.method == "bisection"
    assert abs(solution.gross - Decimal("127761.509434")) < Decimal("0.00001")
    assert solution.segment_label == "IT 45% + NIC 2%"


def test_fallback_is_flagged_in_the_result(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(problem: GrossUpProblem) -> solver_module.GrossUpSolution:
        return solver_module.solve_gross_up(problem, breakpoints=[Decimal("1")])

    monkeypatch.setattr(calculator_module, "solve_gross_up", broken)
    result = calculate(reference_example(), default_provider(), rates_as_of=date(2026, 10, 8))
    assert result.year(1).gross_up.method == "bisection"
    assert result.year(1).line("TOTAL_EMPLOYER_COST") == Decimal("188676")
    flagged = [w for w in result.warnings if w.code == "GROSS_UP_FALLBACK_BISECTION"]
    assert [w.assignment_year for w in flagged] == [1, 2]
    assert flagged[0].severity == "warning"


def test_both_methods_failing_raises_solver_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def no_bisection(*args: object, **kwargs: object) -> tuple[Decimal, int]:
        raise SegmentSolveError("forced")

    def broken(problem: GrossUpProblem) -> solver_module.GrossUpSolution:
        return solver_module.solve_gross_up(problem, breakpoints=[Decimal("1")])

    monkeypatch.setattr(solver_module, "bisect_increasing", no_bisection)
    monkeypatch.setattr(calculator_module, "solve_gross_up", broken)
    with pytest.raises(SolverError) as info:
        calculate(reference_example(), default_provider(), rates_as_of=date(2026, 10, 8))
    assert info.value.code == "GROSS_UP_NOT_CONVERGED"
    assert info.value.assignment_year == 1
    assert "no figures are shown" in info.value.message


def test_piecewise_solver_on_a_simple_function() -> None:
    def f(x: Decimal) -> Decimal:
        return x if x <= 10 else 10 + (x - 10) / 2

    solution = solve_piecewise_linear(f, [Decimal("10")], Decimal("12"))
    assert solution.x == Decimal("14")
    assert solution.segment.lower == Decimal("10")
    assert solution.segment.upper is None
    assert solve_piecewise_linear(f, [Decimal("10")], Decimal("4")).x == Decimal("4")
    assert solve_piecewise_linear(f, [Decimal("10")], Decimal("0")).x == 0
    with pytest.raises(SegmentSolveError):
        solve_piecewise_linear(f, [Decimal("10")], Decimal("-1"))
    with pytest.raises(SegmentSolveError):
        solve_piecewise_linear(lambda x: Decimal("0") * x, [], Decimal("1"))


def test_bisection_and_fixed_point_on_a_simple_function() -> None:
    def f(x: Decimal) -> Decimal:
        return x / 2

    x, _ = bisect_increasing(f, Decimal("21"))
    assert abs(x - 42) < Decimal("0.000001")
    y, steps = fixed_point(f, Decimal("21"))
    assert abs(y - 42) < Decimal("0.000001")
    assert steps > 1
    with pytest.raises(SegmentSolveError):
        fixed_point(f, Decimal("21"), max_iterations=2)
    with pytest.raises(SegmentSolveError):
        bisect_increasing(lambda x: Decimal("0") * x, Decimal("1"))
