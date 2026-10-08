"""Item treatments, the per-move relocation cap and year-restricted items."""

from __future__ import annotations

import copy
from datetime import date
from decimal import Decimal
from typing import Any

from teq_engine import LineCode, ScenarioInput, calculate, default_provider
from teq_engine.jurisdictions.gb.benefits import RelocationPayment, allocate_relocation
from teq_engine.ratesets.schemas import UkBenefitRulesData
from teq_engine.treatments import DEFAULT_TREATMENT, DISPLAY_LABEL, ItemKind, NicClass, Treatment

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
    assert "£2,000 of exemption left" in excess[0].text


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
    # With a calculated tax and base ALL_EQUALISED the bonus raises the hypothetical tax
    # (see the share tests below for the override case).
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


# --------------------------------------------------------------------------- hypothetical share

FX = {"rate": "65.7", "as_of": "2026-10-08"}
BONUS = {"id": "bonus", "kind": "BONUS", "label": "Bonus", "amount": "10000.00", "years": [1]}


def _with_bonus(ref_data: dict[str, Any], hypo: dict[str, Any]) -> Any:
    ref_data["items"].append(dict(BONUS))
    ref_data["hypothetical_tax"] = hypo
    ref_data["fx"] = FX
    return _calc(ref_data)


def _bonus_share(result: Any, year: int) -> Decimal | None:
    bonus = next(i for i in result.items if i.id == "bonus")
    allocation = next(a for a in bonus.allocations if a.assignment_year == year)
    share: Decimal | None = allocation.hypothetical_tax_share
    return share


def test_override_all_equalised_charges_the_override_rate_on_the_item(
    ref_data: dict[str, Any],
) -> None:
    result = _with_bonus(
        ref_data, {"method": "OVERRIDE", "override": "30000.00", "base": "ALL_EQUALISED"}
    )
    y1 = result.year(1).net_guarantee
    # 10,000 x 30,000 / 90,000 = 3,333.33
    assert y1.hypothetical_tax_on_salary == Decimal("30000.00")
    assert y1.hypothetical_tax_on_equalised_items == Decimal("3333.33")
    assert y1.hypothetical_tax == Decimal("33333.33")
    assert y1.net_salary == Decimal("66666.67")
    assert y1.net_cash_target == Decimal("72666.67")
    assert _bonus_share(result, 1) == Decimal("3333.33")
    # The bonus is paid in year 1 only; year 2 is the reference year.
    y2 = result.year(2).net_guarantee
    assert y2.hypothetical_tax == Decimal("30000.00")
    assert y2.hypothetical_tax_on_equalised_items == Decimal("0.00")
    assert result.year(2).line(LineCode.TOTAL_EMPLOYER_COST) == 180676
    assert result.hypothetical_tax.amount == Decimal("30000.00")  # the supplied figure
    assert "EQUALISED_ITEM_NO_HYPO_SHARE" not in result.warning_codes()
    step = next(s for s in result.trace if s.step == "net_guarantee" and s.assignment_year == 1)
    assert step.values["hypothetical_tax_on_equalised_items"] == "3333.33"
    assert step.values["hypothetical_share_basis"].startswith("override x item / salary")


def test_override_salary_only_charges_no_share_and_warns(ref_data: dict[str, Any]) -> None:
    result = _with_bonus(
        ref_data, {"method": "OVERRIDE", "override": "30000.00", "base": "SALARY_ONLY"}
    )
    y1 = result.year(1).net_guarantee
    assert y1.hypothetical_tax == Decimal("30000.00")
    assert y1.hypothetical_tax_on_equalised_items == Decimal("0.00")
    assert y1.net_cash_target == Decimal("76000.00")
    assert _bonus_share(result, 1) == Decimal("0.00")
    warnings = [w for w in result.warnings if w.code == "EQUALISED_ITEM_NO_HYPO_SHARE"]
    assert len(warnings) == 1
    assert warnings[0].severity == "warning"
    assert "Bonus" in warnings[0].text
    assert "joins the net guarantee in full" in warnings[0].text
    assert "salary only" in warnings[0].text
    assert "all equalised items" in warnings[0].question


def test_calculated_all_equalised_share_is_the_increase_in_turkish_tax(
    ref_data: dict[str, Any],
) -> None:
    from teq_engine.jurisdictions.tr.hypo import turkish_hypothetical_tax
    from teq_engine.ratesets.schemas import TrIncomeTaxData, TrSgkData, TrStampData
    from teq_engine.types import FxSnapshot, HypoTaxBase

    result = _with_bonus(ref_data, {"method": "CALCULATED", "base": "ALL_EQUALISED"})
    provider = default_provider()

    def module(gross_gbp: str) -> Decimal:
        fx = FxSnapshot.model_validate(FX)
        tr = date(2026, 1, 1)
        it = provider.get("TR", "TR_INCOME_TAX", tr)
        sgk = provider.get("TR", "TR_SGK", tr)
        stamp = provider.get("TR", "TR_STAMP", tr)
        assert it is not None
        assert sgk is not None
        assert stamp is not None
        amount: Decimal = turkish_hypothetical_tax(
            gross_try=Decimal(gross_gbp) * fx.rate,
            fx=fx,
            income_tax_rates=it.data_as(TrIncomeTaxData),
            sgk_rates=sgk.data_as(TrSgkData),
            stamp_rates=stamp.data_as(TrStampData),
            includes_social_security=True,
            base=HypoTaxBase.ALL_EQUALISED,
            tax_year=2026,
        ).amount
        return amount

    y1 = result.year(1).net_guarantee
    assert y1.hypothetical_tax_on_salary == module("90000") == Decimal("34212.20")
    assert y1.hypothetical_tax == module("100000")
    assert y1.hypothetical_tax_on_equalised_items == module("100000") - module("90000")
    assert y1.hypothetical_tax_on_equalised_items == Decimal("4075.90")
    assert y1.net_salary == Decimal("100000.00") - y1.hypothetical_tax
    assert _bonus_share(result, 1) == Decimal("4075.90")
    assert result.year(2).net_guarantee.hypothetical_tax_on_equalised_items == Decimal("0.00")
    assert "EQUALISED_ITEM_NO_HYPO_SHARE" not in result.warning_codes()


