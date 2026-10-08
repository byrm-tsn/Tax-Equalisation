"""The input form: validation in plain English, engine errors on the right fields, prefill."""

from __future__ import annotations

from typing import Any

import pytest
from django.test import Client

from teq_engine import REFERENCE_RATES_AS_OF, reference_example
from teq_web.scenarios.forms import ItemFormSet, ScenarioForm, initial_from_inputs, parse_years
from teq_web.scenarios.services import decode_scenario, encode_scenario


def _submit(client: Client, data: dict[str, str]) -> Any:
    return client.post("/", data)


def _build(data: dict[str, str]) -> tuple[ScenarioForm, Any, Any]:
    form = ScenarioForm(data)
    formset = ItemFormSet(data, prefix="items")
    assert form.is_valid(), form.errors
    assert formset.is_valid(), formset.errors
    return form, formset, form.to_inputs(formset)


def test_reference_form_reproduces_the_reference_inputs(form_data: dict[str, str]) -> None:
    _, _, outcome = _build(form_data)
    assert outcome is not None
    inputs, rates_as_of = outcome
    assert inputs.inputs_hash() == reference_example().inputs_hash()
    assert rates_as_of == REFERENCE_RATES_AS_OF


def test_get_shows_the_form_with_an_example_link_and_today(client: Client) -> None:
    response = client.get("/")
    html = response.content.decode()
    assert response.status_code == 200
    assert 'href="/example"' in html
    assert "Load the reference example" in html
    assert "csrfmiddlewaretoken" in html
    assert 'name="rates_as_of"' in html
    for name in ("salary_amount", "hypo_override", "cola_amount", "housing_amount"):
        assert f'<label for="id_{name}">' in html
    assert response["Content-Security-Policy"].startswith("default-src 'self'")


def test_missing_salary_is_reported_inline_and_in_the_summary(
    client: Client, form_data: dict[str, str]
) -> None:
    form_data["salary_amount"] = ""
    response = _submit(client, form_data)
    html = response.content.decode()
    assert response.status_code == 200
    assert "There is a problem" in html
    assert 'href="#id_salary_amount"' in html
    assert html.count("Enter the base salary.") == 2  # summary and inline


def test_unreadable_amount_explains_the_format(client: Client, form_data: dict[str, str]) -> None:
    form_data["salary_amount"] = "ninety thousand"
    html = _submit(client, form_data).content.decode()
    assert "Enter the base salary as a number, such as 90000 or 90,000.00." in html


def test_hypothetical_tax_above_salary_uses_the_engine_rule(
    client: Client, form_data: dict[str, str]
) -> None:
    form_data["hypo_override"] = "95000"
    html = _submit(client, form_data).content.decode()
    assert "The hypothetical tax must be less than the annual salary (in pounds)." in html
    assert 'href="#id_hypo_override"' in html


def test_override_without_an_amount(client: Client, form_data: dict[str, str]) -> None:
    form_data["hypo_override"] = ""
    html = _submit(client, form_data).content.decode()
    assert "Enter the hypothetical tax, or choose to calculate it" in html


def test_scotland_is_refused_with_the_capability_message(
    client: Client, form_data: dict[str, str]
) -> None:
    form_data["region"] = "SCT"
    response = _submit(client, form_data)
    html = response.content.decode()
    assert response.status_code == 200
    assert 'href="#id_region"' in html
    assert "The host region SCT is not supported for GB" in html
    assert "This region is not supported yet" in html
    assert "Supported routes" in html
    assert "Turkey to the United Kingdom (England)" in html
    assert "What adding it would require" in html


def test_an_unsupported_country_pair_is_refused(client: Client, form_data: dict[str, str]) -> None:
    form_data["home_country"] = "DE"
    html = _submit(client, form_data).content.decode()
    assert "The route DE to GB is not supported" in html
    assert "This route is not supported yet" in html


def test_lira_salary_without_an_exchange_rate(client: Client, form_data: dict[str, str]) -> None:
    form_data |= {"salary_currency": "TRY", "salary_amount": "5000000", "hypo_override": "30000"}
    html = _submit(client, form_data).content.decode()
    assert "Enter the exchange rate and its date: they are needed for a salary in lira." in html
    assert 'href="#id_fx_rate"' in html


def test_calculated_hypothetical_tax_needs_a_rate(
    client: Client, form_data: dict[str, str]
) -> None:
    form_data |= {"hypo_method": "CALCULATED", "hypo_override": "30000"}
    html = _submit(client, form_data).content.decode()
    assert "needed for a calculated hypothetical tax" in html


def test_half_an_exchange_rate_is_caught(client: Client, form_data: dict[str, str]) -> None:
    form_data["fx_rate"] = "55.25"
    html = _submit(client, form_data).content.decode()
    assert "Enter the date of the exchange rate." in html


def test_rates_date_before_any_rates(client: Client, form_data: dict[str, str]) -> None:
    form_data["rates_as_of"] = "2020-01-01"
    html = _submit(client, form_data).content.decode()
    assert "No tax rates are held for 2020-01-01" in html


def test_incomplete_item_rows_are_reported(client: Client, form_data: dict[str, str]) -> None:
    form_data |= {"items-1-label": "Something", "items-2-kind": "BONUS", "items-2-years": "x"}
    html = _submit(client, form_data).content.decode()
    assert "Item 2: Choose what this item is." in html
    assert "Item 2: Enter the amount." in html
    assert 'href="#id_items-1-kind"' in html
    assert "Item 3: Enter ALL, or the years as numbers" in html


