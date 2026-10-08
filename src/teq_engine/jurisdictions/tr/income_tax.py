"""Turkish income tax on wage income (annual computation, lira).

* Wage income is taxed in progressive brackets (2026: 15% to 190,000; 20% to 400,000;
  27% to 1,500,000; 35% to 5,300,000; 40% above).
* The tax base is gross wages less the employee's social security contributions.
* Wage income up to the minimum wage is exempt. The engine applies this as a credit
  equal to the tax on the minimum-wage base: the annual gross minimum wage less the
  employee contribution rate (``minimum wage x 12 x (1 - 15%)``).

Turkish withholding is cumulative month by month; the annual computation matches it
for even monthly pay.
"""

from __future__ import annotations

from decimal import Decimal

from teq_engine.money import ZERO, in_engine_context
from teq_engine.ratesets.schemas import TrIncomeTaxData, TrSgkData

__all__ = ["minimum_wage_exemption", "minimum_wage_tax_base", "wage_income_tax"]


@in_engine_context
def wage_income_tax(tax_base: Decimal, rates: TrIncomeTaxData) -> Decimal:
    """Progressive tax on an annual wage-income base in lira, unrounded."""
    tax = ZERO
    lower = ZERO
    for bracket in rates.wage_brackets:
        upper = bracket.upto
        top = tax_base if upper is None else min(tax_base, upper)
        if top > lower:
            tax += (top - lower) * bracket.rate
        if upper is None or tax_base <= upper:
            break
        lower = upper
    return tax


@in_engine_context
def minimum_wage_tax_base(sgk: TrSgkData) -> Decimal:
    """The annual minimum-wage income-tax base: minimum wage x 12 less employee SGK."""
    return sgk.minimum_wage_gross_monthly * 12 * (1 - sgk.employee.total)


@in_engine_context
def minimum_wage_exemption(rates: TrIncomeTaxData, sgk: TrSgkData) -> Decimal:
    """The exemption credit: tax on the minimum-wage base (zero if the rule is off).

    2026: base 33,030 x 12 x 0.85 = 336,906; credit 28,500 + 20% x 146,906 = 57,881.20.
    """
    if not rates.minimum_wage_exemption:
        return ZERO
    return wage_income_tax(minimum_wage_tax_base(sgk), rates)
