"""Shared fixtures for the web, API, operations and command tests."""

from __future__ import annotations

from typing import Any

import pytest

from teq_engine import (
    REFERENCE_RATES_AS_OF,
    CalculationResult,
    calculate,
    default_provider,
    reference_example,
)
from teq_web.scenarios.services import encode_scenario


def blank_rows(count: int = 6) -> dict[str, str]:
    """Management form and empty additional-item rows, as a browser submits them."""
    data = {
        "items-TOTAL_FORMS": str(count),
        "items-INITIAL_FORMS": "0",
        "items-MIN_NUM_FORMS": "0",
        "items-MAX_NUM_FORMS": "6",
    }
    for index in range(count):
        data |= {
            f"items-{index}-kind": "",
            f"items-{index}-label": "",
            f"items-{index}-amount": "",
            f"items-{index}-frequency": "ANNUAL",
            f"items-{index}-years": "ALL",
            f"items-{index}-treatment": "",
        }
    return data


def reference_form_data() -> dict[str, str]:
    """The reference example as the input form would post it."""
    return {
        "home_country": "TR",
        "host_country": "GB",
        "region": "ENG",
        "length_years": "2",
        "rates_as_of": REFERENCE_RATES_AS_OF.isoformat(),
        "salary_amount": "90,000",
        "salary_currency": "GBP",
        "salary_frequency": "ANNUAL",
        "hypo_method": "OVERRIDE",
        "hypo_override": "£30,000.00",
        "hypo_includes_social_security": "on",
        "hypo_base": "SALARY_ONLY",
        "cola_amount": "6000",
        "housing_amount": "30000",
        "housing_contribution": "",
        "relocation_amount": "8000",
        "social_security": "UK_NIC",
        "fx_rate": "",
        "fx_date": "",
        "fx_source": "",
        **blank_rows(),
    }


@pytest.fixture
def form_data() -> dict[str, str]:
    return reference_form_data()


@pytest.fixture(scope="session")
def reference_token() -> str:
    return encode_scenario(reference_example(), REFERENCE_RATES_AS_OF)


@pytest.fixture(scope="session")
def reference_result() -> CalculationResult:
    return calculate(reference_example(), default_provider(), rates_as_of=REFERENCE_RATES_AS_OF)


@pytest.fixture(scope="session")
def reference_html(django_test_environment: Any, reference_token: str) -> str:
    from django.test import Client

    response = Client().get("/estimate", {"s": reference_token})
    assert response.status_code == 200
    return str(response.content.decode())
