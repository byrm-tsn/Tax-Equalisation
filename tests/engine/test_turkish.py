"""The Turkish hypothetical tax module (2026 rules)."""

from __future__ import annotations

from decimal import Decimal

from teq_engine.jurisdictions.tr.hypo import turkish_hypothetical_tax
from teq_engine.jurisdictions.tr.income_tax import (
    minimum_wage_exemption,
    minimum_wage_tax_base,
    wage_income_tax,
)
from teq_engine.jurisdictions.tr.social_security import (
    employee_contribution,
    employer_contribution,
)
from teq_engine.jurisdictions.tr.stamp_tax import stamp_tax
from teq_engine.ratesets.schemas import TrIncomeTaxData, TrSgkData, TrStampData
from teq_engine.types import FxSnapshot, HypoTaxBase

FX = FxSnapshot.model_validate({"rate": "65.7", "as_of": "2026-10-08"})


def _hypo(
    gross_try: Decimal,
    tr_it: TrIncomeTaxData,
    sgk: TrSgkData,
    stamp: TrStampData,
    *,
    include_ss: bool = True,
) -> object:
    return turkish_hypothetical_tax(
        gross_try=gross_try,
        fx=FX,
        income_tax_rates=tr_it,
        sgk_rates=sgk,
        stamp_rates=stamp,
        includes_social_security=include_ss,
        base=HypoTaxBase.SALARY_ONLY,
        tax_year=2026,
    )


def test_check_case(tr_it: TrIncomeTaxData, sgk: TrSgkData, stamp: TrStampData) -> None:
    result = turkish_hypothetical_tax(
        gross_try=Decimal("90000") * FX.rate,
        fx=FX,
        income_tax_rates=tr_it,
        sgk_rates=sgk,
        stamp_rates=stamp,
        includes_social_security=True,
        base=HypoTaxBase.SALARY_ONLY,
        tax_year=2026,
    )
    c = result.components
    assert c is not None
    assert result.mode == "CALCULATED"
    assert abs(c.sgk_try - Decimal("535086.00")) <= 1
    assert abs(c.income_tax_try - Decimal("1670784.40")) <= 1
    assert abs(c.stamp_tax_try - Decimal("41871.30")) <= 1
    assert abs(c.total_try - Decimal("2247741.70")) <= 1
    assert abs(result.amount - Decimal("34212")) <= 1
    assert c.sgk_ceiling_applied
    assert result.fx is not None
    assert result.fx.rate == Decimal("65.7")
    assert c.total_try == c.sgk_try + c.income_tax_try + c.stamp_tax_try


def test_brackets(tr_it: TrIncomeTaxData) -> None:
    assert wage_income_tax(Decimal("190000"), tr_it) == Decimal("28500")
    assert wage_income_tax(Decimal("400000"), tr_it) == Decimal("70500")
    assert wage_income_tax(Decimal("1500000"), tr_it) == Decimal("367500")
    assert wage_income_tax(Decimal("5300000"), tr_it) == Decimal("1697500")
    assert wage_income_tax(Decimal("5377914"), tr_it) == Decimal("1728665.60")
    assert wage_income_tax(Decimal("0"), tr_it) == 0


def test_minimum_wage_exemption(tr_it: TrIncomeTaxData, sgk: TrSgkData) -> None:
    assert minimum_wage_tax_base(sgk) == Decimal("336906")
    assert minimum_wage_exemption(tr_it, sgk) == Decimal("57881.20")


def test_ceiling_not_binding(tr_it: TrIncomeTaxData, sgk: TrSgkData, stamp: TrStampData) -> None:
    gross = Decimal("1200000")  # 100,000 a month, below the 297,270 ceiling
    contribution = employee_contribution(gross, sgk)
    assert not contribution.ceiling_applied
    assert contribution.amount == Decimal("180000")
    result = _hypo(gross, tr_it, sgk, stamp)
    c = result.components  # type: ignore[attr-defined]
    assert c.sgk_try == Decimal("180000.00")
    # base 1,020,000: 28,500 + 42,000 + 27% x 620,000 = 237,900; less 57,881.20
    assert c.income_tax_try == Decimal("180018.80")
    assert c.stamp_tax_try == Decimal("6099.63")  # 0.759% x (1,200,000 - 396,360) = 6,099.6276


def test_minimum_wage_earner_pays_no_income_or_stamp_tax(
    tr_it: TrIncomeTaxData, sgk: TrSgkData, stamp: TrStampData
) -> None:
    gross = sgk.minimum_wage_gross_monthly * 12
    result = _hypo(gross, tr_it, sgk, stamp, include_ss=False)
    c = result.components  # type: ignore[attr-defined]
    assert c.income_tax_try == 0
    assert c.stamp_tax_try == 0
    assert result.amount == 0  # type: ignore[attr-defined]


def test_excluding_social_security(
    tr_it: TrIncomeTaxData, sgk: TrSgkData, stamp: TrStampData
) -> None:
    gross = Decimal("90000") * FX.rate
    with_ss = _hypo(gross, tr_it, sgk, stamp)
    without = _hypo(gross, tr_it, sgk, stamp, include_ss=False)
    c = without.components  # type: ignore[attr-defined]
    assert c.total_try == c.income_tax_try + c.stamp_tax_try
    assert with_ss.amount > without.amount  # type: ignore[attr-defined]


def test_stamp_tax(stamp: TrStampData, sgk: TrSgkData) -> None:
    assert stamp_tax(Decimal("5913000"), stamp, sgk) == Decimal("41871.2976")
    assert stamp_tax(Decimal("100000"), stamp, sgk) == 0


def test_employer_contribution_at_ceiling(sgk: TrSgkData) -> None:
    contribution = employer_contribution(Decimal("5913000"), sgk)
    assert contribution.rate == Decimal("0.215")
    assert contribution.amount == Decimal("766956.600")
    zero_incentive = employer_contribution(Decimal("5913000"), sgk, Decimal("0"))
    assert zero_incentive.rate == Decimal("0.235")


def test_every_component_serialises_with_two_decimals(
    tr_it: TrIncomeTaxData, sgk: TrSgkData, stamp: TrStampData
) -> None:
    import re

    # At the minimum wage the exemption removes all income tax and the stamp-tax base
    # is nil: the zero components must still read "0.00", like every other amount.
    for gross, include_ss in (
        (Decimal("396360"), True),
        (Decimal("396360"), False),
        (Decimal("0"), False),
        (Decimal("5913000"), True),
    ):
        result = _hypo(gross, tr_it, sgk, stamp, include_ss=include_ss)
        components = result.components  # type: ignore[attr-defined]
        assert components is not None
        dumped = components.model_dump(mode="json")
        for name, value in dumped.items():
            if name in {"tax_year", "sgk_ceiling_applied"}:
                continue
            assert re.fullmatch(r"\d+\.\d{2}", value), (gross, name, value)
    minimum = _hypo(Decimal("396360"), tr_it, sgk, stamp).components  # type: ignore[attr-defined]
    assert minimum.model_dump(mode="json")["income_tax_try"] == "0.00"
