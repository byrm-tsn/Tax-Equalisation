"""The Turkish hypothetical tax: what the employee would have paid at home.

``hypo = income tax + stamp tax (+ employee SGK when the hypothetical deduction includes
social security)``, computed in lira on the annual gross and converted to pounds at the
dated FX snapshot supplied with the scenario. Each lira component is rounded to the
kuruş; each pound figure to the penny; the total in pounds is the lira total converted
(not the sum of converted components).

Check case (2026 rules, salary 90,000 GBP at 65.7 TRY per GBP): gross 5,913,000; SGK
535,086.00; income tax 1,728,665.60 less the minimum-wage credit 57,881.20 =
1,670,784.40; stamp tax 41,871.30; total 2,247,741.70 TRY = 34,212.20 GBP.
"""

from __future__ import annotations

from decimal import Decimal

from teq_engine.jurisdictions.tr.income_tax import minimum_wage_exemption, wage_income_tax
from teq_engine.jurisdictions.tr.social_security import employee_contribution
from teq_engine.jurisdictions.tr.stamp_tax import stamp_tax, stamp_tax_base
from teq_engine.money import DEFAULT_ROUNDING, ZERO, in_engine_context
from teq_engine.ratesets.schemas import TrIncomeTaxData, TrSgkData, TrStampData
from teq_engine.types import (
    FxSnapshot,
    FxUsed,
    HypoTaxBase,
    HypoTaxComponents,
    HypoTaxResult,
)

__all__ = ["fx_used", "turkish_hypothetical_tax"]


def fx_used(fx: FxSnapshot) -> FxUsed:
    """The FX snapshot as pinned in a result."""
    return FxUsed(pair=fx.pair, rate=fx.rate, as_of=fx.as_of, source=fx.source)


@in_engine_context
def turkish_hypothetical_tax(
    *,
    gross_try: Decimal,
    fx: FxSnapshot,
    income_tax_rates: TrIncomeTaxData,
    sgk_rates: TrSgkData,
    stamp_rates: TrStampData,
    includes_social_security: bool,
    base: HypoTaxBase,
    tax_year: int,
) -> HypoTaxResult:
    """Compute the hypothetical Turkish tax on an annual gross in lira.

    Steps: monthly gross = annual / 12; employee SGK = 15% x min(monthly, ceiling) x 12;
    income-tax base = gross - SGK; tax by the wage brackets less the minimum-wage credit
    (never below zero); stamp tax = 0.759% x (gross - minimum wage x 12).
    """
    r = DEFAULT_ROUNDING
    gross = r.round_minor(gross_try)
    sgk = employee_contribution(gross, sgk_rates)
    sgk_try = r.round_minor(sgk.amount)
    tax_base = max(ZERO, gross - sgk_try)
    before = r.round_minor(wage_income_tax(tax_base, income_tax_rates))
    exemption = r.round_minor(minimum_wage_exemption(income_tax_rates, sgk_rates))
    income_tax_try = max(ZERO, before - exemption)
    stamp_base = r.round_minor(stamp_tax_base(gross, stamp_rates, sgk_rates))
    stamp_try = r.round_minor(stamp_tax(gross, stamp_rates, sgk_rates))
    total_try = income_tax_try + stamp_try + (sgk_try if includes_social_security else ZERO)
    rate = fx.rate
    total_gbp = r.round_minor(total_try / rate)
    components = HypoTaxComponents(
        tax_year=tax_year,
        gross_try=gross,
        monthly_gross_try=r.round_minor(sgk.monthly_gross),
        sgk_base_monthly_try=r.round_minor(sgk.monthly_base),
        sgk_ceiling_applied=sgk.ceiling_applied,
        sgk_try=sgk_try,
        income_tax_base_try=tax_base,
        income_tax_before_exemption_try=before,
        minimum_wage_exemption_try=exemption,
        income_tax_try=income_tax_try,
        stamp_tax_base_try=stamp_base,
        stamp_tax_try=stamp_try,
        total_try=total_try,
        sgk_gbp=r.round_minor(sgk_try / rate),
        income_tax_gbp=r.round_minor(income_tax_try / rate),
        stamp_tax_gbp=r.round_minor(stamp_try / rate),
        total_gbp=total_gbp,
    )
    return HypoTaxResult(
        mode="CALCULATED",
        amount=total_gbp,
        includes_social_security=includes_social_security,
        base=base,
        components=components,
        fx=fx_used(fx),
    )
