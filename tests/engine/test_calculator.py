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
    assert "£34,212.20" in override.text
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
    assert "766,956.60" in estimated[0].text
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


def _carried(result: Any, jurisdiction: str) -> list[Any]:
    return [
        w
        for w in result.warnings
        if w.code == "RATES_NOT_PUBLISHED_FOR_YEAR" and w.params["jurisdiction"] == jurisdiction
    ]


def test_turkish_carry_forward_flagged_when_hypothetical_tax_is_calculated(
    ref_data: dict[str, Any],
) -> None:
    ref_data["hypothetical_tax"] = {"method": "CALCULATED"}
    ref_data["fx"] = FX
    result = _calc(ref_data)
    (turkish,) = _carried(result, "Turkish")
    assert turkish.assignment_year == 2
    assert turkish.severity == "info"
    assert "Turkish rates for 2027" in turkish.text
    assert "the 2026 rates" in turkish.text
    assert len(_carried(result, "UK")) == 1
    assert result.year(2).rates_carried_forward


def test_turkish_carry_forward_flagged_under_the_home_scheme(ref_data: dict[str, Any]) -> None:
    ref_data["assumptions"]["social_security"] = "HOME_SCHEME_AGREEMENT"
    ref_data["fx"] = FX
    result = _calc(ref_data)
    assert [w.assignment_year for w in _carried(result, "Turkish")] == [2]


def test_override_without_fx_does_not_flag_turkish_rates(ref_data: dict[str, Any]) -> None:
    assert ref_data["hypothetical_tax"]["method"] == "OVERRIDE"
    ref_data["assignment"]["length_years"] = 3
    result = _calc(ref_data)
    assert _carried(result, "Turkish") == []
    assert [w.assignment_year for w in _carried(result, "UK")] == [2, 3]
    # Listing the unused sets in the provenance is harmless; the cache key is unchanged.
    assert "TR_SGK:2026:v1" in result.rate_set_ids


def test_override_scenario_started_in_2027_reports_only_what_it_used(
    ref_data: dict[str, Any],
) -> None:
    # 15 January 2027 is in UK tax year 2026-27 (published) but Turkish year 2027 (not).
    ref_data["assignment"]["start_date"] = "2027-01-15"
    ref_data["assignment"]["length_years"] = 1
    result = _calc(ref_data)
    assert not result.year(1).rates_carried_forward
    assert "RATES_NOT_PUBLISHED_FOR_YEAR" not in _codes(result)
    assert "RATES_UNCHANGED_LATER_YEARS" not in result.assumption_codes()
    # The same dates with a calculated hypothetical tax do use the carried Turkish sets.
    ref_data["hypothetical_tax"] = {"method": "CALCULATED"}
    ref_data["fx"] = FX
    calculated = _calc(ref_data)
    assert calculated.year(1).rates_carried_forward
    assert [w.assignment_year for w in _carried(calculated, "Turkish")] == [1]
    assert "RATES_UNCHANGED_LATER_YEARS" in calculated.assumption_codes()


def test_override_comparison_notes_carried_forward_turkish_rates(
    ref_data: dict[str, Any],
) -> None:
    ref_data["assignment"]["start_date"] = "2027-01-15"
    ref_data["fx"] = FX
    result = _calc(ref_data)
    assert _carried(result, "Turkish") == []
    override = next(a for a in result.assumptions if a.code == "HYPO_TAX_OVERRIDE")
    assert "£34,212.20 (2026 Turkish rates carried forward)." in override.text
    # A 2026 start uses the published 2026 sets for the comparison: no suffix.
    ref_data["assignment"]["start_date"] = None
    plain = next(a for a in _calc(ref_data).assumptions if a.code == "HYPO_TAX_OVERRIDE")
    assert "carried forward" not in plain.text
    assert "£34,212.20." in plain.text


def test_marginal_cost_line_label(ref_data: dict[str, Any]) -> None:
    line = next(
        line
        for line in _calc(ref_data).year(1).lines
        if line.code == LineCode.MARGINAL_COST_PER_NET_POUND
    )
    assert line.label == "Cost to the employer of £1 more net pay"


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


