"""Immigration costs computed from the pack's formulas."""

from __future__ import annotations

import dataclasses
from decimal import Decimal

import pytest

from teq_guidance import (
    CostLine,
    GuidancePack,
    TailoringAnswers,
    compute_costs,
    cost_subtotals,
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
    [(1, "1320"), (2, "2640"), (3, "3960"), (4, "5280"), (10, "13200")],
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
