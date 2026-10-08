"""Bundled rate sets: loading, validation, checksums and carry-forward resolution."""

from __future__ import annotations

import copy
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import yaml

from teq_engine import BundledProvider, RateSetError, RatesUnavailableError
from teq_engine.ratesets.provider import load_rate_sets_from, resolve
from teq_engine.ratesets.schemas import UkIncomeTaxData, parse_rate_set

DATA = Path(__file__).resolve().parents[2] / "src" / "teq_engine" / "ratesets" / "data"


def _raw(name: str) -> dict[str, Any]:
    with (DATA / name).open(encoding="utf-8") as fh:
        data: dict[str, Any] = yaml.safe_load(fh)
    return data


def test_all_bundled_sets_load(provider: BundledProvider) -> None:
    ids = {(rs.jurisdiction, rs.id) for rs in provider.all()}
    assert ids == {
        ("UK-ENG", "UK_INCOME_TAX:2026-27:v1"),
        ("UK", "UK_NIC:2026-27:v1"),
        ("UK", "UK_BENEFIT_RULES:2026-27:v1"),
        ("TR", "TR_INCOME_TAX:2026:v1"),
        ("TR", "TR_SGK:2026:v1"),
        ("TR", "TR_STAMP:2026:v1"),
    }
    for rs in provider.all():
        assert rs.checksum.startswith("sha256:")
        assert rs.verified_at == date(2026, 10, 8)
        assert all(s.url.startswith("https://") for s in rs.sources)


def test_bundled_figures(provider: BundledProvider) -> None:
    rs = provider.get("UK-ENG", "UK_INCOME_TAX", date(2026, 10, 8))
    assert rs is not None
    data = rs.data_as(UkIncomeTaxData)
    assert data.personal_allowance == Decimal("12570")
    assert data.taper_end == Decimal("125140")
    assert [b.rate for b in data.bands] == [Decimal("0.20"), Decimal("0.40"), Decimal("0.45")]


def test_yaml_amounts_are_strings() -> None:
    def walk(node: object) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key not in {"version", "schema_version", "window_tax_years"}:
                    walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)
        else:
            assert not isinstance(node, (int, float)) or isinstance(node, bool), node

    for path in DATA.glob("*/*.yaml"):
        walk(yaml.safe_load(path.read_text(encoding="utf-8")))


def test_checksum_is_stable_and_verified() -> None:
    raw = _raw("gb/income_tax_2026_27.yaml")
    first = parse_rate_set(raw)
    assert parse_rate_set(copy.deepcopy(raw)).checksum == first.checksum
    raw["checksum"] = first.checksum
    assert parse_rate_set(raw).checksum == first.checksum
    raw["checksum"] = "sha256:" + "0" * 64
    with pytest.raises(RateSetError, match="checksum mismatch"):
        parse_rate_set(raw)


def test_checksum_changes_with_data() -> None:
    raw = _raw("gb/income_tax_2026_27.yaml")
    before = parse_rate_set(raw).checksum
    raw["data"]["personal_allowance"] = "12571"
    assert parse_rate_set(raw).checksum != before


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda r: r["data"].pop("personal_allowance"), "personal_allowance"),
        (lambda r: r["data"].__setitem__("personal_allowance", 12570), "decimal strings"),
        (lambda r: r["data"].__setitem__("taper_rate", 0.5), "decimal strings"),
        (
            lambda r: r["data"]["bands"].__setitem__(2, {"upto": None, "rate": "1.00"}),
            "less than 1",
        ),
        (lambda r: r["data"]["bands"].__setitem__(1, {"upto": "125140", "rate": "0.70"}), "100%"),
        (
            lambda r: r["data"]["bands"].__setitem__(2, {"upto": "200000", "rate": "0.45"}),
            "upto: null",
        ),
        (lambda r: r["data"].__setitem__("unknown", "1"), "unknown"),
        (lambda r: r.__setitem__("schema_version", 2), "schema_version"),
        (lambda r: r.__setitem__("effective_to", "2026-01-01"), "effective_to"),
        (lambda r: r.__setitem__("sources", []), "sources"),
    ],
)
def test_invalid_rate_sets_fail_at_load(mutate: Any, message: str) -> None:
    raw = _raw("gb/income_tax_2026_27.yaml")
    mutate(raw)
    with pytest.raises(RateSetError, match=message):
        parse_rate_set(raw)


def test_sgk_ceiling_must_match_multiple() -> None:
    raw = _raw("tr/sgk_2026.yaml")
    raw["data"]["ceiling_monthly"] = "297271"
    with pytest.raises(RateSetError, match="multiple"):
        parse_rate_set(raw)


def test_overlapping_ranges_refused(provider: BundledProvider) -> None:
    sets = list(provider.all())
    raw = _raw("gb/nic_2026_27.yaml")
    raw["version"] = 2
    raw["effective_from"] = "2026-10-01"
    raw["effective_to"] = None
    with pytest.raises(RateSetError, match="overlapping"):
        BundledProvider([*sets, parse_rate_set(raw)])


