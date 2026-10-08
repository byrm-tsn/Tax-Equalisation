"""Immigration costs computed from the pack's formulas."""

from __future__ import annotations

import dataclasses
from decimal import Decimal
from typing import Any

import pytest

from teq_guidance import (
    CostLine,
    GuidancePack,
    TailoringAnswers,
    compute_costs,
    cost_subtotals,
    load_pack,
    maintenance_funds,
)
from teq_guidance.model import Basis, Payer


def _lines(pack: GuidancePack, answers: TailoringAnswers) -> dict[str, CostLine]:
    return {line.id: line for line in compute_costs(pack, answers)}


def _amount(line: CostLine) -> Decimal:
    assert line.amount is not None, line.id
    return line.amount


def test_reference_scenario(pack: GuidancePack, reference: TailoringAnswers) -> None:
    lines = _lines(pack, reference)
    assert _amount(lines["cos_fee"]) == Decimal("525")
    assert _amount(lines["immigration_skills_charge"]) == Decimal("2640")
    assert _amount(lines["visa_application_fee"]) == Decimal("819")
    assert _amount(lines["immigration_health_surcharge"]) == Decimal("2070")
    assert "sponsor_licence_fee" not in lines

    subtotals = cost_subtotals(list(lines.values()))
    assert subtotals.employer_mandatory == Decimal("3165")
    assert subtotals.applicant_side == Decimal("2889")
    assert subtotals.employer_by_policy == Decimal("2889")
    assert subtotals.applicant_only == Decimal("0")
    assert subtotals.employer_including_policy == Decimal("6054")


def test_reference_tb_test_is_in_lira_and_listed_separately(
    pack: GuidancePack, reference: TailoringAnswers
) -> None:
    tb = _lines(pack, reference)["tb_test_fee"]
    assert tb.currency == "TRY"
    assert tb.amount is None
    assert (tb.amount_min, tb.amount_max) == (Decimal("3000"), Decimal("5000"))
    assert not tb.in_subtotal
    assert tb.formula == "TRY 3,000 to TRY 5,000 (typical range)"


def test_ranges_and_optional_services_stay_out_of_subtotals(
    pack: GuidancePack, reference: TailoringAnswers
) -> None:
    lines = _lines(pack, reference)
    english = lines["english_test_fee"]
    assert (english.amount_min, english.amount_max) == (Decimal("150"), Decimal("200"))
    assert not english.in_subtotal
    for optional_id in ("priority_visa_service", "super_priority_visa_service"):
        assert lines[optional_id].optional
        assert not lines[optional_id].in_subtotal


def test_formula_text(pack: GuidancePack, reference: TailoringAnswers) -> None:
    lines = _lines(pack, reference)
    assert lines["immigration_health_surcharge"].formula == "£1,035 x 2 years"
    assert lines["immigration_skills_charge"].formula == (
        "£1,320 for the first 12 months + £660 x 2 further 6-month periods "
        "(medium or large sponsor)"
    )
    assert lines["visa_application_fee"].formula == "£819 fixed fee (up to 3 years, outside the UK)"
    assert lines["cos_fee"].formula == "£525 fixed fee"


def test_without_a_licence_adds_the_licence_fee(
    pack: GuidancePack, reference: TailoringAnswers
) -> None:
    answers = dataclasses.replace(reference, sponsor_licence_held=False)
    lines = _lines(pack, answers)
    licence = lines["sponsor_licence_fee"]
    assert _amount(licence) == Decimal("1682")
    assert licence.payer is Payer.EMPLOYER
    assert licence.cannot_be_recouped_from_worker
    priority = lines["sponsor_licence_priority"]
    assert priority.optional
    assert not priority.in_subtotal

    subtotals = cost_subtotals(list(lines.values()))
    assert subtotals.employer_mandatory == Decimal("3165") + Decimal("1682")
    assert subtotals.applicant_side == Decimal("2889")


