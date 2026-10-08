"""UK National Insurance contributions, annual basis (``ANNUAL_NIC_BASIS``).

* Class 1 employee (primary): ``main_rate`` (8%) on cash earnings between the primary
  threshold (12,570) and the upper earnings limit (50,270); ``upper_rate`` (2%) above.
  Charged on cash pay only, never on benefits in kind.
* Class 1 employer (secondary): ``rate`` (15%) on cash earnings above the secondary
  threshold (5,000). The Employment Allowance is not applied.
* Class 1A: ``rate`` (15%) on the cash equivalent of taxable benefits in kind, paid by
  the employer only.

In reality Class 1 is computed per pay period; for even pay the annual figure is the
same up to rounding.
"""

from __future__ import annotations

from decimal import Decimal

from teq_engine.money import ZERO, in_engine_context
from teq_engine.ratesets.schemas import UkNicData

__all__ = ["class_1a", "employee_breakpoints", "employee_nic", "employer_nic"]


@in_engine_context
def employee_nic(gross_cash: Decimal, rates: UkNicData) -> Decimal:
    """Class 1 employee NICs on annual cash earnings, unrounded.

    Example (2026-27): 127,762 gives 8% x 37,700 + 2% x 77,492 = 3,016 + 1,549.84.
    """
    ee = rates.employee
    main_band = max(ZERO, min(gross_cash, ee.upper_earnings_limit) - ee.primary_threshold)
    upper_band = max(ZERO, gross_cash - ee.upper_earnings_limit)
    return main_band * ee.main_rate + upper_band * ee.upper_rate


@in_engine_context
def employer_nic(gross_cash: Decimal, rates: UkNicData) -> Decimal:
    """Class 1 employer NICs on annual cash earnings above the secondary threshold."""
    er = rates.employer
    return max(ZERO, gross_cash - er.secondary_threshold) * er.rate


@in_engine_context
def class_1a(benefits: Decimal, rates: UkNicData) -> Decimal:
    """Class 1A NICs on the cash equivalent of taxable benefits in kind."""
    return max(ZERO, benefits) * rates.class_1a.rate


def employee_breakpoints(rates: UkNicData) -> tuple[Decimal, ...]:
    """Cash earnings at which the employee NIC rate changes (gross-cash space)."""
    return (rates.employee.primary_threshold, rates.employee.upper_earnings_limit)
