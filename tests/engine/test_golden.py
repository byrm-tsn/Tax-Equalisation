"""The golden corpus: every case in tests/golden must reproduce exactly.

A numeric difference fails the build. Changing an expected figure requires updating the
golden file and bumping the engine version.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

import teq_engine
from teq_engine import BundledProvider, CalculationResult, ScenarioInput, calculate

GOLDEN_DIR = Path(__file__).resolve().parent.parent / "golden"
CASES = sorted(GOLDEN_DIR.glob("*.json"))


def _load(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as fh:
        data: dict[str, Any] = json.load(fh)
    return data


def test_corpus_is_present() -> None:
    names = {p.stem for p in CASES}
    assert {
        "reference_example",
        "relocation_10000",
        "home_scheme_social_security",
        "taper_band",
        "below_personal_allowance",
        "calculated_hypothetical_tax",
        "unsupported_route_tr_de",
        "unsupported_route_gb_tr",
        "unsupported_region_sct",
    } <= names


def _run(case: dict[str, Any], provider: BundledProvider) -> CalculationResult:
    inputs = ScenarioInput.model_validate_json(json.dumps(case["inputs"]))
    return calculate(inputs, provider, rates_as_of=date.fromisoformat(case["rates_as_of"]))


def _check_subset(actual: dict[str, Any], expected: dict[str, Any], where: str) -> None:
    for key, value in expected.items():
        assert key in actual, f"{where}.{key} missing"
        if isinstance(value, dict):
            _check_subset(actual[key], value, f"{where}.{key}")
        else:
            assert actual[key] == value, f"{where}.{key}: {actual[key]!r} != {value!r}"


@pytest.mark.parametrize("path", CASES, ids=[p.stem for p in CASES])
def test_golden_case(path: Path, provider: BundledProvider) -> None:
    case = _load(path)
    assert case["engine_version"] == teq_engine.__version__, "bump the golden files"

    if "expected_error" in case:
        expected_error = case["expected_error"]
        error_type = getattr(teq_engine, expected_error["type"])
        with pytest.raises(error_type) as info:
            _run(case, provider)
        error = info.value
        assert type(error).__name__ == expected_error["type"]
        assert error.code == expected_error["code"]
        assert error.supported_as_dicts() == expected_error["supported_routes"]
        return

    result = _run(case, provider)
    dumped = result.model_dump(mode="json")
    expected = case["expected"]

    if "rate_set_ids" in expected:
        assert list(result.rate_set_ids) == expected["rate_set_ids"]
    if "hypothetical_tax" in expected:
        _check_subset(dumped["hypothetical_tax"], expected["hypothetical_tax"], "hypothetical_tax")
    if "net_guarantee" in expected:
        _check_subset(dumped["net_guarantee"], expected["net_guarantee"], "net_guarantee")
    if "items" in expected:
        items = {item["id"]: item for item in dumped["items"]}
        for item_id, values in expected["items"].items():
            _check_subset(items[item_id], values, f"items.{item_id}")

    for expected_year in expected["years"]:
        year = result.year(expected_year["assignment_year"])
        for key in ("uk_tax_year", "tr_calendar_year"):
            if key in expected_year:
                assert getattr(year, key) == expected_year[key]
        gross_up = year.gross_up
        checks = {
            "gross_exact": str(gross_up.gross_exact),
            "gross_rounded": str(gross_up.gross_rounded),
            "net_delivered_exact": str(gross_up.net_delivered),
            "segment": gross_up.segment,
            "marginal_rate": str(gross_up.marginal_rate),
            "method": gross_up.method,
        }
        for key, actual in checks.items():
            if key in expected_year:
                assert actual == expected_year[key], f"year {year.assignment_year} {key}"
        for code, amount in expected_year["lines"].items():
            assert str(year.line(code)) == amount, f"year {year.assignment_year} {code}"
        if "lines" in expected_year and len(expected_year["lines"]) >= 13:
            assert [line.code.value for line in year.lines] == list(expected_year["lines"])
        assert year.decomposition.foots

    assert str(result.totals.total_employer_cost) == expected["totals"]["total_employer_cost"]

    codes = set(result.warning_codes())
    assert set(expected.get("warnings_include", [])) <= codes
    assert not set(expected.get("warnings_exclude", [])) & codes
    assumption_codes = list(result.assumption_codes())
    if "assumptions" in expected:
        assert assumption_codes == expected["assumptions"]
    assert set(expected.get("assumptions_include", [])) <= set(assumption_codes)
    assert not set(expected.get("assumptions_exclude", [])) & set(assumption_codes)
    if "trace_step_count" in expected:
        assert len(result.trace) == expected["trace_step_count"]