def test_duplicate_ids_refused(provider: BundledProvider) -> None:
    sets = list(provider.all())
    with pytest.raises(RateSetError, match="duplicate"):
        BundledProvider([*sets, sets[0]])


def test_provider_from_directory_matches_bundled(provider: BundledProvider) -> None:
    loaded = load_rate_sets_from(DATA)
    assert {rs.id for rs in loaded} == {rs.id for rs in provider.all()}


def test_resolve_carries_forward_and_refuses_backwards(provider: BundledProvider) -> None:
    found = resolve(provider, "UK", "UK_NIC", date(2026, 4, 6))
    assert not found.carried_forward
    assert found.rate_set.id == "UK_NIC:2026-27:v1"
    later = resolve(provider, "UK", "UK_NIC", date(2027, 4, 6))
    assert later.carried_forward
    assert later.rate_set.id == "UK_NIC:2026-27:v1"
    assert provider.get("UK", "UK_NIC", date(2027, 4, 5)) is not None
    assert provider.get("UK", "UK_NIC", date(2027, 4, 6)) is None
    with pytest.raises(RatesUnavailableError) as info:
        resolve(provider, "UK", "UK_NIC", date(2026, 4, 5))
    assert info.value.code == "RATES_UNAVAILABLE"
    with pytest.raises(RatesUnavailableError):
        resolve(provider, "UK-SCT", "UK_INCOME_TAX", date(2026, 4, 6))


def test_effective_range_is_inclusive(provider: BundledProvider) -> None:
    assert provider.get("TR", "TR_SGK", date(2026, 12, 31)) is not None
    assert provider.get("TR", "TR_SGK", date(2027, 1, 1)) is None


# --------------------------------------------------------------------------- gaps between sets


def _nic_2028_29() -> Any:
    """A synthetic later NIC set (2028-29), leaving 2027-28 unpublished: a gap."""
    raw = _raw("gb/nic_2026_27.yaml")
    raw["label"] = "2028-29"
    raw["effective_from"] = "2028-04-06"
    raw["effective_to"] = "2029-04-05"
    raw["data"]["employee"]["main_rate"] = "0.07"
    return parse_rate_set(raw)


class _GetAndLatestOnly:
    """A provider with only the two protocol methods (no latest_on_or_before)."""

    def __init__(self, inner: BundledProvider) -> None:
        self._inner = inner

    def get(self, jurisdiction: str, category: str, as_of: date) -> Any:
        return self._inner.get(jurisdiction, category, as_of)

    def latest(self, jurisdiction: str, category: str) -> Any:
        return self._inner.latest(jurisdiction, category)


def test_gap_between_sets_carries_the_earlier_set_forward(provider: BundledProvider) -> None:
    from teq_engine.ratesets.provider import SupportsLatestOnOrBefore

    bundled = BundledProvider([*provider.all(), _nic_2028_29()])
    minimal = _GetAndLatestOnly(bundled)
    assert isinstance(bundled, SupportsLatestOnOrBefore)
    assert not isinstance(minimal, SupportsLatestOnOrBefore)
    for source in (bundled, minimal):
        in_gap = resolve(source, "UK", "UK_NIC", date(2027, 4, 6))
        assert in_gap.carried_forward
        assert in_gap.rate_set.id == "UK_NIC:2026-27:v1"
        late_in_gap = resolve(source, "UK", "UK_NIC", date(2028, 4, 5))
        assert late_in_gap.rate_set.id == "UK_NIC:2026-27:v1"
        later = resolve(source, "UK", "UK_NIC", date(2028, 4, 6))
        assert not later.carried_forward
        assert later.rate_set.id == "UK_NIC:2028-29:v1"
        after_all = resolve(source, "UK", "UK_NIC", date(2030, 1, 1))
        assert after_all.carried_forward
        assert after_all.rate_set.id == "UK_NIC:2028-29:v1"
        # Before every set: still refused, never applied backwards.
        with pytest.raises(RatesUnavailableError):
            resolve(source, "UK", "UK_NIC", date(2026, 4, 5))


def test_calculation_across_a_gap(provider: BundledProvider, ref_data: dict[str, Any]) -> None:
    from teq_engine import ScenarioInput, calculate

    gapped = BundledProvider([*provider.all(), _nic_2028_29()])
    ref_data["assignment"]["length_years"] = 3
    result = calculate(
        ScenarioInput.model_validate(ref_data), gapped, rates_as_of=date(2026, 10, 8)
    )
    y2, y3 = result.year(2), result.year(3)
    assert "UK_NIC:2026-27:v1" in y2.rate_set_ids
    assert "UK_NIC:2028-29:v1" in y3.rate_set_ids
    assert y2.line("TOTAL_EMPLOYER_COST") == 180676  # 2026-27 rules carried into the gap
    assert y3.line("EMPLOYEE_NIC") != y2.line("EMPLOYEE_NIC")  # the 7% main rate applies
    carried = [
        w
        for w in result.warnings
        if w.code == "RATES_NOT_PUBLISHED_FOR_YEAR" and w.params.get("jurisdiction") == "UK"
    ]
    assert {w.assignment_year for w in carried} >= {2}
    assert "UK_NIC:2028-29:v1" in result.rate_set_ids
