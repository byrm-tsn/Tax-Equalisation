"""Immigration costs as formulas over the tailoring answers.

Each cost item has a ``basis`` (fixed, per visa year, first year then per six
months, per dependant, per dependant per year), optional tiers keyed on the
answers (visa length, where the application is made, sponsor size), a payer and
a flag saying whether it may be recovered from the worker. The amounts computed
here are shown in the immigration panel only: they are never added to any
employment-cost figure.

Visa length is priced in months (``visa_length_months``, or ``12 x
visa_length_years``):

* **Immigration Skills Charge** (``FIRST_YEAR_THEN_PER_6_MONTHS``): the first-12-month
  amount, plus one per-period amount for each further six months or part of six
  months, ``ceil((months - 12) / 6)``. 30 months for a medium or large sponsor:
  1,320 + 660 x 3 = 3,300.
* **Health surcharge** (``PER_VISA_YEAR`` and the per-dependant-year bases): the annual
  amount for each whole year, and for a final part year half the annual amount when it
  is 6 months or less, the full amount when it is longer. 30 months: 2.5 x the annual
  amount.
* **A single grant** lasts at most :data:`MAX_SINGLE_GRANT_MONTHS` (60) months. A longer
  request is priced as one 60-month grant; the panel warns that a further application
  would be needed for the rest.

All arithmetic runs inside :func:`~teq_guidance.formatting.decimal_context`.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Final

from teq_guidance.formatting import format_money, format_range, in_decimal_context, plural
from teq_guidance.model import (
    Basis,
    CostItem,
    CostLine,
    CostSubtotals,
    CostTier,
    FundsRequirement,
    GuidancePack,
    Payer,
)
from teq_guidance.tailoring import (
    MONTHS_PER_YEAR,
    ResolvedAnswers,
    TailoringAnswers,
    applicable,
    evaluate,
    resolve_answers,
)

__all__ = [
    "MAX_SINGLE_GRANT_MONTHS",
    "compute_costs",
    "cost_subtotals",
    "grant_cap_note",
    "maintenance_funds",
    "priced_visa_months",
]

_ZERO = Decimal("0.00")
_HALF = Decimal("0.5")
_PERIOD_MONTHS = 6
_HALF_YEAR_MONTHS = 6

MAX_SINGLE_GRANT_MONTHS: Final = 60
"""The longest single Skilled Worker grant (5 years); a longer stay needs a further
application, priced separately."""

_TIME_BASED: Final = frozenset(
    {
        Basis.PER_VISA_YEAR,
        Basis.FIRST_YEAR_THEN_PER_6_MONTHS,
        Basis.PER_DEPENDANT_VISA_YEAR_ADULT,
        Basis.PER_DEPENDANT_VISA_YEAR_CHILD,
    }
)
_PER_YEAR: Final = _TIME_BASED - {Basis.FIRST_YEAR_THEN_PER_6_MONTHS}

HALF_YEAR_NOTE: Final = (
    "The final part year of 6 months or less is charged at half the annual amount."
)


def priced_visa_months(requested_months: int) -> int:
    """The months one grant covers: the request, capped at a single 60-month grant."""
    return min(requested_months, MAX_SINGLE_GRANT_MONTHS)


def grant_cap_note(requested_months: int) -> str:
    """The note shown when the request is longer than a single grant, else ``""``."""
    if requested_months <= MAX_SINGLE_GRANT_MONTHS:
        return ""
    remaining = requested_months - MAX_SINGLE_GRANT_MONTHS
    return (
        f"Priced for a single grant of {MAX_SINGLE_GRANT_MONTHS} months; the remaining "
        f"{plural(remaining, 'month')} would need a further application, charged separately."
    )


@in_decimal_context
def compute_costs(pack: GuidancePack, answers: TailoringAnswers | None = None) -> list[CostLine]:
    """Compute every applicable cost item for the answers, in pack order.

    Optional services are included with ``optional`` set; ranges, non-GBP amounts
    and amounts the pack does not hold have ``in_subtotal`` false. Use
    ``cost_subtotals`` for the payer subtotals.
    """
    selected = applicable(pack, answers)
    return [_cost_line(item, selected.answers) for item in selected.costs]


@in_decimal_context
def cost_subtotals(lines: list[CostLine]) -> CostSubtotals:
    """Subtotals in GBP by payer over the lines included in subtotals."""

    def total(payer: Payer) -> Decimal:
        amounts = [line.amount for line in lines if line.in_subtotal and line.payer is payer]
        return sum((amount for amount in amounts if amount is not None), _ZERO)

    employer = total(Payer.EMPLOYER)
    by_policy = total(Payer.EITHER_BY_POLICY)
    applicant = total(Payer.APPLICANT)
    return CostSubtotals(
        employer_mandatory=employer,
        employer_by_policy=by_policy,
        applicant_only=applicant,
        applicant_side=by_policy + applicant,
        employer_including_policy=employer + by_policy,
    )


@in_decimal_context
def maintenance_funds(
    pack: GuidancePack, answers: TailoringAnswers | None = None
) -> FundsRequirement:
    """The funds the applicant must show, including any dependants' amounts."""
    resolved = resolve_answers(pack, answers)
    funds = pack.maintenance_funds
    adults = resolved.count("dependants_adults")
    children = resolved.count("dependants_children")
    amount = funds.main_applicant
    parts = [f"{format_money(funds.main_applicant)} main applicant"]
    if adults:
        amount += funds.partner * adults
        parts.append(f"{format_money(funds.partner)} partner")
    if children:
        amount += funds.first_child
        parts.append(f"{format_money(funds.first_child)} first child")
    if children > 1:
        further = children - 1
        amount += funds.each_further_child * further
        further_text = plural(further, "further child", "further children")
        parts.append(f"{format_money(funds.each_further_child)} x {further_text}")
    return FundsRequirement(
        applies=evaluate(funds.condition, resolved.values),
        amount=amount,
        formula=" + ".join(parts),
        days_held=funds.days_held,
    )


