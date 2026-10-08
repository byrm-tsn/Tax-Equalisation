"""The reference example in detail: figures, solver agreement, trace and result shape."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

import pytest

from teq_engine import (
    BundledProvider,
    CalculationResult,
    LineCode,
    calculate,
    reference_example,
)
from teq_engine.ratesets.schemas import UkIncomeTaxData, UkNicData
from teq_engine.solver import (
    GrossUpProblem,
    bisection_gross_up,
    fixed_point_gross_up,
    solve_gross_up,
)

PENNY = Decimal("0.01")


@pytest.fixture(scope="module")
def result(provider: BundledProvider) -> CalculationResult:
    return calculate(reference_example(), provider, rates_as_of=date(2026, 10, 8))


def test_year_one_figures(result: CalculationResult) -> None:
    y1 = result.year(1)
    assert y1.line(LineCode.GROSS_CASH) == Decimal("127762")
    assert y1.line(LineCode.INCOME_TAX) == Decimal("57196")
    assert y1.line(LineCode.EMPLOYEE_NIC) == Decimal("4566")
    assert y1.line(LineCode.EMPLOYER_NIC) == Decimal("18414")
    assert y1.line(LineCode.CLASS_1A) == Decimal("4500")
    assert y1.line(LineCode.TOTAL_EMPLOYER_COST) == Decimal("188676")
    assert y1.gross_up.gross_exact == Decimal("127761.51")
    assert y1.gross_up.net_delivered == Decimal("66000.26")


def test_year_two_and_total(result: CalculationResult) -> None:
    y2 = result.year(2)
    assert y2.line(LineCode.TOTAL_EMPLOYER_COST) == Decimal("180676")
    assert y2.line(LineCode.EXEMPT_COST) == 0
    assert y2.uk_tax_year == "2027-28"
    assert y2.tr_calendar_year == 2027
    assert y2.rates_carried_forward
    assert result.totals.total_employer_cost == Decimal("369352")
    # The two-year total is the sum of the rounded year totals (unrounded: 369,351.47).
    assert result.totals.total_employer_cost == sum(
        y.line(LineCode.TOTAL_EMPLOYER_COST) for y in result.years
    )


def test_carry_forward_warnings_name_years(result: CalculationResult) -> None:
    # The override means the Turkish rate sets are not used, so only the UK line appears,
    # and it is information: the pack itself assumes year-2 rates are unchanged.
    carried = [w for w in result.warnings if w.code == "RATES_NOT_PUBLISHED_FOR_YEAR"]
    assert len(carried) == 1
    (line,) = carried
    assert line.assignment_year == 2
    assert line.severity == "info"
    assert line.params["jurisdiction"] == "UK"
    assert "2027-28" in line.text
    assert "2026-27" in line.text
    assert "Turkish" not in line.text
    assert not [w for w in result.warnings if w.severity in ("error", "warning")]


def test_segment_solve_matches_oracles_to_the_penny(
    it_rates: UkIncomeTaxData, nic_rates: UkNicData
) -> None:
    problem = GrossUpProblem(Decimal("66000"), Decimal("30000"), it_rates, nic_rates)
    exact = solve_gross_up(problem)
    oracle, iterations = fixed_point_gross_up(problem)
    bisected, _ = bisection_gross_up(problem)
    assert exact.method == "segment"
    assert exact.gross.quantize(PENNY) == oracle.quantize(PENNY) == Decimal("127761.51")
    assert bisected.quantize(PENNY) == Decimal("127761.51")
    assert abs(exact.gross - oracle) < Decimal("0.000001")
    # The pack's iteration converges in a few dozen steps (to 1e-7).
    assert 15 <= iterations <= 60
    # To the penny it takes about 21 steps.
    _, penny_steps = _fixed_point_to_penny(problem)
    assert 18 <= penny_steps <= 24


def _fixed_point_to_penny(problem: GrossUpProblem) -> tuple[Decimal, int]:
    from teq_engine.piecewise import fixed_point

    return fixed_point(problem.net, problem.net_target, tolerance=PENNY)


def test_gross_up_trace_step(result: CalculationResult) -> None:
    y1 = result.year(1)
    ref = next(line.trace_ref for line in y1.lines if line.code == LineCode.GROSS_CASH)
    step = next(s for s in result.trace if s.id == ref)
    assert step.step == "gross_up"
    assert step.assignment_year == 1
    assert "PAYE81740" in step.refs
    assert step.values["method"] == "segment"
    assert step.values["segment"] == "IT 45% + NIC 2%"
    assert step.values["marginal_rate"] == "0.47"
    assert step.values["gross_exact"] == "127761.51"
    assert step.values["gross_rounded"] == "127762"
    assert step.values["net_delivered_exact"] == "66000.26"


def test_every_line_links_to_a_trace_step(result: CalculationResult) -> None:
    ids = {s.id for s in result.trace}
    for year in result.years:
        for line in year.lines:
            assert line.trace_ref in ids


def test_marginal_cost_detail(result: CalculationResult) -> None:
    # 1 / (1 - 0.47) x 1.15 = 2.1698...
    y1 = result.year(1)
    assert y1.gross_up.marginal_rate == Decimal("0.47")
    assert y1.line(LineCode.MARGINAL_COST_PER_NET_POUND) == Decimal("2.17")
    assert y1.line(LineCode.MULTIPLE_OF_SALARY) == Decimal("2.10")


def test_both_decompositions_foot(result: CalculationResult) -> None:
    for year in result.years:
        employer_view = sum(
            year.line(code)
            for code in (
                LineCode.GROSS_CASH,
                LineCode.EMPLOYER_NIC,
                LineCode.CLASS_1A,
                LineCode.BENEFIT_COST,
                LineCode.EXEMPT_COST,
            )
        )
        recipient_view = sum(
            year.line(code)
            for code in (
                LineCode.NET_CASH,
                LineCode.INCOME_TAX,
                LineCode.EMPLOYEE_NIC,
                LineCode.EMPLOYER_NIC,
                LineCode.CLASS_1A,
                LineCode.BENEFIT_COST,
                LineCode.EXEMPT_COST,
            )
        )
        assert employer_view == recipient_view == year.line(LineCode.TOTAL_EMPLOYER_COST)
        assert year.decomposition.foots


def test_result_shape_matches_appendix_a(result: CalculationResult) -> None:
    data = json.loads(result.to_json())
    for key in (
        "schema_version",
        "engine_version",
        "status",
        "rates_as_of",
        "rate_set_ids",
        "rate_set_fingerprint",
        "period_mode",
        "inputs_hash",
        "currency",
        "rounding",
        "hypothetical_tax",
        "net_guarantee",
        "items",
        "years",
        "totals",
        "warnings",
        "assumptions",
        "trace",
        "provenance",
        "disclaimer_version",
    ):
        assert key in data, key
    assert data["schema_version"] == "1"
    assert data["engine_version"] == "0.2.0"
    assert data["status"] == "OK"
    assert data["rates_as_of"] == "2026-10-08"
    assert data["period_mode"] == "ILLUSTRATIVE_WHOLE_YEAR"
    assert data["disclaimer_version"] == "2026-10"
    assert data["rounding"] == {
        "gross_cash": "CEIL_TO_UNIT",
        "lines": "HALF_UP_TO_UNIT",
        "totals": "SUM_OF_ROUNDED_LINES",
        "ratios": "HALF_UP_TO_0.01",
    }
    assert data["hypothetical_tax"]["mode"] == "OVERRIDE"
    assert data["hypothetical_tax"]["amount"] == "30000.00"
    assert data["hypothetical_tax"]["components"] is None
    assert data["hypothetical_tax"]["fx"] is None
    assert data["net_guarantee"]["net_cash_target"] == "66000.00"
    assert data["inputs_hash"].startswith("sha256:")
    assert data["rate_set_fingerprint"].startswith("sha256:")
    items = {i["id"]: i for i in data["items"]}
    assert items["cola"]["treatment"] == "NET_CASH"
    assert items["cola"]["nic_class"] == "CLASS_1"
    assert items["housing"]["treatment"] == "TAXABLE_BIK"
    assert items["housing"]["nic_class"] == "CLASS_1A"
    assert items["relocation"]["treatment"] == "EXEMPT_CAPPED"
    assert items["relocation"]["cap"] == "8000.00"
    assert items["relocation"]["excess"] == "0.00"
    assert items["relocation"]["years"] == [1]
    assert {p["id"] for p in data["provenance"]} == set(data["rate_set_ids"])
    for entry in data["provenance"]:
        assert entry["checksum"].startswith("sha256:")
        assert entry["sources"][0].startswith("https://")
        assert entry["verified_at"] == "2026-10-08"


def test_rate_set_fingerprint_covers_ids_and_checksums(result: CalculationResult) -> None:
    from teq_engine.types import canonical_json, sha256_prefixed

    assert list(result.rate_set_ids) == sorted(result.rate_set_ids)
    # sha256 over the sorted (id, content checksum) pairs of the sets actually used.
    pairs = sorted((p.id, p.checksum) for p in result.provenance)
    assert [rs_id for rs_id, _ in pairs] == list(result.rate_set_ids)
    assert result.rate_set_fingerprint == sha256_prefixed(
        canonical_json([[rs_id, checksum] for rs_id, checksum in pairs])
    )
    assert result.rate_set_fingerprint != sha256_prefixed(canonical_json(list(result.rate_set_ids)))
    assert "UK_INCOME_TAX:2026-27:v1" in result.rate_set_ids
    assert "TR_SGK:2026:v1" in result.rate_set_ids


def test_cache_key_combines_the_four_identity_fields(result: CalculationResult) -> None:
    from teq_engine.calculator import compute_cache_key
    from teq_engine.types import canonical_json, sha256_prefixed

    assert result.cache_key is not None
    assert result.cache_key.startswith("sha256:")
    assert result.cache_key == compute_cache_key(
        inputs_hash=result.inputs_hash,
        rate_set_fingerprint=result.rate_set_fingerprint,
        engine_version=result.engine_version,
        rates_as_of=result.rates_as_of,
    )
    assert result.cache_key == sha256_prefixed(
        canonical_json(
            {
                "engine_version": "0.2.0",
                "inputs_hash": result.inputs_hash,
                "rate_set_fingerprint": result.rate_set_fingerprint,
                "rates_as_of": "2026-10-08",
            }
        )
    )


def test_result_round_trips_through_json(result: CalculationResult) -> None:
    again = CalculationResult.model_validate_json(result.model_dump_json())
    assert again == result
    assert again.to_json() == result.to_json()