# --------------------------------------------------------------------------- identity


def _provider_with_edited_rate() -> Any:
    """The bundled sets with one figure changed under the same id, label and version."""
    from pathlib import Path

    import yaml

    from teq_engine import BundledProvider
    from teq_engine.ratesets.schemas import parse_rate_set

    data = Path(__file__).resolve().parents[2] / "src" / "teq_engine" / "ratesets" / "data"
    raw = yaml.safe_load((data / "gb" / "income_tax_2026_27.yaml").read_text(encoding="utf-8"))
    raw["data"]["bands"][2]["rate"] = "0.46"
    edited = parse_rate_set(raw)
    others = [rs for rs in default_provider().all() if rs.id != edited.id]
    return BundledProvider([*others, edited])


def test_fingerprint_changes_when_a_rate_changes_under_the_same_id(
    ref_data: dict[str, Any],
) -> None:
    inputs = ScenarioInput.model_validate(ref_data)
    base = calculate(inputs, default_provider(), rates_as_of=AS_OF)
    edited = calculate(inputs, _provider_with_edited_rate(), rates_as_of=AS_OF)
    assert edited.rate_set_ids == base.rate_set_ids
    assert edited.inputs_hash == base.inputs_hash
    assert edited.totals.total_employer_cost != base.totals.total_employer_cost
    assert edited.rate_set_fingerprint != base.rate_set_fingerprint
    assert edited.cache_key != base.cache_key


def test_fingerprint_uses_content_checksum_when_a_set_has_none(ref_data: dict[str, Any]) -> None:
    import dataclasses

    from teq_engine import BundledProvider
    from teq_engine.calculator import compute_rate_set_fingerprint
    from teq_engine.ratesets.schemas import content_checksum

    sets = default_provider().all()
    blank = [dataclasses.replace(rs, checksum="") for rs in sets]
    assert [content_checksum(rs) for rs in blank] == [rs.checksum for rs in sets]
    assert compute_rate_set_fingerprint(blank) == compute_rate_set_fingerprint(sets)
    inputs = ScenarioInput.model_validate(ref_data)
    assert (
        calculate(inputs, BundledProvider(blank), rates_as_of=AS_OF).rate_set_fingerprint
        == calculate(inputs, default_provider(), rates_as_of=AS_OF).rate_set_fingerprint
    )


def test_cache_key_changes_with_the_rates_date(ref_data: dict[str, Any]) -> None:
    first = _calc(ref_data)
    later = _calc(ref_data, date(2026, 10, 9))
    assert later.inputs_hash == first.inputs_hash
    assert later.rate_set_fingerprint == first.rate_set_fingerprint
    assert later.totals.total_employer_cost == first.totals.total_employer_cost
    assert later.cache_key != first.cache_key
    assert _calc(ref_data).cache_key == first.cache_key


# --------------------------------------------------------------------------- FX validation


def test_fx_dated_after_the_rates_date_is_refused(ref_data: dict[str, Any]) -> None:
    from teq_engine import EngineError, ScenarioValidationError
    from teq_engine.warnings import CATALOGUE, Code

    ref_data["fx"] = dict(FX, as_of="2026-10-09")  # one day after the rates date
    try:
        _calc(ref_data)
    except ScenarioValidationError as exc:
        error = exc
    else:  # pragma: no cover - the assertion below reports it
        raise AssertionError("an FX snapshot from the future was accepted")
    assert isinstance(error, EngineError)
    assert error.code == "FX_RATE_IN_FUTURE"
    assert CATALOGUE[Code.FX_RATE_IN_FUTURE].kind == "error"
    assert error.loc == ("fx", "as_of")
    assert error.pointer == "/fx/as_of"
    assert error.params == {"as_of": "2026-10-09", "rates_as_of": "2026-10-08"}
    assert "2026-10-09" in error.message
    # Dated on the rates date itself: accepted.
    ref_data["fx"] = dict(FX, as_of="2026-10-08")
    assert _calc(ref_data).year(1).line(LineCode.TOTAL_EMPLOYER_COST) == 188676