def test_small_sponsor_rates(pack: GuidancePack, reference: TailoringAnswers) -> None:
    answers = dataclasses.replace(
        reference, sponsor_licence_held=False, sponsor_size="small_or_charitable"
    )
    lines = _lines(pack, answers)
    assert _amount(lines["sponsor_licence_fee"]) == Decimal("611")
    assert _amount(lines["immigration_skills_charge"]) == Decimal("480") + 2 * Decimal("240")


def test_one_partner_and_one_child(pack: GuidancePack, reference: TailoringAnswers) -> None:
    answers = dataclasses.replace(reference, dependants_adults=1, dependants_children=1)
    lines = _lines(pack, answers)
    assert _amount(lines["partner_visa_application_fee"]) == Decimal("819")
    assert _amount(lines["child_visa_application_fee"]) == Decimal("819")
    assert _amount(lines["partner_health_surcharge"]) == Decimal("2070")
    assert _amount(lines["child_health_surcharge"]) == Decimal("1552")
    assert lines["partner_health_surcharge"].formula == "£1,035 x 2 years x 1 partner"
    assert lines["child_health_surcharge"].formula == "£776 x 2 years x 1 child"

    subtotals = cost_subtotals(list(lines.values()))
    dependants = Decimal("819") * 2 + Decimal("2070") + Decimal("1552")
    assert subtotals.applicant_side == Decimal("2889") + dependants
    assert subtotals.employer_mandatory == Decimal("3165")


def test_children_scale_by_count(pack: GuidancePack, reference: TailoringAnswers) -> None:
    lines = _lines(pack, dataclasses.replace(reference, dependants_children=3))
    assert _amount(lines["child_visa_application_fee"]) == Decimal("2457")
    assert _amount(lines["child_health_surcharge"]) == Decimal("776") * 2 * 3
    assert lines["child_health_surcharge"].formula == "£776 x 2 years x 3 children"
    assert "partner_visa_application_fee" not in lines


def test_visa_longer_than_three_years(pack: GuidancePack, reference: TailoringAnswers) -> None:
    lines = _lines(pack, dataclasses.replace(reference, visa_length_years=5))
    assert _amount(lines["visa_application_fee"]) == Decimal("1618")
    assert _amount(lines["immigration_health_surcharge"]) == Decimal("5175")
    assert _amount(lines["immigration_skills_charge"]) == Decimal("1320") + 8 * Decimal("660")


@pytest.mark.parametrize(
    ("years", "expected"),
    # Up to the single-grant cap of 5 years; 10 years is priced as one 5-year grant (see
    # test_ten_years_is_priced_as_one_five_year_grant).
    [(1, "1320"), (2, "2640"), (3, "3960"), (4, "5280"), (5, "6600")],
)
def test_skills_charge_equals_annual_rate_for_whole_years(
    pack: GuidancePack, reference: TailoringAnswers, years: int, expected: str
) -> None:
    lines = _lines(pack, dataclasses.replace(reference, visa_length_years=years))
    assert _amount(lines["immigration_skills_charge"]) == Decimal(expected)


def test_inside_uk_fee_tiers(pack: GuidancePack, reference: TailoringAnswers) -> None:
    inside = dataclasses.replace(reference, application_location="inside_uk")
    assert _amount(_lines(pack, inside)["visa_application_fee"]) == Decimal("943")

    longer = dataclasses.replace(inside, visa_length_years=4)
    fee = _lines(pack, longer)["visa_application_fee"]
    assert fee.amount is None
    assert not fee.in_subtotal
    assert "does not hold" in fee.notes
    assert fee.formula.startswith("Amount not held in this pack")


def test_employer_lines_are_never_recoupable_and_basis_is_kept(
    pack: GuidancePack, reference: TailoringAnswers
) -> None:
    answers = dataclasses.replace(reference, sponsor_licence_held=False, dependants_adults=1)
    for line in compute_costs(pack, answers):
        if line.payer is Payer.EMPLOYER:
            assert line.cannot_be_recouped_from_worker
        assert isinstance(line.basis, Basis)
        for value in (line.amount, line.amount_min, line.amount_max):
            assert value is None or isinstance(value, Decimal)


