"""The UK gross-up: find gross cash ``G`` so that net cash equals the guarantee ``N``.

    G - IncomeTax(G + BIK) - EmployeeNICs(G) = N

``BIK`` is the cash equivalent of the year's taxable benefits; it enters the income-tax
base but not employee NICs. ``net(G)`` is continuous, piecewise linear and strictly
increasing, because every marginal rate is below 100%, so the solution is unique. The
worst segment is the personal-allowance taper band with 8% NICs: taxable benefits large
enough to push total income into the taper band while cash pay is still below the upper
earnings limit. There the marginal rate is 60% income tax (40% x 1.5) plus 8% employee
NICs = 68%, so the slope of ``net`` is 0.32; on every other segment it is steeper (for
example 0.38 in the taper band once cash is above the limit and NICs are 2%, 0.53 at the
45% rate). Rate-set validation refuses any effective income-tax rate at or above 100%,
which keeps every slope positive.

**Primary method: exact segment solve.** Breakpoints in gross-cash space are the
income-tax breakpoints in total-income space (allowance threshold, band edges mapped
through the allowance, taper start and end) shifted left by ``BIK``, plus the NIC
primary threshold and upper earnings limit. ``net`` is evaluated at each, the segment
containing ``N`` is found and solved linearly. Post-condition: ``|net(G) - N| <= 0.0001``.

**Fallback: bisection**, flagged ``GROSS_UP_FALLBACK_BISECTION`` (it would hide a
breakpoint bug, so it must be alerted on). If both fail, :class:`SolverError`
(``GROSS_UP_NOT_CONVERGED``) and no figures are produced.

**Oracle:** the fixed-point iteration ``G <- G + (N - net(G))`` that the reference pack
describes; tests require it to agree with the segment solve to the penny.

HMRC: grossing up a net-of-tax payment through PAYE (pack references PAYE81740,
PAYE72025; tax equalisation helpsheet HS212).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Final, Literal

from teq_engine.errors import SolverError
from teq_engine.jurisdictions.gb.income_tax import income_tax, total_income_breakpoints
from teq_engine.jurisdictions.gb.nic import employee_breakpoints, employee_nic
from teq_engine.money import MICRO, ZERO, engine_context, in_engine_context
from teq_engine.piecewise import (
    SegmentSolveError,
    bisect_increasing,
    fixed_point,
    solve_piecewise_linear,
)
from teq_engine.ratesets.schemas import UkIncomeTaxData, UkNicData
from teq_engine.trace import fmt_percent, fmt_rate

__all__ = [
    "POST_CONDITION_TOLERANCE",
    "GrossUpProblem",
    "GrossUpSolution",
    "SolverError",
    "bisection_gross_up",
    "fixed_point_gross_up",
    "gross_breakpoints",
    "solve_gross_up",
]

POST_CONDITION_TOLERANCE: Final = Decimal("0.0001")


@dataclass(frozen=True, slots=True)
class GrossUpProblem:
    """One year's gross-up: the net target, the benefits and the rules."""

    net_target: Decimal
    benefits: Decimal
    income_tax_rates: UkIncomeTaxData
    nic_rates: UkNicData
    nic_applies: bool = True

    @in_engine_context
    def income_tax_on(self, gross: Decimal) -> Decimal:
        """Income tax on gross cash plus benefits."""
        return income_tax(gross + self.benefits, self.income_tax_rates)

    @in_engine_context
    def employee_nic_on(self, gross: Decimal) -> Decimal:
        """Employee NICs on gross cash (zero under the home scheme)."""
        return employee_nic(gross, self.nic_rates) if self.nic_applies else ZERO

    @in_engine_context
    def net(self, gross: Decimal) -> Decimal:
        """Net cash: ``G - IncomeTax(G + BIK) - EmployeeNICs(G)``."""
        return gross - self.income_tax_on(gross) - self.employee_nic_on(gross)


@dataclass(frozen=True, slots=True)
class GrossUpSolution:
    """The exact gross and how it was found (full precision; rounding happens later)."""

    gross: Decimal
    method: Literal["segment", "bisection"]
    evaluations: int
    segment_from: Decimal
    segment_to: Decimal | None
    income_tax_rate: Decimal
    nic_rate: Decimal
    segment_label: str

    @property
    def marginal_rate(self) -> Decimal:
        """Income tax plus NIC marginal rate on the final segment (0.47 in the pack)."""
        with engine_context():
            return self.income_tax_rate + self.nic_rate