def _limit_errors(data: dict[str, Any]) -> list[tuple[tuple[Any, ...], str]]:
    from pydantic import ValidationError

    try:
        ScenarioInput.model_validate(data)
    except ValidationError as exc:
        return [(tuple(e["loc"]), e["msg"]) for e in exc.errors()]
    return []


def test_converted_and_annualised_amounts_respect_the_limit(ref_data: dict[str, Any]) -> None:
    import copy

    base = copy.deepcopy(ref_data)
    # A monthly salary that annualises above one billion.
    data = copy.deepcopy(base)
    data["salary"] = {"amount": "100000000.00", "currency": "GBP", "frequency": "MONTHLY"}
    errors = _limit_errors(data)
    assert errors[0][0] == ("salary", "amount")
    assert "one billion" in errors[0][1]
    # At exactly one billion a year it is accepted.
    data["salary"] = {"amount": "1000000000.00", "currency": "GBP"}
    assert _limit_errors(data) == []
    # A lira salary that converts above one billion pounds at an absurd rate.
    data = copy.deepcopy(base)
    data["salary"] = {"amount": "90000.00", "currency": "TRY"}
    data["fx"] = dict(FX, rate="0.00001")
    data["hypothetical_tax"]["override"] = "1.00"
    errors = _limit_errors(data)
    assert any(loc == ("salary", "amount") and "converts to" in msg for loc, msg in errors)
    # A pound salary that converts above one billion lira.
    data = copy.deepcopy(base)
    data["salary"] = {"amount": "20000000.00", "currency": "GBP"}
    data["fx"] = FX
    errors = _limit_errors(data)
    assert any(loc == ("salary", "amount") and "TRY" in msg for loc, msg in errors)
    # A monthly item that annualises above one billion.
    data = copy.deepcopy(base)
    data["items"][1]["frequency"] = "MONTHLY"
    data["items"][1]["amount"] = "90000000.00"
    errors = _limit_errors(data)
    assert ("items", 1, "amount") in [loc for loc, _ in errors]
    # A gross-equalised item that converts above one billion lira in the Turkish base.
    data = copy.deepcopy(base)
    data["items"].append({"id": "bonus", "kind": "BONUS", "amount": "20000000.00"})
    data["hypothetical_tax"] = {"method": "CALCULATED", "base": "ALL_EQUALISED"}
    data["salary"]["amount"] = "90000.00"
    data["fx"] = FX
    errors = _limit_errors(data)
    assert ("items", 3, "amount") in [loc for loc, _ in errors]
    # The same item is not converted when the base is salary only.
    data["hypothetical_tax"]["base"] = "SALARY_ONLY"
    assert _limit_errors(data) == []


def test_override_pins_the_fx_used_for_the_comparison(ref_data: dict[str, Any]) -> None:
    assert _calc(ref_data).hypothetical_tax.fx is None  # no snapshot, no comparison
    ref_data["fx"] = FX
    hypo = _calc(ref_data).hypothetical_tax
    assert hypo.calculated_for_comparison == Decimal("34212.20")
    assert hypo.fx is not None
    assert (hypo.fx.rate, hypo.fx.as_of.isoformat(), hypo.fx.source) == (
        Decimal("65.700000"),
        "2026-10-08",
        "test",
    )


def test_home_employer_line_is_rounded_once(ref_data: dict[str, Any]) -> None:
    ref_data["assumptions"]["social_security"] = "HOME_SCHEME_AGREEMENT"
    ref_data["fx"] = FX
    result = _calc(ref_data)
    step = next(s for s in result.trace if s.step == "employer_charges" and s.assignment_year == 1)
    # 766,956.60 / 65.7 = 11,673.616...: one rounding to the pound.
    exact = Decimal(step.values["home_employer_try"]) / Decimal("65.7")
    expected = exact.quantize(Decimal("1"), rounding="ROUND_HALF_UP")
    assert result.year(1).line(LineCode.HOME_EMPLOYER_SOCIAL_SECURITY) == expected == 11674