def test_maintenance_funds(pack: GuidancePack, reference: TailoringAnswers) -> None:
    funds = maintenance_funds(pack, reference)
    assert funds.applies  # the sponsor is assumed not to certify maintenance
    assert funds.amount == Decimal("1270")
    assert funds.days_held == 28

    family = dataclasses.replace(reference, dependants_adults=1, dependants_children=3)
    funds = maintenance_funds(pack, family)
    assert funds.amount == Decimal("1270") + Decimal("285") + Decimal("315") + 2 * Decimal("200")
    assert funds.formula == (
        "£1,270 main applicant + £285 partner + £315 first child + £200 x 2 further children"
    )

    certified = dataclasses.replace(reference, sponsor_certifies_maintenance=True)
    assert not maintenance_funds(pack, certified).applies


# --------------------------------------------------------------- part years and the grant cap


def test_thirty_months(pack: GuidancePack, reference: TailoringAnswers) -> None:
    from teq_guidance.costs import HALF_YEAR_NOTE

    answers = dataclasses.replace(reference, visa_length_years=None, visa_length_months=30)
    lines = _lines(pack, answers)
    # 1,320 for the first 12 months + 660 x ceil(18 / 6) = 3 further periods
    isc = lines["immigration_skills_charge"]
    assert _amount(isc) == Decimal("3300")
    assert isc.formula.startswith("£1,320 for the first 12 months + £660 x 3 further")
    # Two whole years plus a final 6 months at half the annual amount.
    ihs = lines["immigration_health_surcharge"]
    assert _amount(ihs) == Decimal("2587.50")
    assert ihs.formula == "£1,035 x 2.5 years"
    assert HALF_YEAR_NOTE in ihs.notes
    # 30 months is up to 3 years for the application fee.
    assert _amount(lines["visa_application_fee"]) == Decimal("819")
    # The same with the years answer given and consistent (ceil(30 / 12) = 3).
    both = dataclasses.replace(reference, visa_length_years=3, visa_length_months=30)
    assert _amount(_lines(pack, both)["immigration_skills_charge"]) == Decimal("3300")


def test_part_year_over_six_months_is_a_full_year(
    pack: GuidancePack, reference: TailoringAnswers
) -> None:
    from teq_guidance.costs import HALF_YEAR_NOTE

    lines = _lines(
        pack, dataclasses.replace(reference, visa_length_years=None, visa_length_months=32)
    )
    assert _amount(lines["immigration_health_surcharge"]) == Decimal("3105")
    assert HALF_YEAR_NOTE not in lines["immigration_health_surcharge"].notes
    assert _amount(lines["immigration_skills_charge"]) == Decimal("1320") + 4 * Decimal("660")
    six = _lines(pack, dataclasses.replace(reference, visa_length_years=None, visa_length_months=6))
    assert _amount(six["immigration_health_surcharge"]) == Decimal("517.50")
    assert _amount(six["immigration_skills_charge"]) == Decimal("1320")


def test_dependant_surcharges_use_the_half_year(
    pack: GuidancePack, reference: TailoringAnswers
) -> None:
    answers = dataclasses.replace(
        reference,
        visa_length_years=None,
        visa_length_months=30,
        dependants_adults=1,
        dependants_children=2,
    )
    lines = _lines(pack, answers)
    assert _amount(lines["partner_health_surcharge"]) == Decimal("2587.50")
    assert lines["partner_health_surcharge"].formula == "£1,035 x 2.5 years x 1 partner"
    assert _amount(lines["child_health_surcharge"]) == Decimal("776") * Decimal("2.5") * 2


