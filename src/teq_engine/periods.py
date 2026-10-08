"""The period plan: how assignment years map onto UK tax years and Turkish calendar years.

A :class:`PeriodPlan` is a list of :class:`Period` objects, each made of one or more
:class:`FiscalSlice` objects. The calculator computes per fiscal slice and allocates to
assignment years; tax functions never see dates.

v1 implements the **illustrative whole-year mode** only (``MODE_ILLUSTRATIVE_WHOLE_YEAR``):
one slice per assignment year with fraction 1. Assignment year 1 is the UK tax year
(6 April to 5 April) and the Turkish calendar year containing the anchor date (the
assignment start date if given, else ``rates_as_of``); each later year increments both.
Calendar-accurate mode (splitting at 6 April and 1 January) can add slices with
fractions below 1 without changing this interface.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from teq_engine.types import MAX_YEARS, PeriodMode

__all__ = [
    "FiscalSlice",
    "Period",
    "PeriodPlan",
    "build_period_plan",
    "uk_tax_year_label",
    "uk_tax_year_start_year",
]


def uk_tax_year_start_year(on: date) -> int:
    """The calendar year in which the UK tax year containing ``on`` starts (6 April)."""
    return on.year if (on.month, on.day) >= (4, 6) else on.year - 1


def uk_tax_year_label(start_year: int) -> str:
    """``2026`` -> ``"2026-27"``."""
    return f"{start_year}-{(start_year + 1) % 100:02d}"


@dataclass(frozen=True, slots=True)
class FiscalSlice:
    """The part of an assignment year falling in one UK tax year and one TR year."""

    uk_tax_year: str
    uk_tax_year_start: date
    tr_calendar_year: int
    tr_year_start: date
    fraction: Decimal


@dataclass(frozen=True, slots=True)
class Period:
    """One assignment year and its fiscal slices."""

    assignment_year: int
    slices: tuple[FiscalSlice, ...]

    @property
    def primary(self) -> FiscalSlice:
        """The slice that carries the year's label (the only one in whole-year mode)."""
        return self.slices[0]


@dataclass(frozen=True, slots=True)
class PeriodPlan:
    """The ordered periods of an assignment."""

    mode: PeriodMode
    anchor: date
    periods: tuple[Period, ...]


def build_period_plan(
    length_years: int,
    anchor: date,
    mode: PeriodMode = PeriodMode.ILLUSTRATIVE_WHOLE_YEAR,
) -> PeriodPlan:
    """Build the plan for ``length_years`` whole years anchored on ``anchor``."""
    if mode is not PeriodMode.ILLUSTRATIVE_WHOLE_YEAR:  # pragma: no cover - single mode in v1
        raise ValueError(f"period mode {mode} is not supported")
    if not 1 <= length_years <= MAX_YEARS:
        raise ValueError(f"assignment length must be 1 to {MAX_YEARS} years")
    uk_start = uk_tax_year_start_year(anchor)
    tr_year = anchor.year
    periods = tuple(
        Period(
            assignment_year=offset + 1,
            slices=(
                FiscalSlice(
                    uk_tax_year=uk_tax_year_label(uk_start + offset),
                    uk_tax_year_start=date(uk_start + offset, 4, 6),
                    tr_calendar_year=tr_year + offset,
                    tr_year_start=date(tr_year + offset, 1, 1),
                    fraction=Decimal("1"),
                ),
            ),
        )
        for offset in range(length_years)
    )
    return PeriodPlan(mode=mode, anchor=anchor, periods=periods)