def test_calculated_salary_only_charges_no_share_and_warns(ref_data: dict[str, Any]) -> None:
    result = _with_bonus(ref_data, {"method": "CALCULATED", "base": "SALARY_ONLY"})
    y1 = result.year(1).net_guarantee
    assert y1.hypothetical_tax == Decimal("34212.20")
    assert y1.hypothetical_tax_on_salary == Decimal("34212.20")
    assert y1.hypothetical_tax_on_equalised_items == Decimal("0.00")
    assert y1.net_salary == Decimal("100000.00") - Decimal("34212.20")
    assert result.warning_codes().count("EQUALISED_ITEM_NO_HYPO_SHARE") == 1


def test_calculated_share_spreads_over_several_items(ref_data: dict[str, Any]) -> None:
    ref_data["items"].append(
        {
            "id": "retention",
            "kind": "OTHER",
            "amount": "5000.00",
            "years": [1],
            "treatment": "GROSS_EQUALISED",
        }
    )
    result = _with_bonus(ref_data, {"method": "CALCULATED", "base": "ALL_EQUALISED"})
    y1 = result.year(1).net_guarantee
    shares = [
        a.hypothetical_tax_share
        for item in result.items
        for a in item.allocations
        if a.assignment_year == 1 and a.hypothetical_tax_share is not None
    ]
    assert len(shares) == 2
    assert sum(shares) == y1.hypothetical_tax_on_equalised_items
    assert y1.hypothetical_tax == y1.hypothetical_tax_on_salary + sum(shares)
    retention, bonus = shares
    assert bonus > retention > 0


def test_reference_has_no_share_fields_set_to_anything_but_zero(
    ref_data: dict[str, Any],
) -> None:
    result = _calc(ref_data)
    for year in result.years:
        assert year.net_guarantee.hypothetical_tax_on_salary == Decimal("30000.00")
        assert year.net_guarantee.hypothetical_tax_on_equalised_items == Decimal("0.00")
    for item in result.items:
        assert all(a.hypothetical_tax_share is None for a in item.allocations)
    assert "EQUALISED_ITEM_NO_HYPO_SHARE" not in result.warning_codes()


# --------------------------------------------------------------------------- relocation split


def test_relocation_over_the_cap_is_traced_and_allocated_as_two_lines(
    ref_data: dict[str, Any],
) -> None:
    ref_data["items"][2]["amount"] = "10000.00"
    result = _calc(ref_data)
    assert result.year(1).line(LineCode.TOTAL_EMPLOYER_COST) == 192929

    step = next(s for s in result.trace if s.step == "benefits" and s.assignment_year == 1)
    assert step.values["relocation_exempt"] == "8000.00"
    assert step.values["relocation_excess_taxable"] == "2000.00"
    assert step.values["exempt_cost"] == "8000.00"
    assert step.values["taxable_benefits"] == "32000.00"  # housing 30,000 + excess 2,000
    year_two = next(s for s in result.trace if s.step == "benefits" and s.assignment_year == 2)
    assert "relocation_exempt" not in year_two.values  # nothing paid in year 2

    relocation = next(i for i in result.items if i.id == "relocation")
    (allocation,) = relocation.allocations
    exempt, taxable = allocation.lines
    assert (exempt.treatment, exempt.amount, exempt.nic_class) == (
        Treatment.EXEMPT_CAPPED,
        Decimal("10000.00") - Decimal("2000.00"),
        None,
    )
    assert exempt.display_label == "Exempt"
    assert (taxable.treatment, taxable.amount, taxable.nic_class) == (
        Treatment.TAXABLE_BIK,
        Decimal("2000.00"),
        NicClass.CLASS_1A,
    )
    assert taxable.display_label == "Taxable benefit in kind"
    assert exempt.amount + taxable.amount == allocation.amount
    # Items other than capped relocation carry no split.
    housing = next(i for i in result.items if i.id == "housing")
    assert all(a.lines == () for a in housing.allocations)


def test_relocation_within_the_cap_shows_a_zero_taxable_line(ref_data: dict[str, Any]) -> None:
    result = _calc(ref_data)
    step = next(s for s in result.trace if s.step == "benefits" and s.assignment_year == 1)
    assert step.values["relocation_exempt"] == "8000.00"
    assert step.values["relocation_excess_taxable"] == "0.00"
    relocation = next(i for i in result.items if i.id == "relocation")
    assert [line.amount for line in relocation.allocations[0].lines] == [
        Decimal("8000.00"),
        Decimal("0"),
    ]
