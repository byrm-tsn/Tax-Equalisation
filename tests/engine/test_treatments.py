"""Item treatments, the per-move relocation cap and year-restricted items."""

from __future__ import annotations

import copy
from datetime import date
from decimal import Decimal
from typing import Any

from teq_engine import LineCode, ScenarioInput, calculate, default_provider
from teq_engine.jurisdictions.gb.benefits import RelocationPayment, allocate_relocation
from teq_engine.ratesets.schemas import UkBenefitRulesData
from teq_engine.treatments import DEFAULT_TREATMENT, DISPLAY_LABEL, ItemKind, Treatment

AS_OF = date(2026, 10, 8)


def _calc(data: dict[str, Any]):  # type: ignore[no-untyped-def]
    return calculate(ScenarioInput.model_validate(data), default_provider(), rates_as_of=AS_OF)


def test_every_kind_has_a_default_and_every_treatment_a_label() -> None:
    assert set(DEFAULT_TREATMENT) == set(ItemKind)
    assert set(DISPLAY_LABEL) == set(Treatment)
    assert set(DISPLAY_LABEL.values()) == {"Taxable cash", "Taxable benefit in kind", "Exempt"}


def test_cap_is_per_move_across_years(benefit_rules: UkBenefitRulesData) -> None:
    rules = benefit_rules.relocation
    allocations = allocate_relocation(
        [
            RelocationPayment(2, "b", Decimal("6000")),
            RelocationPayment(1, "a", Decimal("6000")),
        ],
        rules,
    )
    assert [(a.assignment_year, a.exempt, a.taxable) for a in allocations] == [
        (1, Decimal("6000"), Decimal("0")),
        (2, Decimal("2000"), Decimal("4000")),
    ]


def test_relocation_split_across_years(ref_data: dict[str, Any]) -> None:
    ref_data["items"][2]["amount"] = "6000.00"
    ref_data["items"][2]["years"] = [1, 2]
    result = _calc(ref_data)
    y1, y2 = result.year(1), result.year(2)
    assert y1.line(LineCode.EXEMPT_COST) == 6000
    assert y2.line(LineCode.EXEMPT_COST) == 2000
    assert y2.line(LineCode.BENEFIT_COST) == 34000
    assert y2.line(LineCode.CLASS_1A) == 5100  # 15% x (30,000 + 4,000)
    relocation = next(i for i in result.items if i.id == "relocation")
    assert relocation.exempt == Decimal("8000.00")
    assert relocation.excess == Decimal("4000.00")
    excess = [w for w in result.warnings if w.code == "RELOCATION_EXCESS_TAXABLE"]
    assert [w.assignment_year for w in excess] == [2]
    assert "£2000.00 of exemption left" in excess[0].text


def test_relocation_outside_window(ref_data: dict[str, Any]) -> None:
    ref_data["assignment"]["length_years"] = 3
    ref_data["items"][2]["years"] = [3]
    result = _calc(ref_data)
    y3 = result.year(3)
    assert y3.line(LineCode.EXEMPT_COST) == 0
    assert y3.line(LineCode.BENEFIT_COST) == 38000
    assert y3.line(LineCode.CLASS_1A) == 5700
    codes = [(w.code, w.assignment_year) for w in result.warnings]
    assert ("RELOCATION_OUTSIDE_WINDOW", 3) in codes
    assert ("RELOCATION_EXCESS_TAXABLE", 3) not in codes


def test_year_restricted_items_leave_other_years_unchanged(ref_data: dict[str, Any]) -> None:
    with_relocation = _calc(ref_data)
    without = copy.deepcopy(ref_data)
    without["items"] = without["items"][:2]
    without_result = _calc(without)
    assert with_relocation.year(2).lines == without_result.year(2).lines
    assert with_relocation.year(1).line(LineCode.GROSS_CASH) == without_result.year(1).line(
        LineCode.GROSS_CASH
    )


def test_benefits_only_in_later_years(ref_data: dict[str, Any]) -> None:
    ref_data["items"][1]["years"] = [2]
    result = _calc(ref_data)
    assert result.year(1).line(LineCode.BENEFIT_COST) == 0
    assert result.year(1).line(LineCode.CLASS_1A) == 0
    assert result.year(2).line(LineCode.CLASS_1A) == 4500
    assert result.year(1).line(LineCode.GROSS_CASH) < result.year(2).line(LineCode.GROSS_CASH)


def test_employee_contribution_reduces_cash_equivalent(ref_data: dict[str, Any]) -> None:
    ref_data["items"][1]["employee_contribution"] = "6000.00"
    result = _calc(ref_data)
    y1 = result.year(1)
    assert y1.line(LineCode.BENEFIT_COST) == 24000
    assert y1.line(LineCode.CLASS_1A) == 3600
    assert y1.line(LineCode.TAXABLE_PAY) == y1.line(LineCode.GROSS_CASH) + 24000


def test_cola_paid_gross_versus_net(ref_data: dict[str, Any]) -> None:
    net = _calc(ref_data)
    ref_data["items"][0]["kind"] = "BONUS"  # gross-equalised: joins the guarantee gross
    gross = _calc(ref_data)
    assert gross.net_guarantee.equalised_items == Decimal("6000.00")
    assert gross.year(1).line(LineCode.GROSS_CASH) == net.year(1).line(LineCode.GROSS_CASH)
    # With base ALL_EQUALISED and an override the hypothetical tax is unchanged; with a
    # calculated tax the bonus would raise it.
    ref_data["hypothetical_tax"] = {"method": "CALCULATED", "base": "ALL_EQUALISED"}
    ref_data["fx"] = {"rate": "65.7", "as_of": "2026-10-08"}
    all_eq = _calc(ref_data)
    ref_data["hypothetical_tax"]["base"] = "SALARY_ONLY"
    salary_only = _calc(ref_data)
    assert all_eq.hypothetical_tax.amount > salary_only.hypothetical_tax.amount


def test_exempt_and_employer_only_items_cost_only(ref_data: dict[str, Any]) -> None:
    base = _calc(ref_data)
    ref_data["items"].append(
        {"id": "pension", "kind": "PENSION_EMPLOYER", "amount": "5000.00", "currency": "GBP"}
    )
    ref_data["items"].append(
        {
            "id": "visa",
            "kind": "OTHER",
            "amount": "1000.00",
            "currency": "GBP",
            "treatment": "EXEMPT",
        }
    )
    result = _calc(ref_data)
    assert result.year(1).line(LineCode.GROSS_CASH) == base.year(1).line(LineCode.GROSS_CASH)
    assert result.year(1).line(LineCode.EXEMPT_COST) == 14000
    assert result.year(2).line(LineCode.EXEMPT_COST) == 6000
    visa = next(i for i in result.items if i.id == "visa")
    assert visa.treatment_overridden
    assert visa.display_label == "Exempt"


def test_monthly_item_is_annualised(ref_data: dict[str, Any]) -> None:
    ref_data["items"][1]["frequency"] = "MONTHLY"
    ref_data["items"][1]["amount"] = "2500.00"
    result = _calc(ref_data)
    assert result.year(1).line(LineCode.BENEFIT_COST) == 30000
    assert result.year(1).line(LineCode.TOTAL_EMPLOYER_COST) == 188676