def test_engine_item_errors_land_on_the_row(client: Client, form_data: dict[str, str]) -> None:
    form_data |= {
        "items-0-kind": "BONUS",
        "items-0-amount": "5000",
        "items-0-frequency": "ONE_OFF",
        "items-0-years": "",
        "items-2-kind": "SCHOOL_FEES",
        "items-2-amount": "1000",
        "items-2-years": "3",
        "items-3-kind": "HOUSING",
        "items-3-amount": "1000",
        "items-3-treatment": "EXEMPT_CAPPED",
    }
    html = _submit(client, form_data).content.decode()
    assert "Item 1: A one-off item must list the year(s) it is paid in." in html
    assert 'href="#id_items-0-years"' in html
    assert "Item 3: Every year must be within the assignment length (1 to 2)." in html
    assert 'href="#id_items-2-years"' in html
    assert "Item 4: Only relocation can be exempt within the relocation cap" in html


def test_housing_contribution_above_the_value(client: Client, form_data: dict[str, str]) -> None:
    form_data["housing_contribution"] = "40000"
    html = _submit(client, form_data).content.decode()
    assert "The employee contribution cannot exceed the item amount." in html
    assert 'href="#id_housing_contribution"' in html


def test_additional_items_reach_the_engine(form_data: dict[str, str]) -> None:
    form_data |= {
        "items-0-kind": "BONUS",
        "items-0-label": "Annual bonus",
        "items-0-amount": "10,000",
        "items-0-years": "1, 2",
        "items-1-kind": "SCHOOL_FEES",
        "items-1-amount": "1500",
        "items-1-frequency": "MONTHLY",
        "items-1-treatment": "EXEMPT",
    }
    _, _, outcome = _build(form_data)
    assert outcome is not None
    inputs, _ = outcome
    extras = [item for item in inputs.items if item.id.startswith("extra-")]
    assert [item.kind.value for item in extras] == ["BONUS", "SCHOOL_FEES"]
    assert extras[0].years == (1, 2)
    assert str(extras[1].amount) == "1500.00"
    assert extras[1].treatment is not None
    assert extras[1].treatment.value == "EXEMPT"


def test_calculated_method_ignores_a_leftover_override(form_data: dict[str, str]) -> None:
    form_data |= {
        "hypo_method": "CALCULATED",
        "hypo_override": "30000",
        "fx_rate": "55.25",
        "fx_date": "2026-10-01",
    }
    _, _, outcome = _build(form_data)
    assert outcome is not None
    assert outcome[0].hypothetical_tax.override is None


def test_prg_redirects_to_a_results_url(client: Client, form_data: dict[str, str]) -> None:
    response = _submit(client, form_data)
    assert response.status_code == 303
    location = response["Location"]
    assert location.startswith("/estimate?s=")
    inputs, rates_as_of = decode_scenario(location.split("s=", 1)[1])
    assert inputs.inputs_hash() == reference_example().inputs_hash()
    assert rates_as_of == REFERENCE_RATES_AS_OF
    page = client.get(location)
    assert page.status_code == 200
    assert "188,676" in page.content.decode()


def test_edit_link_prefills_the_form(client: Client, reference_token: str) -> None:
    html = client.get("/", {"s": reference_token}).content.decode()
    assert 'name="salary_amount" value="90000"' in html
    assert 'name="hypo_override" value="30000"' in html
    assert 'name="housing_amount" value="30000"' in html
    assert 'name="relocation_amount" value="8000"' in html
    assert 'name="rates_as_of" value="2026-10-08"' in html


def test_prefill_round_trips_extra_items() -> None:
    from teq_engine import ScenarioInput
    from teq_engine.reference import reference_example_data

    data = reference_example_data()
    data["items"] = [
        *data["items"],  # type: ignore[misc]
        {"id": "bonus", "kind": "BONUS", "amount": "5000.00", "frequency": "ANNUAL", "years": [2]},
    ]
    inputs = ScenarioInput.model_validate(data)
    initial, rows = initial_from_inputs(inputs, REFERENCE_RATES_AS_OF)
    assert initial["cola_amount"] == "6000"
    assert rows == [
        {
            "kind": "BONUS",
            "label": "",
            "amount": "5000",
            "frequency": "ANNUAL",
            "years": "2",
            "treatment": "",
        }
    ]


def test_a_damaged_edit_link_shows_an_empty_form(client: Client) -> None:
    response = client.get("/", {"s": "garbage!"})
    assert response.status_code == 200
    assert "The link could not be read" in response.content.decode()


def test_unchanged_resubmission_gives_the_same_link(
    client: Client, form_data: dict[str, str]
) -> None:
    first = _submit(client, form_data)["Location"]
    assert first == f"/estimate?s={encode_scenario(reference_example(), REFERENCE_RATES_AS_OF)}"


@pytest.mark.parametrize(
    ("text", "expected"),
    [("", "ALL"), ("all", "ALL"), ("1", [1]), ("1, 2", [1, 2]), ("1 and 3", [1, 3])],
)
def test_parse_years(text: str, expected: object) -> None:
    assert parse_years(text) == expected


def test_exchange_rate_dated_after_the_rates_date(
    client: Client, form_data: dict[str, str]
) -> None:
    form_data |= {"fx_rate": "55.25", "fx_date": "2026-11-01"}
    html = client.post("/", form_data).content.decode()
    assert "There is a problem" in html
    assert 'href="#id_fx_date"' in html
