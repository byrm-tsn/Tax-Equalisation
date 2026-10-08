"""Route capability matrix and the period plan."""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from teq_engine import (
    ScenarioInput,
    UnsupportedRegionError,
    UnsupportedRouteError,
    calculate,
    default_provider,
    resolve_route,
    supported_routes,
)
from teq_engine.periods import build_period_plan, uk_tax_year_label, uk_tax_year_start_year
from teq_engine.types import Route


@pytest.mark.parametrize(("home", "host"), [("TR", "DE"), ("GB", "TR"), ("DE", "GB")])
def test_unsupported_country_pairs(home: str, host: str) -> None:
    with pytest.raises(UnsupportedRouteError) as info:
        resolve_route(Route(home=home, host=host))
    error = info.value
    assert type(error) is UnsupportedRouteError
    assert error.code == "ROUTE_UNSUPPORTED"
    assert error.supported_routes == supported_routes()
    assert "TR to GB (ENG)" in error.message


def test_scotland_is_refused() -> None:
    with pytest.raises(UnsupportedRegionError) as info:
        resolve_route(Route(home="TR", host="GB", region="SCT"))
    assert info.value.code == "REGION_NOT_SUPPORTED"
    assert isinstance(info.value, UnsupportedRouteError)
    assert "SCT" in info.value.message


def test_england_and_default_region() -> None:
    assert (
        resolve_route(Route(home="TR", host="GB", region="ENG")).host_income_tax_jurisdiction
        == "UK-ENG"
    )
    assert resolve_route(Route(home="TR", host="GB")).region == "ENG"


def test_calculate_refuses_before_computing(ref_data: dict[str, Any]) -> None:
    ref_data["route"] = {"home": "TR", "host": "GB", "region": "SCT"}
    with pytest.raises(UnsupportedRegionError):
        calculate(
            ScenarioInput.model_validate(ref_data),
            default_provider(),
            rates_as_of=date(2026, 10, 8),
        )


@pytest.mark.parametrize(
    ("on", "start"),
    [
        (date(2026, 4, 5), 2025),
        (date(2026, 4, 6), 2026),
        (date(2027, 1, 1), 2026),
        (date(2026, 10, 8), 2026),
    ],
)
def test_uk_tax_year(on: date, start: int) -> None:
    assert uk_tax_year_start_year(on) == start


def test_tax_year_label() -> None:
    assert uk_tax_year_label(2026) == "2026-27"
    assert uk_tax_year_label(2099) == "2099-00"


def test_whole_year_plan() -> None:
    plan = build_period_plan(3, date(2026, 10, 8))
    assert [
        (p.assignment_year, p.primary.uk_tax_year, p.primary.tr_calendar_year) for p in plan.periods
    ] == [
        (1, "2026-27", 2026),
        (2, "2027-28", 2027),
        (3, "2028-29", 2028),
    ]
    assert all(len(p.slices) == 1 and p.primary.fraction == 1 for p in plan.periods)
    with pytest.raises(ValueError, match="1 to 10"):
        build_period_plan(11, date(2026, 10, 8))


def test_start_date_anchors_the_plan(ref_data: dict[str, Any]) -> None:
    ref_data["assignment"]["start_date"] = "2027-01-15"
    result = calculate(
        ScenarioInput.model_validate(ref_data), default_provider(), rates_as_of=date(2026, 10, 8)
    )
    # 15 January 2027 is in UK tax year 2026-27 but Turkish year 2027 (carried forward).
    assert (result.year(1).uk_tax_year, result.year(1).tr_calendar_year) == ("2026-27", 2027)
    assert result.year(1).line("TOTAL_EMPLOYER_COST") == 188676


def test_rates_date_before_any_rate_set_is_refused(ref_data: dict[str, Any]) -> None:
    from teq_engine import RatesUnavailableError

    with pytest.raises(RatesUnavailableError):
        calculate(
            ScenarioInput.model_validate(ref_data),
            default_provider(),
            rates_as_of=date(2025, 10, 8),
        )