# --------------------------------------------------------------------------- lines


def _matching_tier(item: CostItem, answers: ResolvedAnswers) -> CostTier | None:
    for tier in item.tiers:
        if evaluate(tier.condition, answers.values):
            return tier
    return None


def _cost_line(item: CostItem, answers: ResolvedAnswers) -> CostLine:
    unit = item.amount
    per_period = item.amount_per_period
    tier_label = ""
    missing_note = ""
    if item.tiers:
        tier = _matching_tier(item, answers)
        if tier is None:
            unit, per_period = None, None
            missing_note = "No fee in this pack matches these answers; check the source."
        else:
            unit, per_period, tier_label = tier.amount, tier.amount_per_period, tier.label
            if tier.amount is None:
                missing_note = tier.note

    requested = answers.months()
    months = priced_visa_months(requested)
    visa_years, years_text, half_year = _chargeable_years(months)
    adults = answers.count("dependants_adults")
    children = answers.count("dependants_children")
    multiplier, multiplier_text = _multiplier(item.basis, visa_years, years_text, adults, children)

    amount: Decimal | None = None
    amount_min: Decimal | None = None
    amount_max: Decimal | None = None
    currency = item.currency
    if item.amount_min is not None and item.amount_max is not None:
        amount_min = item.amount_min * multiplier
        amount_max = item.amount_max * multiplier
        formula = f"{format_range(item.amount_min, item.amount_max, currency)} (typical range)"
        if multiplier_text:
            formula += f" x {multiplier_text}"
    elif unit is None:
        formula = "Amount not held in this pack"
    elif item.basis is Basis.FIRST_YEAR_THEN_PER_6_MONTHS:
        amount, formula = _first_year_then_periods(unit, per_period or _ZERO, months, currency)
    else:
        amount = unit * multiplier
        if item.basis is Basis.FIXED:
            formula = f"{format_money(unit, currency)} fixed fee"
        else:
            formula = f"{format_money(unit, currency)} x {multiplier_text}"
    if tier_label:
        formula += f" ({tier_label})"

    timing_notes: list[str] = []
    if item.basis in _PER_YEAR and half_year:
        timing_notes.append(HALF_YEAR_NOTE)
    if item.basis in _TIME_BASED:
        timing_notes.append(grant_cap_note(requested))
    notes = " ".join(part for part in (missing_note, item.notes, *timing_notes) if part)
    in_subtotal = amount is not None and currency == "GBP" and not item.optional
    return CostLine(
        id=item.id,
        label=item.label,
        description=item.description,
        basis=item.basis,
        payer=item.payer,
        cannot_be_recouped_from_worker=item.cannot_be_recouped_from_worker,
        currency=currency,
        amount=amount,
        amount_min=amount_min,
        amount_max=amount_max,
        formula=formula,
        optional=item.optional,
        in_subtotal=in_subtotal,
        verification=item.verification,
        source=item.source,
        verified_at=item.verified_at,
        effective_from=item.effective_from,
        notes=notes,
    )


def _chargeable_years(months: int) -> tuple[Decimal, str, bool]:
    """Years charged for a per-year fee: whole years, plus half a year for a final part
    year of 6 months or less, or a whole year for a longer one.

    Returns the multiplier, its words (``2 years``, ``2.5 years``) and whether a half
    year was charged.
    """
    whole, part = divmod(months, MONTHS_PER_YEAR)
    if part == 0:
        return Decimal(whole), plural(whole, "year"), False
    if part <= _HALF_YEAR_MONTHS:
        return Decimal(whole) + _HALF, f"{whole}.5 years", True
    return Decimal(whole + 1), plural(whole + 1, "year"), False


def _multiplier(
    basis: Basis, visa_years: Decimal, years_text: str, adults: int, children: int
) -> tuple[Decimal, str]:
    """How many units of the amount are due, and the words for the formula."""
    partner_text = plural(adults, "partner")
    child_text = plural(children, "child", "children")
    match basis:
        case Basis.FIXED | Basis.FIRST_YEAR_THEN_PER_6_MONTHS:
            return Decimal(1), ""
        case Basis.PER_VISA_YEAR:
            return visa_years, years_text
        case Basis.PER_ADULT_DEPENDANT:
            return Decimal(adults), partner_text
        case Basis.PER_CHILD_DEPENDANT:
            return Decimal(children), child_text
        case Basis.PER_DEPENDANT_VISA_YEAR_ADULT:
            return visa_years * adults, f"{years_text} x {partner_text}"
        case Basis.PER_DEPENDANT_VISA_YEAR_CHILD:
            return visa_years * children, f"{years_text} x {child_text}"


def _first_year_then_periods(
    first_year: Decimal, per_period: Decimal, months: int, currency: str
) -> tuple[Decimal, str]:
    """The Immigration Skills Charge pattern: the first 12 months, then each
    further 6 months or part of 6 months, ``ceil((months - 12) / 6)``."""
    remaining = max(months - MONTHS_PER_YEAR, 0)
    periods = -(-remaining // _PERIOD_MONTHS)
    amount = first_year + per_period * periods
    formula = f"{format_money(first_year, currency)} for the first 12 months"
    if periods:
        formula += (
            f" + {format_money(per_period, currency)} x {plural(periods, 'further 6-month period')}"
        )
    return amount, formula
