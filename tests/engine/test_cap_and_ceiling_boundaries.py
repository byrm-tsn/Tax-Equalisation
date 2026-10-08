"""Boundary tests through ``calculate`` (plan section 12): the relocation cap and the
Turkish social security ceiling, each at the value and one penny (kuruş) either side,
read from the rate sets; and the per-move cap across years 1 and 2."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from teq_engine import BundledProvider, CalculationResult, LineCode, ScenarioInput, calculate
from teq_engine.ratesets.schemas import TrSgkData, UkBenefitRulesData
from teq_engine.treatments import Treatment

AS_OF = date(2026, 10, 8)
PENNY = Decimal("0.01")
FX = {"rate": "65.7", "as_of": "2026-10-08"}


def _calc(data: dict[str, Any], provider: BundledProvider) -> CalculationResult:
    return calculate(ScenarioInput.model_validate(data), provider, rates_as_of=AS_OF)


def _cap(provider: BundledProvider) -> Decimal:
    rs = provider.get("UK", "UK_BENEFIT_RULES", date(2026, 4, 6))
    assert rs is not None
    cap: Decimal = rs.data_as(UkBenefitRulesData).relocation.exemption_cap
    return cap


def _ceiling(provider: BundledProvider) -> Decimal:
    rs = provider.get("TR", "TR_SGK", date(2026, 1, 1))
    assert rs is not None
    ceiling: Decimal = rs.data_as(TrSgkData).ceiling_monthly
    return ceiling


# --------------------------------------------------------------------------- relocation cap


@pytest.mark.parametrize("offset", [Decimal("-0.01"), Decimal("0"), Decimal("0.01")])
def test_relocation_at_the_cap(
    provider: BundledProvider, ref_data: dict[str, Any], offset: Decimal
) -> None:
    cap = _cap(provider)
    assert cap == Decimal("8000")
    amount = (cap + offset).quantize(PENNY)
    ref_data["items"][2]["amount"] = str(amount)
    result = _calc(ref_data, provider)
    relocation = next(i for i in result.items if i.id == "relocation")
    excess = max(Decimal("0"), amount - cap)
    assert relocation.exempt == min(amount, cap)
    assert relocation.excess == excess
    y1 = result.year(1)
    assert y1.gross_up.taxable_benefits == Decimal("30000") + excess
    exempt_line, taxable_line = relocation.allocations[0].lines
    assert exempt_line.treatment is Treatment.EXEMPT_CAPPED
    assert exempt_line.amount == min(amount, cap)
    assert taxable_line.treatment is Treatment.TAXABLE_BIK
    assert taxable_line.amount == excess
    flagged = "RELOCATION_EXCESS_TAXABLE" in result.warning_codes()
    assert flagged is (offset > 0)
    # A penny either way changes nothing that is rounded to the pound.
    assert y1.line(LineCode.EXEMPT_COST) == 8000
    assert y1.line(LineCode.BENEFIT_COST) == 30000
    assert y1.line(LineCode.CLASS_1A) == 4500
    assert y1.decomposition.foots


def test_per_move_cap_spans_years_one_and_two(
    provider: BundledProvider, ref_data: dict[str, Any]
) -> None:
    cap = _cap(provider)
    # The same 8,000 paid in each of years 1 and 2: the cap is used up in year 1, so
    # all of year 2's payment is taxable (it is not reset for the new tax year).
    ref_data["items"][2]["amount"] = str(cap)
    ref_data["items"][2]["years"] = [1, 2]
    result = _calc(ref_data, provider)
    relocation = next(i for i in result.items if i.id == "relocation")
    y1, y2 = relocation.allocations
    assert (y1.exempt, y1.taxable_benefit) == (cap, Decimal("0"))
    assert (y2.exempt, y2.taxable_benefit) == (Decimal("0"), cap)
    assert [line.amount for line in y2.lines] == [Decimal("0"), cap]
    assert relocation.exempt == cap
    assert relocation.excess == cap
    year_two = result.year(2)
    assert year_two.line(LineCode.EXEMPT_COST) == 0
    assert year_two.line(LineCode.BENEFIT_COST) == 38000
    assert year_two.line(LineCode.CLASS_1A) == 5700
    excess = [w for w in result.warnings if w.code == "RELOCATION_EXCESS_TAXABLE"]
    assert [(w.assignment_year, w.params["remaining"]) for w in excess] == [(2, "0.00")]
    step = next(s for s in result.trace if s.step == "benefits" and s.assignment_year == 2)
    assert step.values["relocation_exempt"] == "0.00"
    assert step.values["relocation_excess_taxable"] == "8000.00"


def test_per_move_cap_is_shared_by_two_relocation_items(
    provider: BundledProvider, ref_data: dict[str, Any]
) -> None:
    ref_data["items"][2]["amount"] = "5000.00"
    ref_data["items"].append(
        {
            "id": "removal",
            "kind": "RELOCATION",
            "amount": "5000.00",
            "frequency": "ONE_OFF",
            "years": [2],
        }
    )
    result = _calc(ref_data, provider)
    removal = next(i for i in result.items if i.id == "removal")
    assert (removal.exempt, removal.excess) == (Decimal("3000.00"), Decimal("2000.00"))
    assert result.year(1).line(LineCode.EXEMPT_COST) == 5000
    assert result.year(2).line(LineCode.EXEMPT_COST) == 3000
    assert result.year(2).line(LineCode.BENEFIT_COST) == 32000


# --------------------------------------------------------------------------- SGK ceiling


@pytest.mark.parametrize("offset", [Decimal("-0.01"), Decimal("0"), Decimal("0.01")])
def test_turkish_social_security_ceiling(
    provider: BundledProvider, ref_data: dict[str, Any], offset: Decimal
) -> None:
    ceiling = _ceiling(provider)
    assert ceiling == Decimal("297270")
    monthly = (ceiling + offset).quantize(PENNY)
    ref_data["salary"] = {"amount": str(monthly), "currency": "TRY", "frequency": "MONTHLY"}
    ref_data["hypothetical_tax"] = {"method": "CALCULATED"}
    ref_data["fx"] = FX
    result = _calc(ref_data, provider)
    components = result.hypothetical_tax.components
    assert components is not None
    assert components.monthly_gross_try == monthly
    assert components.sgk_ceiling_applied is (offset > 0)
    base = min(monthly, ceiling)
    assert components.sgk_base_monthly_try == base
    # Employee SGK: 15% of the capped monthly gross, times 12, to the kuruş.
    expected = (base * Decimal("0.15") * 12).quantize(PENNY)
    assert components.sgk_try == expected
    assert (
        components.sgk_try
        == {
            Decimal("-0.01"): Decimal("535085.98"),
            Decimal("0"): Decimal("535086.00"),
            Decimal("0.01"): Decimal("535086.00"),
        }[offset]
    )


@pytest.mark.parametrize("offset", [Decimal("-0.01"), Decimal("0"), Decimal("0.01")])
def test_home_scheme_employer_contribution_at_the_ceiling(
    provider: BundledProvider, ref_data: dict[str, Any], offset: Decimal
) -> None:
    ceiling = _ceiling(provider)
    monthly = (ceiling + offset).quantize(PENNY)
    ref_data["salary"] = {"amount": str(monthly), "currency": "TRY", "frequency": "MONTHLY"}
    ref_data["hypothetical_tax"]["override"] = "10000.00"
    ref_data["assumptions"]["social_security"] = "HOME_SCHEME_AGREEMENT"
    ref_data["fx"] = FX
    result = _calc(ref_data, provider)
    step = next(s for s in result.trace if s.step == "employer_charges" and s.assignment_year == 1)
    assert step.values["home_employer_base_monthly_try"] == str(min(monthly, ceiling))
    # Employer rate 21.5% after the incentive, on the capped base, times 12.
    expected_try = (min(monthly, ceiling) * Decimal("0.215") * 12).quantize(PENNY)
    assert step.values["home_employer_try"] == str(expected_try)
    assert result.year(1).line(LineCode.HOME_EMPLOYER_SOCIAL_SECURITY) == 11674
