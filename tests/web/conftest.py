"""Shared fixtures for the web, API, operations and command tests."""

from __future__ import annotations

import re
from collections.abc import Callable
from html.parser import HTMLParser
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


class _RowParser(HTMLParser):
    """Collects every table row as the visible text of its cells (``th`` and ``td``)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self.rows.append([])
        elif tag in ("td", "th"):
            self._cell = []
        elif tag == "br" and self._cell is not None:
            self._cell.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("td", "th") and self._cell is not None and self.rows:
            self.rows[-1].append(re.sub(r"\s+", " ", "".join(self._cell)).strip())
            self._cell = None

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)


def section(html: str, start_id: str, end_id: str) -> str:
    """The part of a page from the element with ``id=start_id`` to ``id=end_id``."""
    return html[html.index(f'id="{start_id}"') : html.index(f'id="{end_id}"')]


def table_rows(html: str) -> list[list[str]]:
    """Every table row in ``html`` as its cells' visible text, whitespace collapsed."""
    parser = _RowParser()
    parser.feed(html)
    return [row for row in parser.rows if row]


def rows_by_label(html: str) -> dict[str, list[str]]:
    """Table rows keyed by their first cell (later rows with the same label win)."""
    return {row[0]: row[1:] for row in table_rows(html)}


@pytest.fixture
def rows_of() -> Callable[[str], dict[str, list[str]]]:
    """``rows_of(html)``: the table rows in ``html`` keyed by their first cell."""
    return rows_by_label


@pytest.fixture
def section_of() -> Callable[[str, str, str], str]:
    """``section_of(html, start_id, end_id)``: a slice of a page between two ids."""
    return section


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
