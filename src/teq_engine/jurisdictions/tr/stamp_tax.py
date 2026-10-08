"""Turkish stamp tax on wages: 0.759% of gross wages, with the minimum-wage portion exempt."""

from __future__ import annotations

from decimal import Decimal

from teq_engine.money import ZERO, in_engine_context
from teq_engine.ratesets.schemas import TrSgkData, TrStampData

__all__ = ["stamp_tax", "stamp_tax_base"]


@in_engine_context
def stamp_tax_base(annual_gross: Decimal, stamp: TrStampData, sgk: TrSgkData) -> Decimal:
    """Gross wages less the annual gross minimum wage when the exemption applies."""
    if not stamp.minimum_wage_exempt:
        return annual_gross
    return max(ZERO, annual_gross - sgk.minimum_wage_gross_monthly * 12)


@in_engine_context
def stamp_tax(annual_gross: Decimal, stamp: TrStampData, sgk: TrSgkData) -> Decimal:
    """Stamp tax, unrounded. 2026 at 5,913,000: 0.759% x 5,516,640 = 41,871.2976."""
    return stamp_tax_base(annual_gross, stamp, sgk) * stamp.rate
