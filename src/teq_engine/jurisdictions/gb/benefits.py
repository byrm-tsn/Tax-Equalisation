"""UK benefit rules: cash equivalents and the relocation exemption.

Relocation (HMRC "Expenses and benefits: relocation"): qualifying removal expenses and
benefits are exempt up to a cap of 8,000 **per move**. The cap is therefore consumed
cumulatively across every payment for the move, in assignment-year order and, within a
year, in input order; it is not reset each year. Any excess is a taxable benefit in kind
(income tax inside the gross-up, Class 1A for the employer).

The exemption only applies to items provided by the end of the tax year following the
tax year in which the job starts. In whole-year mode, assignment years 1 and 2 are
inside that window; a payment in year 3 or later is treated as fully taxable and flagged
``RELOCATION_OUTSIDE_WINDOW``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from teq_engine.money import ZERO, in_engine_context
from teq_engine.ratesets.schemas import RelocationRules

__all__ = [
    "RelocationAllocation",
    "RelocationPayment",
    "allocate_relocation",
    "benefit_cash_equivalent",
]


@in_engine_context
def benefit_cash_equivalent(amount: Decimal, employee_contribution: Decimal) -> Decimal:
    """The taxable cash equivalent: the amount less any employee contribution, at least 0.

    The cash equivalent is taken as an input (``BIK_CASH_EQUIVALENT_AS_INPUT``); no
    statutory valuation of living accommodation is performed.
    """
    return max(ZERO, amount - employee_contribution)


@dataclass(frozen=True, slots=True)
class RelocationPayment:
    """One relocation payment in one assignment year."""

    assignment_year: int
    item_id: str
    amount: Decimal


@dataclass(frozen=True, slots=True)
class RelocationAllocation:
    """How one relocation payment splits between the exemption and a taxable benefit."""

    assignment_year: int
    item_id: str
    amount: Decimal
    exempt: Decimal
    taxable: Decimal
    remaining_before: Decimal
    outside_window: bool


@in_engine_context
def allocate_relocation(
    payments: Sequence[RelocationPayment], rules: RelocationRules
) -> tuple[RelocationAllocation, ...]:
    """Apply the per-move cap cumulatively across ``payments``.

    Payments are taken in assignment-year order (stable within a year). A payment inside
    the window uses whatever cap remains; the rest of it is taxable. A payment outside
    the window is wholly taxable and does not use the cap.
    """
    remaining = rules.exemption_cap
    result: list[RelocationAllocation] = []
    for payment in sorted(payments, key=lambda p: p.assignment_year):
        outside = payment.assignment_year > rules.window_tax_years
        exempt = ZERO if outside else min(payment.amount, remaining)
        before = remaining
        remaining -= exempt
        result.append(
            RelocationAllocation(
                assignment_year=payment.assignment_year,
                item_id=payment.item_id,
                amount=payment.amount,
                exempt=exempt,
                taxable=payment.amount - exempt,
                remaining_before=before,
                outside_window=outside,
            )
        )
    return tuple(result)
