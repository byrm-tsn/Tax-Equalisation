"""UK income tax on employment income (England rates), annual basis.

Rules (all figures come from the ``UK_INCOME_TAX`` rate set, never from code):

* Total income is the grossed-up cash pay plus the cash equivalent of taxable benefits.
* The personal allowance (12,570 for 2026-27) is reduced by 1 for every 2 of total
  income above the taper start (100,000), reaching nil at 125,140. The reduction is
  applied continuously (half of the excess, not rounded to whole pounds) so that the
  net-pay function stays continuous for the gross-up solver.
* Taxable income is total income less the allowance. Bands apply to taxable income:
  20% on the first 37,700, 40% up to 125,140, 45% above.

HMRC: the reference pack cites helpsheet HS212 (tax equalisation) and PAYE Manual
PAYE81740 and PAYE72025 for grossing up net-of-tax pay through PAYE.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from teq_engine.money import ZERO, in_engine_context
from teq_engine.ratesets.schemas import UkIncomeTaxData

__all__ = [
    "BandSlice",
    "band_slices",
    "income_tax",
    "personal_allowance",
    "taxable_income",
    "total_income_breakpoints",
]


@in_engine_context
def personal_allowance(total_income: Decimal, rates: UkIncomeTaxData) -> Decimal:
    """The personal allowance for ``total_income``.

    Full allowance up to the taper start; then reduced by ``taper_rate`` (1 for every
    2) of the excess; nil from the taper end (125,140 for 2026-27). Assumes the
    employee is entitled to the allowance (UK resident; ``PERSONAL_ALLOWANCE_ENTITLED``).
    """
    excess = max(ZERO, total_income - rates.taper_start)
    return max(ZERO, rates.personal_allowance - excess * rates.taper_rate)


@in_engine_context
def taxable_income(total_income: Decimal, rates: UkIncomeTaxData) -> Decimal:
    """Total income less the personal allowance, never negative."""
    return max(ZERO, total_income - personal_allowance(total_income, rates))


@dataclass(frozen=True, slots=True)
class BandSlice:
    """The part of taxable income falling in one band, and the tax on it."""

    rate: Decimal
    lower: Decimal
    upper: Decimal | None
    amount: Decimal
    tax: Decimal


@in_engine_context
def band_slices(total_income: Decimal, rates: UkIncomeTaxData) -> tuple[BandSlice, ...]:
    """Split taxable income across the bands (20%, 40%, 45% for 2026-27)."""
    taxable = taxable_income(total_income, rates)
    slices: list[BandSlice] = []
    lower = ZERO
    for band in rates.bands:
        upper = band.upto
        top = taxable if upper is None else min(taxable, upper)
        amount = max(ZERO, top - lower)
        slices.append(BandSlice(band.rate, lower, upper, amount, amount * band.rate))
        if upper is None or taxable <= upper:
            break
        lower = upper
    return tuple(slices)


@in_engine_context
def income_tax(total_income: Decimal, rates: UkIncomeTaxData) -> Decimal:
    """Income tax on ``total_income`` (cash plus benefits), unrounded.

    Example (2026-27): total income 157,762 gives no allowance and tax of
    7,540 + 34,976 + 14,679.90 = 57,195.90.
    """
    return sum((s.tax for s in band_slices(total_income, rates)), ZERO)


@in_engine_context
def total_income_breakpoints(rates: UkIncomeTaxData) -> tuple[Decimal, ...]:
    """Total incomes at which the marginal income-tax rate can change.

    These are the allowance threshold, the taper start and end, and every band edge
    mapped back from taxable-income space through the allowance in force there:

    * full allowance: ``T = edge + allowance`` (valid up to the taper start);
    * inside the taper: ``T = (edge + allowance + taper_rate * taper_start) /
      (1 + taper_rate)``;
    * nil allowance: ``T = edge`` (valid from the taper end).

    The gross-up shifts these left by the benefit value to get gross-cash breakpoints.
    """
    allowance = rates.personal_allowance
    start = rates.taper_start
    end = rates.taper_end
    rate = rates.taper_rate
    points: set[Decimal] = {allowance, start, end}
    for band in rates.bands:
        if band.upto is None:
            continue
        edge = band.upto
        full = edge + allowance
        if full <= start:
            points.add(full)
        tapered = (edge + allowance + rate * start) / (1 + rate)
        if start < tapered < end:
            points.add(tapered)
        if edge >= end:
            points.add(edge)
    return tuple(sorted(p for p in points if p > 0))
