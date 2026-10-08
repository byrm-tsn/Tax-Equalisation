"""Scenario input validation, canonicalisation and hashing."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from teq_engine import ScenarioInput
from teq_engine.types import CompensationItem


def scenario(data: dict[str, Any]) -> ScenarioInput:
    return ScenarioInput.model_validate(data)


def _errors(data: dict[str, Any]) -> list[tuple[tuple[Any, ...], str]]:
    with pytest.raises(ValidationError) as info:
        ScenarioInput.model_validate(data)
    return [(tuple(e["loc"]), e["msg"]) for e in info.value.errors()]


def test_reference_inputs_validate(ref_data: dict[str, Any]) -> None:
    inputs = scenario(ref_data)
    assert inputs.salary.amount == Decimal("90000.00")
    assert inputs.items[2].years == (1,)


def test_dict_and_json_inputs_are_equal(ref_data: dict[str, Any]) -> None:
    assert scenario(ref_data) == ScenarioInput.model_validate_json(json.dumps(ref_data))


def test_money_is_canonicalised_for_hashing(ref_data: dict[str, Any]) -> None:
    a = scenario(ref_data)
    ref_data["salary"]["amount"] = "90000"
    ref_data["items"][0]["amount"] = "6000.0"
    b = scenario(ref_data)
    assert a == b
    assert a.inputs_hash() == b.inputs_hash()
    assert a.canonical_json() == b.canonical_json()
    assert '"90000.00"' in a.canonical_json()


def test_hash_changes_with_inputs(ref_data: dict[str, Any]) -> None:
    a = scenario(ref_data)
    ref_data["items"][1]["amount"] = "30001.00"
    assert scenario(ref_data).inputs_hash() != a.inputs_hash()


@pytest.mark.parametrize("bad", [90000, 90000.0, True, "9e99999", "NaN", "90000.001", "-1.00"])
def test_salary_amount_must_be_a_decimal_string(ref_data: dict[str, Any], bad: object) -> None:
    ref_data["salary"]["amount"] = bad
    errors = _errors(ref_data)
    assert errors[0][0][:2] == ("salary", "amount")


def test_bare_json_numbers_are_refused(ref_data: dict[str, Any]) -> None:
    text = json.dumps(ref_data).replace('"90000.00"', "90000.00")
    with pytest.raises(ValidationError):
        ScenarioInput.model_validate_json(text)


def test_extra_fields_are_refused(ref_data: dict[str, Any]) -> None:
    ref_data["salary"]["bonus"] = "1.00"
    assert _errors(ref_data)[0][0] == ("salary", "bonus")


def test_hypothetical_tax_must_be_below_salary(ref_data: dict[str, Any]) -> None:
    ref_data["hypothetical_tax"]["override"] = "90000.00"
    assert (
        ("hypothetical_tax", "override"),
        "the hypothetical tax must be below the annual salary in GBP",
    ) in _errors(ref_data)


def test_override_required_and_forbidden(ref_data: dict[str, Any]) -> None:
    ref_data["hypothetical_tax"]["override"] = None
    assert _errors(ref_data)[0][0] == ("hypothetical_tax", "override")
    ref_data["hypothetical_tax"] = {"method": "CALCULATED", "override": "1.00"}
    ref_data["fx"] = {"rate": "65.7", "as_of": "2026-10-08"}
    assert _errors(ref_data)[0][0] == ("hypothetical_tax", "override")


@pytest.mark.parametrize(
    "change",
    [
        {"salary": {"amount": "3000000.00", "currency": "TRY"}},
        {"hypothetical_tax": {"method": "CALCULATED"}},
        {"assumptions": {"social_security": "HOME_SCHEME_AGREEMENT"}},
    ],
)
def test_fx_snapshot_required(ref_data: dict[str, Any], change: dict[str, Any]) -> None:
    for key, value in change.items():
        ref_data[key] = value
    assert ("fx",) in [loc for loc, _ in _errors(ref_data)]


def test_items_must_be_gbp(ref_data: dict[str, Any]) -> None:
    ref_data["items"][0]["currency"] = "TRY"
    assert (("items", 0, "currency"), "in v1 all compensation items must be in GBP") in _errors(
        ref_data
    )


def test_item_years_within_assignment(ref_data: dict[str, Any]) -> None:
    ref_data["items"][2]["years"] = [3]
    loc, msg = _errors(ref_data)[0]
    assert loc == ("items", 2, "years")
    assert "within the assignment length" in msg
    ref_data["items"][2]["years"] = [1, 1]
    assert _errors(ref_data)[0][1] == "years must not repeat"
    ref_data["items"][2]["years"] = "ALL"
    assert "one-off" in _errors(ref_data)[0][1]


def test_duplicate_ids_and_salary_kind_refused(ref_data: dict[str, Any]) -> None:
    ref_data["items"][1]["id"] = "cola"
    ref_data["items"].append({"id": "pay", "kind": "SALARY", "amount": "1.00", "currency": "GBP"})
    messages = [m for _, m in _errors(ref_data)]
    assert "duplicate item id 'cola'" in messages
    assert any("salary block" in m for m in messages)


def test_exempt_capped_only_for_relocation(ref_data: dict[str, Any]) -> None:
    ref_data["items"][1]["treatment"] = "EXEMPT_CAPPED"
    assert _errors(ref_data)[0][0] == ("items", 1, "treatment")


def test_employee_contribution_rules(ref_data: dict[str, Any]) -> None:
    ref_data["items"][0]["employee_contribution"] = "100.00"
    assert _errors(ref_data)[0][0] == ("items", 0, "employee_contribution")
    ref_data["items"][0].pop("employee_contribution")
    ref_data["items"][1]["employee_contribution"] = "40000.00"
    assert "cannot exceed" in _errors(ref_data)[0][1]


def test_assignment_length_bounds(ref_data: dict[str, Any]) -> None:
    for length in (0, 11):
        ref_data["assignment"]["length_years"] = length
        assert _errors(ref_data)[0][0][:2] == ("assignment", "length_years")
    ref_data["assignment"]["length_years"] = 10
    assert scenario(ref_data).assignment.length_years == 10


def test_too_many_items(ref_data: dict[str, Any]) -> None:
    ref_data["items"] = [
        {"id": f"i{n}", "kind": "COLA", "amount": "1.00", "currency": "GBP"} for n in range(51)
    ]
    assert _errors(ref_data)[0][0] == ("items",)


def test_non_resident_refused(ref_data: dict[str, Any]) -> None:
    ref_data["assumptions"]["uk_resident"] = False
    assert _errors(ref_data)[0][0] == ("assumptions", "uk_resident")


def test_zero_salary_requires_zero_hypothetical_tax(ref_data: dict[str, Any]) -> None:
    ref_data["salary"]["amount"] = "0.00"
    assert "zero when the salary is zero" in _errors(ref_data)[0][1]
    ref_data["hypothetical_tax"]["override"] = "0.00"
    assert scenario(ref_data).annual_salary_gbp() == 0


def test_monthly_amounts_are_annualised(ref_data: dict[str, Any]) -> None:
    ref_data["salary"] = {"amount": "7500.00", "currency": "GBP", "frequency": "MONTHLY"}
    ref_data["items"][0]["frequency"] = "MONTHLY"
    ref_data["items"][0]["amount"] = "500.00"
    inputs = scenario(ref_data)
    assert inputs.annual_salary_gbp() == Decimal("90000.00")
    assert inputs.items[0].annual_amount == Decimal("6000.00")


def test_try_salary_conversion(ref_data: dict[str, Any]) -> None:
    ref_data["salary"] = {"amount": "5913000.00", "currency": "TRY"}
    ref_data["fx"] = {"rate": "65.7", "as_of": "2026-10-08"}
    inputs = scenario(ref_data)
    assert inputs.annual_salary_gbp() == Decimal("90000.00")
    assert inputs.annual_salary_try() == Decimal("5913000.00")
    assert inputs.fx is not None
    assert inputs.fx.as_of == date(2026, 10, 8)


def test_default_treatment_from_kind() -> None:
    item = CompensationItem.model_validate(
        {"id": "school", "kind": "SCHOOL_FEES", "amount": "1.00", "currency": "GBP"}
    )
    assert item.effective_treatment.value == "TAXABLE_BIK"
    item = CompensationItem.model_validate(
        {"id": "pension", "kind": "PENSION_EMPLOYER", "amount": "1.00", "currency": "GBP"}
    )
    assert item.effective_treatment.value == "EMPLOYER_ONLY"


def test_models_are_frozen(ref_data: dict[str, Any]) -> None:
    inputs = scenario(ref_data)
    with pytest.raises(ValidationError):
        inputs.salary.amount = Decimal("1")  # type: ignore[misc]


@pytest.mark.parametrize("treatment", ["EXEMPT", "EMPLOYER_ONLY", "NET_CASH", "GROSS_EQUALISED"])
def test_relocation_cannot_escape_the_cap(ref_data: dict[str, Any], treatment: str) -> None:
    ref_data["items"][2]["treatment"] = treatment
    ref_data["items"][2]["amount"] = "50000.00"
    loc, message = _errors(ref_data)[0]
    assert loc == ("items", 2, "treatment")
    assert "EXEMPT_CAPPED" in message
    assert "TAXABLE_BIK" in message
    assert treatment in message


@pytest.mark.parametrize("treatment", [None, "EXEMPT_CAPPED", "TAXABLE_BIK"])
def test_relocation_treatments_allowed(ref_data: dict[str, Any], treatment: str | None) -> None:
    if treatment is not None:
        ref_data["items"][2]["treatment"] = treatment
    assert scenario(ref_data).items[2].effective_treatment.value in {"EXEMPT_CAPPED", "TAXABLE_BIK"}


def test_try_to_gbp_conversion_rounds_half_up(ref_data: dict[str, Any]) -> None:
    # 1,000,000.50 / 100 = 10,000.005: half-up gives 10,000.01 (half-even would give .00)
    ref_data["salary"] = {"amount": "1000000.50", "currency": "TRY"}
    ref_data["hypothetical_tax"]["override"] = "1000.00"
    ref_data["fx"] = {"rate": "100", "as_of": "2026-10-01"}
    assert scenario(ref_data).annual_salary_gbp() == Decimal("10000.01")


def test_gbp_to_try_conversion_rounds_half_up(ref_data: dict[str, Any]) -> None:
    # 0.01 x 0.5 = 0.005 lira: half-up gives 0.01 (half-even would give 0.00)
    ref_data["salary"] = {"amount": "0.01", "currency": "GBP"}
    ref_data["hypothetical_tax"]["override"] = "0.00"
    ref_data["fx"] = {"rate": "0.5", "as_of": "2026-10-01"}
    assert scenario(ref_data).annual_salary_try() == Decimal("0.01")


def test_trace_money_rounds_half_up() -> None:
    from teq_engine.trace import fmt_money

    assert fmt_money(Decimal("0.005")) == "0.01"
    assert fmt_money(Decimal("2.665")) == "2.67"


def test_missing_region_hashes_as_england(ref_data: dict[str, Any]) -> None:
    england = scenario(ref_data)
    ref_data["route"].pop("region")
    missing = scenario(ref_data)
    assert missing.route.region is None
    assert missing.inputs_hash() == england.inputs_hash()
    assert '"region":"ENG"' in missing.canonical_json()
    ref_data["route"]["region"] = None
    assert scenario(ref_data).inputs_hash() == england.inputs_hash()


def test_negative_zero_never_survives(ref_data: dict[str, Any]) -> None:
    from teq_engine.money import to_decimal

    assert not to_decimal("-0").is_signed()
    assert not to_decimal("-0.00").is_signed()
    assert not to_decimal(Decimal("-0")).is_signed()
    ref_data["salary"]["amount"] = "0.00"
    ref_data["hypothetical_tax"]["override"] = "0"
    zero = scenario(ref_data)
    for negative in ("-0", "-0.00", "-0.0"):
        ref_data["hypothetical_tax"]["override"] = negative
        inputs = scenario(ref_data)
        assert inputs.hypothetical_tax.override is not None
        assert not inputs.hypothetical_tax.override.is_signed()
        assert inputs.inputs_hash() == zero.inputs_hash()
        assert "-0" not in inputs.canonical_json()
    ref_data["items"][0]["amount"] = "-0.00"
    assert '"-0' not in scenario(ref_data).canonical_json()


def test_item_years_are_sorted_for_hashing(ref_data: dict[str, Any]) -> None:
    ref_data["assignment"]["length_years"] = 3
    ref_data["items"][1]["years"] = [1, 3]
    ordered = scenario(ref_data)
    ref_data["items"][1]["years"] = [3, 1]
    reversed_ = scenario(ref_data)
    assert reversed_.inputs_hash() == ordered.inputs_hash()
    assert '"years":[1,3]' in reversed_.canonical_json()
    # A repeated year is refused rather than silently de-duplicated.
    ref_data["items"][1]["years"] = [3, 1, 3]
    assert _errors(ref_data)[0][1] == "years must not repeat"


def test_canonical_json_round_trips(ref_data: dict[str, Any]) -> None:
    ref_data["route"].pop("region")
    ref_data["assignment"]["length_years"] = 3
    ref_data["items"][1]["years"] = [3, 1]
    inputs = scenario(ref_data)
    again = ScenarioInput.model_validate_json(inputs.canonical_json())
    assert again.canonical_json() == inputs.canonical_json()
    assert again.inputs_hash() == inputs.inputs_hash()