@in_engine_context
def gross_breakpoints(problem: GrossUpProblem) -> tuple[Decimal, ...]:
    """Positive gross-cash breakpoints of ``net(G)``, sorted.

    Total-income breakpoints shifted by ``-BIK`` (12,570 - BIK, 50,270 - BIK,
    100,000 - BIK, 125,140 - BIK for 2026-27) plus the NIC thresholds 12,570 and 50,270
    in cash space.
    """
    points: set[Decimal] = {
        point - problem.benefits for point in total_income_breakpoints(problem.income_tax_rates)
    }
    if problem.nic_applies:
        points.update(employee_breakpoints(problem.nic_rates))
    return tuple(sorted(p for p in points if p > 0))


@in_engine_context
def _segment_rates(
    problem: GrossUpProblem, lower: Decimal, upper: Decimal | None
) -> tuple[Decimal, Decimal, str]:
    """Income-tax and NIC slopes over ``[lower, upper]`` and a label for the trace."""
    top = upper if upper is not None else lower + 1
    width = top - lower
    it_rate = (problem.income_tax_on(top) - problem.income_tax_on(lower)) / width
    nic_rate = (problem.employee_nic_on(top) - problem.employee_nic_on(lower)) / width
    it_rate = Decimal(fmt_rate(it_rate.quantize(MICRO, rounding=ROUND_HALF_UP)))
    nic_rate = Decimal(fmt_rate(nic_rate.quantize(MICRO, rounding=ROUND_HALF_UP)))
    rates = problem.income_tax_rates
    middle_income = (lower + top) / 2 + problem.benefits
    taper = " (allowance taper)" if rates.taper_start < middle_income < rates.taper_end else ""
    if problem.nic_applies:
        label = f"IT {fmt_percent(it_rate)}{taper} + NIC {fmt_percent(nic_rate)}"
    else:
        label = f"IT {fmt_percent(it_rate)}{taper}, no UK NIC"
    return it_rate, nic_rate, label


def _verified(problem: GrossUpProblem, gross: Decimal) -> bool:
    return abs(problem.net(gross) - problem.net_target) <= POST_CONDITION_TOLERANCE


@in_engine_context
def solve_gross_up(
    problem: GrossUpProblem, *, breakpoints: Iterable[Decimal] | None = None
) -> GrossUpSolution:
    """Solve the gross-up exactly, falling back to bisection, else raise SolverError.

    ``breakpoints`` overrides :func:`gross_breakpoints` (used by tests to exercise the
    fallback). The returned gross is unrounded; the rounding policy ceils it to the pound.
    """
    target = problem.net_target
    points = gross_breakpoints(problem) if breakpoints is None else tuple(breakpoints)
    try:
        solution = solve_piecewise_linear(problem.net, points, target)
    except SegmentSolveError:
        solution = None
    if solution is not None and solution.x >= 0 and _verified(problem, solution.x):
        segment = solution.segment
        it_rate, nic_rate, label = _segment_rates(problem, segment.lower, segment.upper)
        return GrossUpSolution(
            gross=solution.x,
            method="segment",
            evaluations=solution.evaluations,
            segment_from=segment.lower,
            segment_to=segment.upper,
            income_tax_rate=it_rate,
            nic_rate=nic_rate,
            segment_label=label,
        )
    return _fallback(problem)


def _fallback(problem: GrossUpProblem) -> GrossUpSolution:
    try:
        gross, evaluations = bisect_increasing(problem.net, problem.net_target)
    except SegmentSolveError as exc:
        raise SolverError(
            f"gross-up not solved: {exc}",
            net_target=str(problem.net_target),
            taxable_benefits=str(problem.benefits),
        ) from exc
    if not _verified(problem, gross):
        raise SolverError(
            "gross-up not solved: bisection failed its post-condition",
            net_target=str(problem.net_target),
            taxable_benefits=str(problem.benefits),
        )
    # Locate the segment from the true breakpoints, for the label only.
    lower = max((p for p in gross_breakpoints(problem) if p <= gross), default=ZERO)
    upper = min((p for p in gross_breakpoints(problem) if p > gross), default=None)
    it_rate, nic_rate, label = _segment_rates(problem, lower, upper)
    return GrossUpSolution(
        gross=gross,
        method="bisection",
        evaluations=evaluations,
        segment_from=lower,
        segment_to=upper,
        income_tax_rate=it_rate,
        nic_rate=nic_rate,
        segment_label=label,
    )


@in_engine_context
def fixed_point_gross_up(problem: GrossUpProblem) -> tuple[Decimal, int]:
    """The test oracle: ``G <- G + (N - net(G))`` from ``G = N``; returns (G, iterations)."""
    return fixed_point(problem.net, problem.net_target)


@in_engine_context
def bisection_gross_up(problem: GrossUpProblem) -> tuple[Decimal, int]:
    """Bisection on ``net(G) = N``; returns (G, evaluations)."""
    return bisect_increasing(problem.net, problem.net_target)
