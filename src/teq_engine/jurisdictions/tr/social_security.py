"""Turkish social security (SGK) contributions on wages.

Contributions are charged on monthly gross earnings capped at the ceiling (2026: 9 x
the gross minimum wage = 297,270 a month, Law 7566):

* employee: 14% insurance + 1% unemployment = 15%;
* employer: 12% disability, old age and death + 2% short-term + 7.5% health + 2%
  unemployment = 23.5%, less the incentive points (2 points for non-manufacturing
  employers) = 21.5%.

The floor at the minimum wage is not applied (salaries in scope are far above it).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from teq_engine.money import in_engine_context
from teq_engine.ratesets.schemas import TrSgkData

__all__ = ["Contribution", "employee_contribution", "employer_contribution"]


@dataclass(frozen=True, slots=True)
class Contribution:
    """An annual contribution with the monthly base and rate it was computed on."""

    amount: Decimal
    monthly_gross: Decimal
    monthly_base: Decimal
    rate: Decimal
    ceiling_applied: bool


@in_engine_context
def _monthly_base(annual_gross: Decimal, sgk: TrSgkData) -> tuple[Decimal, Decimal, bool]:
    monthly = annual_gross / 12
    base = min(monthly, sgk.ceiling_monthly)
    return monthly, base, monthly > sgk.ceiling_monthly


@in_engine_context
def employee_contribution(annual_gross: Decimal, sgk: TrSgkData) -> Contribution:
    """Employee SGK: 15% of the capped monthly gross, times 12, unrounded.

    2026 at 5,913,000 a year: the monthly 492,750 caps at 297,270, so
    15% x 297,270 x 12 = 535,086.
    """
    monthly, base, capped = _monthly_base(annual_gross, sgk)
    rate = sgk.employee.total
    return Contribution(base * rate * 12, monthly, base, rate, capped)


@in_engine_context
def employer_contribution(
    annual_gross: Decimal, sgk: TrSgkData, incentive_points: Decimal | None = None
) -> Contribution:
    """Employer SGK: (employer rates - incentive points / 100) x capped monthly gross x 12.

    2026 at the ceiling: 21.5% x 297,270 x 12 = 766,956.60.
    """
    monthly, base, capped = _monthly_base(annual_gross, sgk)
    points = sgk.incentive_points_default if incentive_points is None else incentive_points
    rate = sgk.employer.total - points / 100
    return Contribution(base * rate * 12, monthly, base, rate, capped)