def test_sixty_six_months_is_priced_as_one_sixty_month_grant(
    pack: GuidancePack, reference: TailoringAnswers
) -> None:
    from teq_guidance import build_panel

    answers = dataclasses.replace(
        reference, visa_length_years=None, visa_length_months=66, dependants_adults=1
    )
    lines = _lines(pack, answers)
    assert _amount(lines["immigration_skills_charge"]) == Decimal("1320") + 8 * Decimal("660")
    assert _amount(lines["immigration_health_surcharge"]) == Decimal("5175")
    assert _amount(lines["partner_health_surcharge"]) == Decimal("5175")
    assert _amount(lines["visa_application_fee"]) == Decimal("1618")
    for line_id in ("immigration_skills_charge", "immigration_health_surcharge"):
        assert "single grant of 60 months" in lines[line_id].notes
        assert "remaining 6 months" in lines[line_id].notes
    assert "single grant" not in lines["cos_fee"].notes

    panel = build_panel(answers)
    warnings = panel["warnings"]
    assert isinstance(warnings, list)
    codes = [w["code"] for w in warnings]
    assert codes == ["VISA_LENGTH_EXCEEDS_SINGLE_GRANT"]
    assert "66 months" in warnings[0]["text"]
    assert "further application" in warnings[0]["text"]
    costs = panel["costs"]
    assert isinstance(costs, dict)
    assert (costs["visa_months_requested"], costs["visa_months_priced"]) == (66, 60)


def test_ten_years_is_priced_as_one_five_year_grant(
    pack: GuidancePack, reference: TailoringAnswers
) -> None:
    from teq_guidance import build_panel

    answers = dataclasses.replace(reference, visa_length_years=10)
    lines = _lines(pack, answers)
    assert _amount(lines["immigration_skills_charge"]) == Decimal("6600")
    assert _amount(lines["immigration_health_surcharge"]) == Decimal("5175")
    assert lines["immigration_health_surcharge"].formula == "£1,035 x 5 years"
    assert "remaining 60 months" in lines["immigration_skills_charge"].notes
    warnings = build_panel(answers)["warnings"]
    assert isinstance(warnings, list)
    assert [w["code"] for w in warnings] == ["VISA_LENGTH_EXCEEDS_SINGLE_GRANT"]
    assert "120 months" in warnings[0]["text"]
    # Exactly 5 years needs no further application.
    five = build_panel(dataclasses.replace(reference, visa_length_years=5))
    assert five["warnings"] == []


@pytest.mark.parametrize(
    ("years", "months", "message"),
    [
        (None, 0, "1 or more"),
        (None, 121, "maximum of 120 months"),
        (None, True, "whole number"),
        (2, 30, "make them agree"),
    ],
)
def test_visa_months_validation(
    pack: GuidancePack, reference: TailoringAnswers, years: int | None, months: Any, message: str
) -> None:
    from teq_guidance import TailoringError

    answers = dataclasses.replace(reference, visa_length_years=years, visa_length_months=months)
    with pytest.raises(TailoringError, match=message):
        compute_costs(pack, answers)


def test_visa_months_from_form_data(pack: GuidancePack) -> None:
    from teq_guidance import build_panel

    answers = TailoringAnswers.from_mapping({"visa_length_months": "30", "sponsor_size": ""})
    assert answers.visa_length_months == 30
    tailoring = build_panel(answers)["tailoring"]
    assert isinstance(tailoring, dict)
    assert tailoring["visa_length_months"] == 30
    visa = next(q for q in tailoring["questions"] if q["id"] == "visa_length_years")
    assert visa["value"] == 3
    assert visa["answered"] is True
    assert "visa_length_years" not in {q["id"] for q in tailoring["unanswered"]}
    assert build_panel()["tailoring"]["visa_length_months"] == 24  # type: ignore[index]


# --------------------------------------------------------------------------- decimal context


def test_hostile_decimal_context_cannot_break_the_panel(reference: TailoringAnswers) -> None:
    import decimal

    from teq_guidance import build_panel

    family = dataclasses.replace(
        reference, visa_length_years=None, visa_length_months=30, dependants_children=3
    )
    expected = build_panel(family)
    context = decimal.getcontext()
    saved = context.copy()
    try:
        context.prec = 5
        context.rounding = decimal.ROUND_FLOOR
        context.traps[decimal.Inexact] = True
        assert build_panel(family) == expected
        lines = compute_costs(load_pack(), family)
        assert cost_subtotals(lines).applicant_side == Decimal(
            expected["costs"]["subtotals"]["applicant_side"]  # type: ignore[index]
        )
    finally:
        decimal.setcontext(saved)
    assert decimal.getcontext().prec == saved.prec
