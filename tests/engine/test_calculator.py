"""Calculator behaviour: FX checks, lira salaries, home-scheme mode, determinism."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from typing import Any

from teq_engine import LineCode, ScenarioInput, calculate, default_provider

AS_OF = date(2026, 10, 8)
FX = {"rate": "65.7", "as_of": "2026-10-08", "source": "test"}


def _calc(data: dict[str, Any], as_of: date = AS_OF):  # type: ignore[no-untyped-def]
    return calculate(ScenarioInput.model_validate(data), default_provider(), rates_as_of=as_of)


def _codes(result: Any) -> set[str]:
    return set(result.warning_codes())


def test_fx_stale_after_thirty_days(ref_data: dict[str, Any]) -> None:
    ref_data["fx"] = dict(FX, as_of="2026-09-08")  # exactly 30 days: not stale
    assert "FX_RATE_STALE" not in _codes(_calc(ref_data))
    ref_data["fx"] = dict(FX, as_of="2026-09-07")  # 31 days
    stale = [w for w in _calc(ref_data).warnings if w.code == "FX_RATE_STALE"]
    assert stale
    assert stale[0].params["days"] == "31"


def test_fx_implausible_outside_band(ref_data: dict[str, Any]) -> None:
    for rate, flagged in (("20", False), ("19.99", True), ("200", False), ("200.01", True)):
        ref_data["fx"] = dict(FX, rate=rate)
        assert ("FX_RATE_IMPLAUSIBLE" in _codes(_calc(ref_data))) is flagged, rate


def test_fx_supplied_is_recorded_and_override_shows_comparison(ref_data: dict[str, Any]) -> None:
    ref_data["fx"] = FX
    result = _calc(ref_data)
    assert "FX_RATE_USER_SUPPLIED" in _codes(result)
    assert result.hypothetical_tax.mode == "OVERRIDE"
    assert result.hypothetical_tax.calculated_for_comparison == Decimal("34212.20")
    override = next(a for a in result.assumptions if a.code == "HYPO_TAX_OVERRIDE")
    assert "£34212.20" in override.text
    # The FX snapshot alone does not change any figure under an override.
    assert result.year(1).line(LineCode.TOTAL_EMPLOYER_COST) == 188676


def test_lira_salary_converts_at_snapshot(ref_data: dict[str, Any]) -> None:
    ref_data["salary"] = {"amount": "5913000.00", "currency": "TRY"}
    ref_data["fx"] = FX
    result = _calc(ref_data)
    assert result.net_guarantee.salary == Decimal("90000.00")
    assert "SALARY_CONVERTED_AT_SNAPSHOT_FX" in _codes(result)
    assert result.year(1).line(LineCode.TOTAL_EMPLOYER_COST) == 188676
    assert result.year(1).line(LineCode.MULTIPLE_OF_SALARY) == Decimal("2.10")


def test_lira_salary_with_calculated_tax(ref_data: dict[str, Any]) -> None:
    ref_data["salary"] = {"amount": "492750.00", "currency": "TRY", "frequency": "MONTHLY"}
    ref_data["hypothetical_tax"] = {"method": "CALCULATED"}
    ref_data["fx"] = FX
    result = _calc(ref_data)
    components = result.hypothetical_tax.components
    assert components is not None
    assert components.gross_try == Decimal("5913000.00")
    assert result.hypothetical_tax.amount == Decimal("34212.20")
    assert result.year(1).line(LineCode.TOTAL_EMPLOYER_COST) == 179536


def test_home_scheme_lines(ref_data: dict[str, Any]) -> None:
    ref_data["assumptions"]["social_security"] = "HOME_SCHEME_AGREEMENT"
    ref_data["fx"] = FX
    result = _calc(ref_data)
    for year in result.years:
        assert year.line(LineCode.EMPLOYEE_NIC) == 0
        assert year.line(LineCode.EMPLOYER_NIC) == 0
        assert year.line(LineCode.CLASS_1A) == 0
        assert year.line(LineCode.HOME_EMPLOYER_SOCIAL_SECURITY) == 11674
        assert year.decomposition.foots
    estimated = [w for w in result.warnings if w.code == "HOME_EMPLOYER_SOCIAL_SECURITY_ESTIMATED"]
    assert [w.assignment_year for w in estimated] == [1, 2]
    assert "766956.60" in estimated[0].text
    assert "HOME_SCHEME_CERTIFICATE_REQUIRED" in result.assumption_codes()
    assert "UK_NIC_APPLIES" not in result.assumption_codes()
    # The home-scheme line appears in the totals, so the toggle cannot hide it.
    totals = {line.code: line.amount for line in result.totals.lines}
    assert totals[LineCode.HOME_EMPLOYER_SOCIAL_SECURITY] == 23348


def test_uk_nic_mode_has_no_home_line(ref_data: dict[str, Any]) -> None:
    result = _calc(ref_data)
    assert not result.year(1).has_line(LineCode.HOME_EMPLOYER_SOCIAL_SECURITY)
    assert LineCode.HOME_EMPLOYER_SOCIAL_SECURITY not in {line.code for line in result.totals.lines}


def test_owr_claim_is_noted(ref_data: dict[str, Any]) -> None:
    ref_data["assumptions"]["owr_claimed"] = True
    owr = next(a for a in _calc(ref_data).assumptions if a.code == "OWR_NOT_MODELLED")
    assert "claim was indicated" in owr.text


def test_one_year_has_no_carry_forward(ref_data: dict[str, Any]) -> None:
    ref_data["assignment"]["length_years"] = 1
    result = _calc(ref_data)
    assert "RATES_NOT_PUBLISHED_FOR_YEAR" not in _codes(result)
    assert "RATES_UNCHANGED_LATER_YEARS" not in result.assumption_codes()


def test_warnings_are_ordered_by_severity(ref_data: dict[str, Any]) -> None:
    order = {"error": 0, "warning": 1, "info": 2}
    severities = [order[w.severity] for w in _calc(ref_data).warnings]
    assert severities == sorted(severities)


def test_determinism_and_hash(ref_data: dict[str, Any]) -> None:
    first = _calc(ref_data)
    second = _calc(json.loads(json.dumps(ref_data)))
    assert first.to_json() == second.to_json()
    assert first.inputs_hash == second.inputs_hash
    assert first.model_dump_json() == second.model_dump_json()


def _walk(node: object) -> None:
    assert not isinstance(node, float), node
    if isinstance(node, dict):
        for value in node.values():
            _walk(value)
    elif isinstance(node, list | tuple):
        for value in node:
            _walk(value)


def test_no_float_anywhere(ref_data: dict[str, Any]) -> None:
    ref_data["fx"] = FX
    ref_data["hypothetical_tax"] = {"method": "CALCULATED"}
    result = _calc(ref_data)
    _walk(result.model_dump())

    def no_float(text: str) -> None:
        raise AssertionError(f"float in JSON: {text}")

    json.loads(result.model_dump_json(), parse_float=no_float)
