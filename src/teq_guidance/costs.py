"""Immigration costs as formulas over the tailoring answers.

Each cost item has a ``basis`` (fixed, per visa year, first year then per six
months, per dependant, per dependant per year), optional tiers keyed on the
answers (visa length, where the application is made, sponsor size), a payer and
a flag saying whether it may be recovered from the worker. The amounts computed
here are shown in the immigration panel only: they are never added to any
employment-cost figure.
"""

from __future__ import annotations

from decimal import Decimal

from teq_guidance.formatting import format_money, format_range, plural
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
    ResolvedAnswers,
    TailoringAnswers,
    applicable,
    evaluate,
    resolve_answers,
)

_ZERO = Decimal("0.00")
_MONTHS_PER_YEAR = 12
_PERIOD_MONTHS = 6


def compute_costs(pack: GuidancePack, answers: TailoringAnswers | None = None) -> list[CostLine]:
    """Compute every applicable cost item for the answers, in pack order.

    Optional services are included with ``optional`` set; ranges, non-GBP amounts
    and amounts the pack does not hold have ``in_subtotal`` false. Use
    ``cost_subtotals`` for the payer subtotals.
    """
    selected = applicable(pack, answers)
    return [_cost_line(item, selected.answers) for item in selected.costs]


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

    years = answers.count("visa_length_years")
    adults = answers.count("dependants_adults")
    children = answers.count("dependants_children")
    multiplier, multiplier_text = _multiplier(item.basis, years, adults, children)

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
        amount, formula = _first_year_then_periods(unit, per_period or _ZERO, years, currency)
    else:
        amount = unit * multiplier
        if item.basis is Basis.FIXED:
            formula = f"{format_money(unit, currency)} fixed fee"
        else:
            formula = f"{format_money(unit, currency)} x {multiplier_text}"
    if tier_label:
        formula += f" ({tier_label})"

    notes = " ".join(part for part in (missing_note, item.notes) if part)
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


def _multiplier(basis: Basis, years: int, adults: int, children: int) -> tuple[int, str]:
    """How many units of the amount are due, and the words for the formula."""
    year_text = plural(years, "year")
    partner_text = plural(adults, "partner")
    child_text = plural(children, "child", "children")
    match basis:
        case Basis.FIXED | Basis.FIRST_YEAR_THEN_PER_6_MONTHS:
            return 1, ""
        case Basis.PER_VISA_YEAR:
            return years, year_text
        case Basis.PER_ADULT_DEPENDANT:
            return adults, partner_text
        case Basis.PER_CHILD_DEPENDANT:
            return children, child_text
        case Basis.PER_DEPENDANT_VISA_YEAR_ADULT:
            return years * adults, f"{year_text} x {partner_text}"
        case Basis.PER_DEPENDANT_VISA_YEAR_CHILD:
            return years * children, f"{year_text} x {child_text}"


def _first_year_then_periods(
    first_year: Decimal, per_period: Decimal, years: int, currency: str
) -> tuple[Decimal, str]:
    """The Immigration Skills Charge pattern: the first 12 months, then each
    further 6 months or part of 6 months."""
    months = years * _MONTHS_PER_YEAR
    remaining = max(months - _MONTHS_PER_YEAR, 0)
    periods = -(-remaining // _PERIOD_MONTHS)
    amount = first_year + per_period * periods
    formula = f"{format_money(first_year, currency)} for the first 12 months"
    if periods:
        formula += (
            f" + {format_money(per_period, currency)} x {plural(periods, 'further 6-month period')}"
        )
    return amount, formula
