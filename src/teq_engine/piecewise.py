"""Root finding for continuous, increasing, piecewise-linear functions.

The gross-up's net-pay function ``net(G)`` is continuous, piecewise linear and strictly
increasing (every marginal rate is below 100%), so ``net(G) = N`` has exactly one
solution. Given the breakpoints where the slope can change, the solution is found by
evaluating ``net`` at each breakpoint, locating the segment that contains ``N`` and
solving the linear equation on it: no iteration, deterministic, exact to the precision
of the decimal context.

Two independent methods are kept beside it: bracketed bisection (the fallback) and the
fixed-point iteration ``G <- G + (N - net(G))`` (the test oracle, and the iteration the
reference pack describes).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from decimal import Decimal

from teq_engine.money import ZERO, in_engine_context

__all__ = [
    "Segment",
    "SegmentSolution",
    "SegmentSolveError",
    "bisect_increasing",
    "fixed_point",
    "solve_piecewise_linear",
]

type Fn = Callable[[Decimal], Decimal]


class SegmentSolveError(ArithmeticError):
    """The segment solve could not place the target (bad breakpoints or domain)."""


@dataclass(frozen=True, slots=True)
class Segment:
    """A linear piece ``[lower, upper]`` (``upper`` is ``None`` for the last piece)."""

    lower: Decimal
    upper: Decimal | None
    value_at_lower: Decimal
    slope: Decimal


@dataclass(frozen=True, slots=True)
class SegmentSolution:
    """The root, the segment it lies on and how many evaluations it took."""

    x: Decimal
    segment: Segment
    evaluations: int


@in_engine_context
def solve_piecewise_linear(
    f: Fn, breakpoints: Iterable[Decimal], target: Decimal, *, probe: Decimal = Decimal("1")
) -> SegmentSolution:
    """Solve ``f(x) = target`` for ``x >= 0``.

    ``f`` must be continuous, increasing and linear between consecutive points of
    ``{0} | breakpoints``. Beyond the last breakpoint the slope is measured over
    ``probe``. Raises :class:`SegmentSolveError` when ``target < f(0)`` or a segment is
    flat; the caller verifies the answer and falls back if the breakpoints were wrong.
    """
    points = sorted({ZERO, *(b for b in breakpoints if b > 0)})
    x0 = points[0]
    v0 = f(x0)
    evaluations = 1
    if target < v0:
        raise SegmentSolveError("target is below f(0)")
    if target == v0:
        return SegmentSolution(x0, Segment(x0, points[1] if len(points) > 1 else None, v0, ZERO), 1)
    for x1 in points[1:]:
        v1 = f(x1)
        evaluations += 1
        if v1 >= target:
            slope = (v1 - v0) / (x1 - x0)
            if slope <= 0:
                raise SegmentSolveError("flat or decreasing segment")
            x = x0 + (target - v0) / slope
            return SegmentSolution(x, Segment(x0, x1, v0, slope), evaluations)
        x0, v0 = x1, v1
    v_probe = f(x0 + probe)
    evaluations += 1
    slope = (v_probe - v0) / probe
    if slope <= 0:
        raise SegmentSolveError("flat or decreasing final segment")
    return SegmentSolution(x0 + (target - v0) / slope, Segment(x0, None, v0, slope), evaluations)


@in_engine_context
def bisect_increasing(
    f: Fn,
    target: Decimal,
    *,
    lower: Decimal = ZERO,
    max_iterations: int = 200,
    tolerance: Decimal = Decimal("0.000000001"),
) -> tuple[Decimal, int]:
    """Bracketed bisection for an increasing ``f``; returns ``(x, evaluations)``.

    The upper bracket starts at ``max(target, 1) * 2`` above ``lower`` and doubles until
    ``f(upper) >= target``. Returns the upper end of the final bracket, so
    ``f(x) >= target`` and ``x`` is within ``tolerance`` of the root.
    """
    evaluations = 0
    if f(lower) >= target:
        return lower, 1
    upper = lower + max(target, Decimal("1")) * 2
    for _ in range(128):
        evaluations += 1
        if f(upper) >= target:
            break
        upper = lower + (upper - lower) * 2
    else:
        raise SegmentSolveError("could not bracket the root")
    for _ in range(max_iterations):
        if upper - lower <= tolerance:
            break
        middle = (lower + upper) / 2
        evaluations += 1
        if f(middle) >= target:
            upper = middle
        else:
            lower = middle
    else:
        if upper - lower > tolerance:
            raise SegmentSolveError("bisection did not converge")
    return upper, evaluations


@in_engine_context
def fixed_point(
    f: Fn,
    target: Decimal,
    *,
    start: Decimal | None = None,
    max_iterations: int = 1000,
    tolerance: Decimal = Decimal("0.0000001"),
) -> tuple[Decimal, int]:
    """The pack's iteration: ``x <- x + (target - f(x))`` from ``start`` (default target).

    Converges when ``f`` has slope in (0, 2); for the net-pay function the slope lies
    between 0.38 and 1, so each step removes at least 38% of the error. Returns
    ``(x, iterations)`` once the step is below ``tolerance``.
    """
    x = target if start is None else start
    for iteration in range(1, max_iterations + 1):
        step = target - f(x)
        x += step
        if abs(step) <= tolerance:
            return x, iteration
    raise SegmentSolveError("fixed-point iteration did not converge")
