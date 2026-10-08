"""The calculator: scenario inputs plus rate sets in, a reproducible result out.

    calculate(inputs, provider, *, rates_as_of) -> CalculationResult

For each assignment year:

1. **Net guarantee** (HS212): ``net salary = salary - hypothetical tax``; gross-equalised
   items (a bonus) join it, less their hypothetical-tax share when the base is all
   equalised items (``override / salary`` of each item under an override; the increase
   in the calculated Turkish tax otherwise; none, with ``EQUALISED_ITEM_NO_HYPO_SHARE``,
   when the base is salary only); ``NET_CASH`` items (the cost-of-living allowance) are
   added to give the net cash target ``N`` (66,000 in the reference example).
2. **Benefits**: the cash equivalent of ``TAXABLE_BIK`` items (plus any relocation over
   the per-move cap) is ``BIK``; exempt and employer-only items are costs only.
3. **Gross-up** (PAYE81740, PAYE72025): solve ``G - IncomeTax(G + BIK) - EmployeeNICs(G)
   = N`` exactly (:mod:`teq_engine.solver`), then ceil ``G`` to the pound.
4. **Lines**: recompute income tax, employee NICs, employer NICs (15% above 5,000) and
   Class 1A (15% of BIK) from the rounded gross with the full functions; round each line
   half-up to the pound; ``NET_CASH = GROSS_CASH - INCOME_TAX - EMPLOYEE_NIC``.
5. **Totals**: ``TOTAL_EMPLOYER_COST = GROSS_CASH + EMPLOYER_NIC + CLASS_1A + BENEFIT_COST
   + EXEMPT_COST (+ HOME_EMPLOYER_SOCIAL_SECURITY)``, checked against the second
   decomposition ``NET_CASH + INCOME_TAX + EMPLOYEE_NIC + employer charges + costs``.

Under the home-scheme social security mode, UK NICs and Class 1A are zero and the
Turkish employer contribution is added as its own line. The engine has no clock and no
I/O: the rates date, the FX snapshot and the rate sets are all passed in.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Final

from teq_engine._version import __version__
from teq_engine.errors import EngineInvariantError, ScenarioValidationError, SolverError
from teq_engine.jurisdictions.gb.benefits import (
    RelocationAllocation,
    RelocationPayment,
    allocate_relocation,
    benefit_cash_equivalent,
)
from teq_engine.jurisdictions.gb.income_tax import band_slices, income_tax, personal_allowance
from teq_engine.jurisdictions.gb.nic import class_1a, employee_nic, employer_nic
from teq_engine.jurisdictions.tr.hypo import fx_used, turkish_hypothetical_tax
from teq_engine.jurisdictions.tr.social_security import employer_contribution
from teq_engine.money import DEFAULT_ROUNDING, ZERO, engine_context
from teq_engine.periods import Period, build_period_plan
from teq_engine.ratesets.provider import RateSetProvider, Resolved, resolve
from teq_engine.ratesets.schemas import (
    RateCategory,
    RateSet,
    TrIncomeTaxData,
    TrSgkData,
    TrStampData,
    UkBenefitRulesData,
    UkIncomeTaxData,
    UkNicData,
    content_checksum,
)
from teq_engine.routes.registry import RouteSpec, resolve_route
from teq_engine.solver import GrossUpProblem, gross_breakpoints, solve_gross_up
from teq_engine.trace import TraceBuilder, fmt_money, fmt_money_text, fmt_percent, fmt_rate
from teq_engine.treatments import NIC_CLASS, Treatment, default_treatment, display_label
from teq_engine.types import (
    AllocationLine,
    Assumption,
    CalculationResult,
    CompensationItem,
    Currency,
    Decomposition,
    FxUsed,
    GrossUpResult,
    HypoTaxBase,
    HypoTaxMethod,
    HypoTaxResult,
    ItemAllocation,
    ItemResult,
    Line,
    LineCode,
    NetGuarantee,
    ProvenanceEntry,
    RoundingBlock,
    ScenarioInput,
    SocialSecurityMode,
    Totals,
    Warning,
    YearResult,
    canonical_json,
    sha256_prefixed,
)
from teq_engine.warnings import Code, make_assumption, make_warning, render

__all__ = [
    "DISCLAIMER_VERSION",
    "ENGINE_VERSION",
    "SCHEMA_VERSION",
    "calculate",
    "compute_cache_key",
    "compute_rate_set_fingerprint",
]

ENGINE_VERSION: Final = __version__
SCHEMA_VERSION: Final = "1"
DISCLAIMER_VERSION: Final = "2026-10"
FX_STALE_DAYS: Final = 30

GROSS_UP_REFS: Final = ("PAYE81740", "PAYE72025", "HS212")
NET_GUARANTEE_REFS: Final = ("HS212",)

LINE_LABELS: Final[dict[LineCode, str]] = {
    LineCode.GROSS_CASH: "Gross cash pay (grossed up)",
    LineCode.TAXABLE_PAY: "Taxable pay (cash plus benefits)",
    LineCode.PERSONAL_ALLOWANCE: "Personal allowance",
    LineCode.INCOME_TAX: "Income tax",
    LineCode.EMPLOYEE_NIC: "Employee National Insurance",
    LineCode.NET_CASH: "Net cash to the employee",
    LineCode.EMPLOYER_NIC: "Employer National Insurance",
    LineCode.CLASS_1A: "Class 1A National Insurance on benefits",
    LineCode.BENEFIT_COST: "Cost of taxable benefits",
    LineCode.EXEMPT_COST: "Cost of exempt items",
    LineCode.HOME_EMPLOYER_SOCIAL_SECURITY: "Turkish employer social security (estimated)",
    LineCode.TOTAL_EMPLOYER_COST: "Total employer cost",
    LineCode.MULTIPLE_OF_SALARY: "Multiple of salary",
    LineCode.MARGINAL_COST_PER_NET_POUND: "Employer cost of one more net pound",
}

# Lines that add up across years in the totals block.
_SUMMABLE: Final = (
    LineCode.GROSS_CASH,
    LineCode.INCOME_TAX,
    LineCode.EMPLOYEE_NIC,
    LineCode.NET_CASH,
    LineCode.EMPLOYER_NIC,
    LineCode.CLASS_1A,
    LineCode.BENEFIT_COST,
    LineCode.EXEMPT_COST,
    LineCode.HOME_EMPLOYER_SOCIAL_SECURITY,
    LineCode.TOTAL_EMPLOYER_COST,
)

_SEVERITY_ORDER: Final = {"error": 0, "warning": 1, "info": 2}


# --------------------------------------------------------------------------- helpers


@dataclass(frozen=True, slots=True)
class _YearRates:
    period: Period
    uk_income_tax: Resolved
    uk_nic: Resolved
    uk_benefits: Resolved
    tr_income_tax: Resolved
    tr_sgk: Resolved
    tr_stamp: Resolved

    def all(self) -> tuple[Resolved, ...]:
        return (
            self.uk_income_tax,
            self.uk_nic,
            self.uk_benefits,
            self.tr_income_tax,
            self.tr_sgk,
            self.tr_stamp,
        )

    @property
    def uk(self) -> tuple[Resolved, ...]:
        return (self.uk_income_tax, self.uk_nic, self.uk_benefits)

    @property
    def tr(self) -> tuple[Resolved, ...]:
        return (self.tr_income_tax, self.tr_sgk, self.tr_stamp)

    @property
    def it(self) -> UkIncomeTaxData:
        return self.uk_income_tax.rate_set.data_as(UkIncomeTaxData)

    @property
    def nic(self) -> UkNicData:
        return self.uk_nic.rate_set.data_as(UkNicData)

    @property
    def benefits(self) -> UkBenefitRulesData:
        return self.uk_benefits.rate_set.data_as(UkBenefitRulesData)

    @property
    def tr_it(self) -> TrIncomeTaxData:
        return self.tr_income_tax.rate_set.data_as(TrIncomeTaxData)

    @property
    def sgk(self) -> TrSgkData:
        return self.tr_sgk.rate_set.data_as(TrSgkData)

    @property
    def stamp(self) -> TrStampData:
        return self.tr_stamp.rate_set.data_as(TrStampData)

    @property
    def ids(self) -> tuple[str, ...]:
        return tuple(sorted({r.rate_set.id for r in self.all()}))


@dataclass(slots=True)
class _YearItems:
    net_cash: Decimal = ZERO
    equalised_gross: Decimal = ZERO
    taxable_benefits: Decimal = ZERO
    benefit_cost: Decimal = ZERO
    exempt_cost: Decimal = ZERO
    allocations: dict[str, ItemAllocation] = field(default_factory=dict)
    relocation_paid: bool = False
    relocation_exempt: Decimal = ZERO
    relocation_taxable: Decimal = ZERO


@dataclass(slots=True)
class _Collector:
    warnings: list[Warning] = field(default_factory=list)
    assumptions: list[Assumption] = field(default_factory=list)

    def warn(self, code: Code, *, assignment_year: int | None = None, **params: str) -> None:
        self.warnings.append(make_warning(code, assignment_year=assignment_year, **params))

    def assume(self, code: Code, **params: str) -> None:
        if all(a.code != code.value for a in self.assumptions):
            self.assumptions.append(make_assumption(code, **params))

    def sorted_warnings(self) -> tuple[Warning, ...]:
        return tuple(sorted(self.warnings, key=lambda w: _SEVERITY_ORDER[w.severity]))


def _resolve_year(provider: RateSetProvider, route: RouteSpec, period: Period) -> _YearRates:
    uk_on = period.primary.uk_tax_year_start
    tr_on = period.primary.tr_year_start
    host_it = route.host_income_tax_jurisdiction
    host = route.host_jurisdiction
    home = route.home_jurisdiction
    return _YearRates(
        period=period,
        uk_income_tax=resolve(provider, host_it, RateCategory.UK_INCOME_TAX.value, uk_on),
        uk_nic=resolve(provider, host, RateCategory.UK_NIC.value, uk_on),
        uk_benefits=resolve(provider, host, RateCategory.UK_BENEFIT_RULES.value, uk_on),
        tr_income_tax=resolve(provider, home, RateCategory.TR_INCOME_TAX.value, tr_on),
        tr_sgk=resolve(provider, home, RateCategory.TR_SGK.value, tr_on),
        tr_stamp=resolve(provider, home, RateCategory.TR_STAMP.value, tr_on),
    )


def _check_fx_date(inputs: ScenarioInput, rates_as_of: date) -> None:
    """Refuse an FX snapshot dated after the rates date (``FX_RATE_IN_FUTURE``).

    A result is pinned to its rates date; a rate not yet known on that date would make
    the result irreproducible, so it is refused rather than silently accepted.
    """
    fx = inputs.fx
    if fx is None or fx.as_of <= rates_as_of:
        return
    params = {"as_of": fx.as_of.isoformat(), "rates_as_of": rates_as_of.isoformat()}
    text, _ = render(Code.FX_RATE_IN_FUTURE, params)
    raise ScenarioValidationError(
        text, code=Code.FX_RATE_IN_FUTURE.value, loc=("fx", "as_of"), params=params
    )


def _proxy_label(resolved: Iterable[Resolved]) -> str:
    return ", ".join(sorted({r.rate_set.label for r in resolved if r.carried_forward}))


def _allocate_items(
    inputs: ScenarioInput,
    years: Sequence[_YearRates],
    collector: _Collector,
) -> tuple[dict[int, _YearItems], tuple[RelocationAllocation, ...]]:
    """Spread each item over the years it applies to and apply the relocation cap."""
    per_year: dict[int, _YearItems] = {y.period.assignment_year: _YearItems() for y in years}
    payments: list[RelocationPayment] = []
    with engine_context():
        for item in inputs.items:
            treatment = item.effective_treatment
            for year_rates in years:
                year = year_rates.period.assignment_year
                if not item.applies_to(year):
                    continue
                bucket = per_year[year]
                annual = item.annual_amount
                net_cash = equalised = taxable = cost = exempt = ZERO
                if treatment is Treatment.GROSS_EQUALISED:
                    equalised = annual
                elif treatment is Treatment.NET_CASH:
                    net_cash = annual
                elif treatment is Treatment.TAXABLE_BIK:
                    taxable = benefit_cash_equivalent(annual, item.annual_employee_contribution)
                    cost = taxable
                elif treatment is Treatment.EXEMPT_CAPPED:
                    payments.append(RelocationPayment(year, item.id, annual))
                    continue  # allocated below, once the cap has been applied
                else:  # EXEMPT, EMPLOYER_ONLY
                    exempt = annual
                bucket.net_cash += net_cash
                bucket.equalised_gross += equalised
                bucket.taxable_benefits += taxable
                bucket.benefit_cost += cost
                bucket.exempt_cost += exempt
                bucket.allocations[item.id] = ItemAllocation(
                    assignment_year=year,
                    amount=annual,
                    net_cash=net_cash,
                    equalised_gross=equalised,
                    taxable_benefit=taxable,
                    benefit_cost=cost,
                    exempt=exempt,
                )

        # The cap is per move: it comes from the rules of the move year (year 1).
        rules = years[0].benefits.relocation
        relocation = allocate_relocation(payments, rules)
        for alloc in relocation:
            bucket = per_year[alloc.assignment_year]
            bucket.exempt_cost += alloc.exempt
            bucket.taxable_benefits += alloc.taxable
            bucket.benefit_cost += alloc.taxable
            bucket.relocation_paid = True
            bucket.relocation_exempt += alloc.exempt
            bucket.relocation_taxable += alloc.taxable
            bucket.allocations[alloc.item_id] = ItemAllocation(
                assignment_year=alloc.assignment_year,
                amount=alloc.amount,
                net_cash=ZERO,
                equalised_gross=ZERO,
                taxable_benefit=alloc.taxable,
                benefit_cost=alloc.taxable,
                exempt=alloc.exempt,
                lines=_relocation_lines(alloc),
            )
            if alloc.outside_window:
                collector.warn(
                    Code.RELOCATION_OUTSIDE_WINDOW,
                    assignment_year=alloc.assignment_year,
                    amount=fmt_money(alloc.amount),
                )
            elif alloc.taxable > 0:
                collector.warn(
                    Code.RELOCATION_EXCESS_TAXABLE,
                    assignment_year=alloc.assignment_year,
                    amount=fmt_money(alloc.amount),
                    remaining=fmt_money(alloc.remaining_before),
                    cap=fmt_money(rules.exemption_cap),
                    excess=fmt_money(alloc.taxable),
                )
    return per_year, relocation


def _relocation_lines(alloc: RelocationAllocation) -> tuple[AllocationLine, ...]:
    """The two lines of a capped relocation payment: exempt within the cap, and taxable."""
    return tuple(
        AllocationLine(
            treatment=treatment,
            display_label=display_label(treatment),
            nic_class=NIC_CLASS[treatment],
            amount=amount,
        )
        for treatment, amount in (
            (Treatment.EXEMPT_CAPPED, alloc.exempt),
            (Treatment.TAXABLE_BIK, alloc.taxable),
        )
    )


def _item_results(
    inputs: ScenarioInput,
    per_year: dict[int, _YearItems],
    relocation: Sequence[RelocationAllocation],
    cap: Decimal,
) -> tuple[ItemResult, ...]:
    results: list[ItemResult] = []
    with engine_context():
        for item in inputs.items:
            treatment = item.effective_treatment
            allocations = tuple(
                per_year[year].allocations[item.id]
                for year in sorted(per_year)
                if item.id in per_year[year].allocations
            )
            capped = treatment is Treatment.EXEMPT_CAPPED
            own = [a for a in relocation if a.item_id == item.id]
            results.append(
                ItemResult(
                    id=item.id,
                    kind=item.kind,
                    label=item.label,
                    treatment=treatment,
                    default_treatment=_default(item),
                    treatment_overridden=item.treatment is not None
                    and item.treatment is not _default(item),
                    display_label=display_label(treatment),
                    nic_class=NIC_CLASS[treatment],
                    frequency=item.frequency,
                    amount=item.amount,
                    annual_amount=item.annual_amount,
                    years=item.years,
                    cap=cap if capped else None,
                    exempt=sum((a.exempt for a in own), ZERO) if capped else None,
                    excess=sum((a.taxable for a in own), ZERO) if capped else None,
                    allocations=allocations,
                )
            )
    return tuple(results)


def _default(item: CompensationItem) -> Treatment:
    return default_treatment(item.kind)


def _override_hypo(
    inputs: ScenarioInput, year1: _YearRates, salary_try: Decimal | None
) -> HypoTaxResult:
    spec = inputs.hypothetical_tax
    if spec.override is None:  # pragma: no cover - guaranteed by ScenarioInput validation
        raise EngineInvariantError("OVERRIDE without an override amount")
    comparison: Decimal | None = None
    pinned: FxUsed | None = None
    if inputs.fx is not None and salary_try is not None:
        pinned = fx_used(inputs.fx)
        comparison = turkish_hypothetical_tax(
            gross_try=salary_try,
            fx=inputs.fx,
            income_tax_rates=year1.tr_it,
            sgk_rates=year1.sgk,
            stamp_rates=year1.stamp,
            includes_social_security=spec.includes_social_security,
            base=spec.base,
            tax_year=year1.period.primary.tr_calendar_year,
        ).amount
    return HypoTaxResult(
        mode="OVERRIDE",
        amount=spec.override,
        includes_social_security=spec.includes_social_security,
        base=spec.base,
        fx=pinned,
        calculated_for_comparison=comparison,
    )


def _hypo_for_year(
    inputs: ScenarioInput,
    year_rates: _YearRates,
    items: _YearItems,
    salary_try: Decimal | None,
    override: HypoTaxResult | None,
) -> HypoTaxResult:
    if override is not None:
        return override
    spec = inputs.hypothetical_tax
    fx = inputs.fx
    if fx is None or salary_try is None:  # pragma: no cover - guaranteed by validation
        raise EngineInvariantError("a calculated hypothetical tax needs an FX snapshot")
    with engine_context():
        gross_try = salary_try
        if spec.base is HypoTaxBase.ALL_EQUALISED:
            gross_try += items.equalised_gross * fx.rate
    return turkish_hypothetical_tax(
        gross_try=gross_try,
        fx=fx,
        income_tax_rates=year_rates.tr_it,
        sgk_rates=year_rates.sgk,
        stamp_rates=year_rates.stamp,
        includes_social_security=spec.includes_social_security,
        base=spec.base,
        tax_year=year_rates.period.primary.tr_calendar_year,
    )


@dataclass(frozen=True, slots=True)
class _HypoCharge:
    """The hypothetical tax charged in one year, split between salary and equalised items."""

    total: Decimal
    on_salary: Decimal
    on_items: Decimal
    item_shares: dict[str, Decimal]
    basis: str


_PENNY_ZERO: Final = Decimal("0.00")


def _hypo_charge(
    inputs: ScenarioInput,
    year_rates: _YearRates,
    items: _YearItems,
    hypo: HypoTaxResult,
    salary_gbp: Decimal,
    salary_try: Decimal | None,
) -> _HypoCharge:
    """Split the year's hypothetical tax between salary and gross-equalised items.

    * Base ``SALARY_ONLY``: no share; the items join the net guarantee in full (the
      caller emits ``EQUALISED_ITEM_NO_HYPO_SHARE``).
    * Base ``ALL_EQUALISED``, ``OVERRIDE``: each item bears ``amount x override /
      salary``, the override's effective rate on salary.
    * Base ``ALL_EQUALISED``, ``CALCULATED``: ``hypo`` was computed on salary plus the
      items; the share is that figure less the Turkish tax on salary alone, spread over
      the items pro rata (the last item takes the rounding remainder).
    """
    r = DEFAULT_ROUNDING
    spec = inputs.hypothetical_tax
    equalised = {
        item_id: alloc.equalised_gross
        for item_id, alloc in items.allocations.items()
        if alloc.equalised_gross > 0
    }
    if not equalised:
        return _HypoCharge(hypo.amount, hypo.amount, _PENNY_ZERO, {}, "no gross-equalised items")
    if spec.base is HypoTaxBase.SALARY_ONLY:
        return _HypoCharge(
            hypo.amount,
            hypo.amount,
            _PENNY_ZERO,
            dict.fromkeys(equalised, _PENNY_ZERO),
            "none: the hypothetical tax base is salary only",
        )
    with engine_context():
        if hypo.mode == "OVERRIDE":
            shares = {
                item_id: (
                    r.round_minor(amount * hypo.amount / salary_gbp)
                    if salary_gbp > 0
                    else _PENNY_ZERO
                )
                for item_id, amount in equalised.items()
            }
            on_items = sum(shares.values(), _PENNY_ZERO)
            return _HypoCharge(
                hypo.amount + on_items,
                hypo.amount,
                on_items,
                shares,
                f"override x item / salary ({fmt_money(hypo.amount)} / {fmt_money(salary_gbp)})",
            )
        fx = inputs.fx
        if fx is None or salary_try is None:  # pragma: no cover - guaranteed by validation
            raise EngineInvariantError("a calculated hypothetical tax needs an FX snapshot")
        on_salary = turkish_hypothetical_tax(
            gross_try=salary_try,
            fx=fx,
            income_tax_rates=year_rates.tr_it,
            sgk_rates=year_rates.sgk,
            stamp_rates=year_rates.stamp,
            includes_social_security=spec.includes_social_security,
            base=spec.base,
            tax_year=year_rates.period.primary.tr_calendar_year,
        ).amount
        on_items = hypo.amount - on_salary
        total_items = sum(equalised.values(), ZERO)
        shares = {}
        allocated = _PENNY_ZERO
        ids = list(equalised)
        for item_id in ids[:-1]:
            shares[item_id] = r.round_minor(on_items * equalised[item_id] / total_items)
            allocated += shares[item_id]
        shares[ids[-1]] = on_items - allocated
        return _HypoCharge(
            hypo.amount,
            on_salary,
            on_items,
            shares,
            "Turkish tax on salary plus the items less Turkish tax on salary alone",
        )


def _equalised_item_warnings(inputs: ScenarioInput, collector: _Collector) -> None:
    """Flag gross-equalised items that bear no hypothetical tax (base salary only)."""
    if inputs.hypothetical_tax.base is not HypoTaxBase.SALARY_ONLY:
        return
    for item in inputs.items:
        if item.effective_treatment is Treatment.GROSS_EQUALISED and item.annual_amount > 0:
            collector.warn(
                Code.EQUALISED_ITEM_NO_HYPO_SHARE,
                item=item.label or item.id,
                amount=fmt_money(item.annual_amount),
            )


def _line(code: LineCode, amount: Decimal, trace_ref: str | None) -> Line:
    return Line(code=code, label=LINE_LABELS[code], amount=amount, trace_ref=trace_ref)


def compute_rate_set_fingerprint(rate_sets: Iterable[RateSet]) -> str:
    """``sha256`` over the sorted, distinct ``(id, content checksum)`` pairs of ``rate_sets``.

    The checksum covers the figures, so editing a rate under an unchanged identifier
    (same category, label and version) changes the fingerprint.
    """
    pairs = sorted({(rs.id, content_checksum(rs)) for rs in rate_sets})
    return sha256_prefixed(canonical_json([[rs_id, checksum] for rs_id, checksum in pairs]))


def compute_cache_key(
    *, inputs_hash: str, rate_set_fingerprint: str, engine_version: str, rates_as_of: date
) -> str:
    """The key a cache or de-duplication must use for a result.

    ``sha256`` over the canonical JSON object ``{"engine_version", "inputs_hash",
    "rate_set_fingerprint", "rates_as_of" (ISO date)}``. Equal keys mean identical
    results; ``inputs_hash`` alone is not enough.
    """
    return sha256_prefixed(
        canonical_json(
            {
                "engine_version": engine_version,
                "inputs_hash": inputs_hash,
                "rate_set_fingerprint": rate_set_fingerprint,
                "rates_as_of": rates_as_of.isoformat(),
            }
        )
    )


def _provenance(years: Sequence[_YearRates]) -> tuple[ProvenanceEntry, ...]:
    used: dict[str, list[int]] = defaultdict(list)
    sets: dict[str, RateSet] = {}
    for year_rates in years:
        for resolved in year_rates.all():
            rs = resolved.rate_set
            sets[rs.id] = rs
            if year_rates.period.assignment_year not in used[rs.id]:
                used[rs.id].append(year_rates.period.assignment_year)
    return tuple(
        ProvenanceEntry(
            id=rs.id,
            jurisdiction=rs.jurisdiction,
            category=rs.category.value,
            label=rs.label,
            version=rs.version,
            checksum=rs.checksum,
            effective_from=rs.effective_from,
            effective_to=rs.effective_to,
            sources=tuple(s.url for s in rs.sources),
            verified_at=rs.verified_at,
            used_for_years=tuple(used[rs.id]),
        )
        for rs in (sets[key] for key in sorted(sets))
    )


# --------------------------------------------------------------------------- calculate


def calculate(
    inputs: ScenarioInput, provider: RateSetProvider, *, rates_as_of: date
) -> CalculationResult:
    """Calculate the employer cost of a tax-equalised assignment.

    ``rates_as_of`` anchors assignment year 1 (unless the scenario gives a start date)
    and is recorded in the result; it is never read from a clock. Raises
    :class:`~teq_engine.errors.UnsupportedRouteError` (or its region subclass) for a
    route outside the capability matrix,
    :class:`~teq_engine.errors.ScenarioValidationError` (``FX_RATE_IN_FUTURE``) when the
    FX snapshot is dated after ``rates_as_of``,
    :class:`~teq_engine.errors.RatesUnavailableError` when no rate set covers a year,
    and :class:`~teq_engine.errors.SolverError` if the gross-up cannot be solved; no
    partial figures are ever returned.
    """
    with engine_context():
        return _calculate(inputs, provider, rates_as_of)


def _calculate(
    inputs: ScenarioInput, provider: RateSetProvider, rates_as_of: date
) -> CalculationResult:
    rounding = DEFAULT_ROUNDING
    trace = TraceBuilder()
    collector = _Collector()

    # ---- route and periods
    route = resolve_route(inputs.route)
    _check_fx_date(inputs, rates_as_of)
    anchor = inputs.assignment.start_date or rates_as_of
    plan = build_period_plan(inputs.assignment.length_years, anchor, inputs.assignment.period_mode)
    trace.add(
        "route",
        "Route checked against the capability matrix.",
        values={"home": route.home, "host": route.host, "region": route.region},
    )
    trace.add(
        "period_plan",
        "Whole-year illustrative mode: assignment year 1 is the UK tax year and Turkish "
        "calendar year containing the anchor date; later years increment both.",
        values={
            "anchor": anchor.isoformat(),
            "years": "; ".join(
                f"{p.assignment_year}: UK {p.primary.uk_tax_year}, TR {p.primary.tr_calendar_year}"
                for p in plan.periods
            ),
        },
    )

    # ---- rate sets, with carry-forward flagged per year
    years = [_resolve_year(provider, route, period) for period in plan.periods]
    for year_rates in years:
        period = year_rates.period
        if any(r.carried_forward for r in year_rates.uk):
            collector.warn(
                Code.RATES_NOT_PUBLISHED_FOR_YEAR,
                assignment_year=period.assignment_year,
                jurisdiction="UK",
                period=f"tax year {period.primary.uk_tax_year}",
                proxy=_proxy_label(year_rates.uk),
            )
        if any(r.carried_forward for r in year_rates.tr):
            collector.warn(
                Code.RATES_NOT_PUBLISHED_FOR_YEAR,
                assignment_year=period.assignment_year,
                jurisdiction="Turkish",
                period=str(period.primary.tr_calendar_year),
                proxy=_proxy_label(year_rates.tr),
            )
    carried_any = any(r.carried_forward for y in years for r in y.all())
    year1 = years[0]

    # ---- route-level information
    collector.warn(Code.SOCIAL_SECURITY_AGREEMENT_MAY_APPLY)
    collector.warn(Code.AUTO_ENROLMENT_MAY_APPLY)
    collector.warn(Code.APPRENTICESHIP_LEVY_MAY_APPLY)

    # ---- FX snapshot checks
    fx = inputs.fx
    if fx is not None:
        collector.warn(
            Code.FX_RATE_USER_SUPPLIED,
            rate=fmt_rate(fx.rate),
            as_of=fx.as_of.isoformat(),
            source=fx.source,
        )
        age = (rates_as_of - fx.as_of).days
        if age > FX_STALE_DAYS:
            collector.warn(
                Code.FX_RATE_STALE,
                as_of=fx.as_of.isoformat(),
                days=str(age),
                rates_as_of=rates_as_of.isoformat(),
            )
        band = year1.sgk.fx_sanity_band.get(fx.pair)
        if band is not None and not band.low <= fx.rate <= band.high:
            collector.warn(
                Code.FX_RATE_IMPLAUSIBLE,
                rate=fmt_rate(fx.rate),
                low=fmt_rate(band.low),
                high=fmt_rate(band.high),
            )
        trace.add(
            "fx",
            "Exchange-rate snapshot supplied with the scenario (never fetched).",
            values={
                "pair": fx.pair,
                "rate": fmt_rate(fx.rate),
                "as_of": fx.as_of.isoformat(),
                "source": fx.source,
                "age_days": str(age),
            },
        )

    # ---- salary
    salary_gbp = inputs.annual_salary_gbp()
    salary_try = inputs.annual_salary_try()
    if inputs.salary.currency is Currency.TRY:
        if fx is None or salary_try is None:  # pragma: no cover - guaranteed by validation
            raise EngineInvariantError("a TRY salary needs an FX snapshot")
        collector.warn(
            Code.SALARY_CONVERTED_AT_SNAPSHOT_FX,
            salary_try=fmt_money(salary_try),
            salary_gbp=fmt_money(salary_gbp),
            rate=fmt_rate(fx.rate),
            as_of=fx.as_of.isoformat(),
        )

    # ---- items and relocation cap
    per_year, relocation = _allocate_items(inputs, years, collector)
    cap = year1.benefits.relocation.exemption_cap

    # ---- hypothetical tax, and its share on gross-equalised items
    override = (
        _override_hypo(inputs, year1, salary_try)
        if inputs.hypothetical_tax.method is HypoTaxMethod.OVERRIDE
        else None
    )
    hypo_by_year = {
        y.period.assignment_year: _hypo_for_year(
            inputs, y, per_year[y.period.assignment_year], salary_try, override
        )
        for y in years
    }
    charges: dict[int, _HypoCharge] = {}
    for y in years:
        year = y.period.assignment_year
        charge = _hypo_charge(inputs, y, per_year[year], hypo_by_year[year], salary_gbp, salary_try)
        charges[year] = charge
        allocations = per_year[year].allocations
        for item_id, share in charge.item_shares.items():
            allocations[item_id] = allocations[item_id].model_copy(
                update={"hypothetical_tax_share": share}
            )
    _equalised_item_warnings(inputs, collector)
    items = _item_results(inputs, per_year, relocation, cap)
    hypo1 = hypo_by_year[1]
    hypo_values: dict[str, str] = {"mode": hypo1.mode, "amount_gbp": fmt_money(hypo1.amount)}
    if hypo1.components is not None:
        c = hypo1.components
        hypo_values |= {
            "gross_try": fmt_money(c.gross_try),
            "sgk_try": fmt_money(c.sgk_try),
            "income_tax_before_exemption_try": fmt_money(c.income_tax_before_exemption_try),
            "minimum_wage_exemption_try": fmt_money(c.minimum_wage_exemption_try),
            "income_tax_try": fmt_money(c.income_tax_try),
            "stamp_tax_try": fmt_money(c.stamp_tax_try),
            "total_try": fmt_money(c.total_try),
        }
    if hypo1.calculated_for_comparison is not None:
        hypo_values["calculated_for_comparison_gbp"] = fmt_money(hypo1.calculated_for_comparison)
    trace.add(
        "hypothetical_tax",
        "Hypothetical home tax: supplied (override) or calculated under the Turkish rules "
        "and converted at the FX snapshot.",
        values=hypo_values,
    )

    # ---- per-year calculation
    social = inputs.assumptions.social_security
    nic_applies = social is SocialSecurityMode.UK_NIC
    year_results: list[YearResult] = []
    for year_rates in years:
        year_results.append(
            _calculate_year(
                inputs=inputs,
                year_rates=year_rates,
                items=per_year[year_rates.period.assignment_year],
                hypo=hypo_by_year[year_rates.period.assignment_year],
                charge=charges[year_rates.period.assignment_year],
                salary_gbp=salary_gbp,
                salary_try=salary_try,
                nic_applies=nic_applies,
                trace=trace,
                collector=collector,
            )
        )

    # ---- totals: sums of rounded year lines
    total_lines: list[Line] = []
    for code in _SUMMABLE:
        present = [y for y in year_results if y.has_line(code)]
        if present:
            total_lines.append(_line(code, sum((y.line(code) for y in present), ZERO), None))
    total_cost = sum((y.line(LineCode.TOTAL_EMPLOYER_COST) for y in year_results), ZERO)
    trace.add(
        "totals",
        "Assignment totals are sums of the rounded year totals.",
        values={
            "years": str(len(year_results)),
            "total_employer_cost": str(total_cost),
            "by_year": ", ".join(str(y.line(LineCode.TOTAL_EMPLOYER_COST)) for y in year_results),
        },
    )

    # ---- assumptions (fixed order)
    collector.assume(Code.UK_RESIDENT_FULL_YEAR)
    collector.assume(Code.ENGLAND_RATES)
    if nic_applies:
        collector.assume(Code.UK_NIC_APPLIES)
    else:
        collector.assume(Code.HOME_SCHEME_CERTIFICATE_REQUIRED)
    collector.assume(
        Code.OWR_NOT_MODELLED,
        claimed_note=(
            " even though a claim was indicated; any relief would lower the cost"
            if inputs.assumptions.owr_claimed
            else ""
        ),
    )
    if carried_any:
        collector.assume(Code.RATES_UNCHANGED_LATER_YEARS)
    collector.assume(Code.MODE_ILLUSTRATIVE_WHOLE_YEAR)
    collector.assume(Code.ANNUAL_NIC_BASIS)
    collector.assume(Code.BIK_CASH_EQUIVALENT_AS_INPUT)
    if any(item.effective_treatment is Treatment.EXEMPT_CAPPED for item in inputs.items):
        collector.assume(Code.RELOCATION_QUALIFYING_ASSUMED)
    collector.assume(Code.MODIFIED_PAYE_SAME_YEAR_GROSSUP)
    collector.assume(Code.PERSONAL_ALLOWANCE_ENTITLED)
    collector.assume(Code.EMPLOYMENT_ALLOWANCE_NOT_APPLIED)
    collector.assume(Code.PAYROLLING_REPORTING_CHANGE_2027)
    if override is not None:
        note = ""
        if override.calculated_for_comparison is not None:
            note = (
                " Calculated under the Turkish rules at the FX snapshot it would be "
                f"£{fmt_money_text(override.calculated_for_comparison)}."
            )
        collector.assume(
            Code.HYPO_TAX_OVERRIDE, amount=fmt_money(override.amount), comparison_note=note
        )

    rate_set_ids = tuple(sorted({rs_id for y in years for rs_id in y.ids}))
    fingerprint = compute_rate_set_fingerprint(r.rate_set for y in years for r in y.all())
    inputs_hash = inputs.inputs_hash()
    return CalculationResult(
        schema_version=SCHEMA_VERSION,
        engine_version=ENGINE_VERSION,
        rates_as_of=rates_as_of,
        rate_set_ids=rate_set_ids,
        rate_set_fingerprint=fingerprint,
        period_mode=plan.mode,
        inputs_hash=inputs_hash,
        cache_key=compute_cache_key(
            inputs_hash=inputs_hash,
            rate_set_fingerprint=fingerprint,
            engine_version=ENGINE_VERSION,
            rates_as_of=rates_as_of,
        ),
        rounding=RoundingBlock(
            gross_cash=rounding.gross_cash,
            lines=rounding.lines,
            totals=rounding.totals,
            ratios=rounding.ratios,
        ),
        route=inputs.route,
        hypothetical_tax=hypo1,
        net_guarantee=year_results[0].net_guarantee,
        items=items,
        years=tuple(year_results),
        totals=Totals(total_employer_cost=total_cost, lines=tuple(total_lines)),
        warnings=collector.sorted_warnings(),
        assumptions=tuple(collector.assumptions),
        trace=trace.steps(),
        provenance=_provenance(years),
        disclaimer_version=DISCLAIMER_VERSION,
    )


def _calculate_year(
    *,
    inputs: ScenarioInput,
    year_rates: _YearRates,
    items: _YearItems,
    hypo: HypoTaxResult,
    charge: _HypoCharge,
    salary_gbp: Decimal,
    salary_try: Decimal | None,
    nic_applies: bool,
    trace: TraceBuilder,
    collector: _Collector,
) -> YearResult:
    r = DEFAULT_ROUNDING
    period = year_rates.period
    year = period.assignment_year
    it_rates = year_rates.it
    nic_rates = year_rates.nic

    # ---- net guarantee
    net_salary = salary_gbp + items.equalised_gross - charge.total
    target = net_salary + items.net_cash
    guarantee = NetGuarantee(
        salary=salary_gbp,
        equalised_items=items.equalised_gross,
        hypothetical_tax=charge.total,
        net_salary=net_salary,
        net_allowances=items.net_cash,
        net_cash_target=target,
        hypothetical_tax_on_salary=charge.on_salary,
        hypothetical_tax_on_equalised_items=charge.on_items,
    )
    trace.add(
        "net_guarantee",
        "Net guarantee: salary plus gross-equalised items, less the hypothetical home tax "
        "(on salary, plus its share on those items when the base is all equalised items), "
        "plus allowances promised net.",
        assignment_year=year,
        refs=NET_GUARANTEE_REFS,
        values={
            "salary": fmt_money(salary_gbp),
            "equalised_items": fmt_money(items.equalised_gross),
            "hypothetical_tax": fmt_money(charge.total),
            "hypothetical_tax_mode": hypo.mode,
            "hypothetical_tax_on_salary": fmt_money(charge.on_salary),
            "hypothetical_tax_on_equalised_items": fmt_money(charge.on_items),
            "hypothetical_share_basis": charge.basis,
            "net_salary": fmt_money(net_salary),
            "net_allowances": fmt_money(items.net_cash),
            "net_cash_target": fmt_money(target),
        },
    )
    benefit_values = {
        "taxable_benefits": fmt_money(items.taxable_benefits),
        "benefit_cost": fmt_money(items.benefit_cost),
        "exempt_cost": fmt_money(items.exempt_cost),
    }
    if items.relocation_paid:
        benefit_values |= {
            "relocation_exempt": fmt_money(items.relocation_exempt),
            "relocation_excess_taxable": fmt_money(items.relocation_taxable),
        }
    benefits_ref = trace.add(
        "benefits",
        "Taxable benefits enter the income-tax base at their cash equivalent (Class 1A for "
        "the employer, no employee NICs); exempt and employer-only items are costs only. "
        "Relocation is exempt within the per-move cap; any excess is a taxable benefit.",
        assignment_year=year,
        values=benefit_values,
    )

    # ---- gross-up
    problem = GrossUpProblem(
        net_target=target,
        benefits=items.taxable_benefits,
        income_tax_rates=it_rates,
        nic_rates=nic_rates,
        nic_applies=nic_applies,
    )
    try:
        solution = solve_gross_up(problem)
    except SolverError as exc:
        text, _ = render(Code.GROSS_UP_NOT_CONVERGED, {"year": str(year)})
        raise SolverError(
            text,
            net_target=exc.net_target,
            taxable_benefits=exc.taxable_benefits,
            assignment_year=year,
        ) from exc
    if solution.method == "bisection":
        collector.warn(Code.GROSS_UP_FALLBACK_BISECTION, assignment_year=year)

    gross = r.round_gross(solution.gross)
    total_income = gross + items.taxable_benefits
    allowance = personal_allowance(total_income, it_rates)
    tax_exact = income_tax(total_income, it_rates)
    ee_exact = employee_nic(gross, nic_rates) if nic_applies else ZERO
    er_exact = employer_nic(gross, nic_rates) if nic_applies else ZERO
    c1a_exact = class_1a(items.taxable_benefits, nic_rates) if nic_applies else ZERO
    net_delivered = gross - tax_exact - ee_exact
    if net_delivered < target:
        raise EngineInvariantError(
            f"year {year}: net delivered {net_delivered} is below the guarantee {target}"
        )

    tax = r.round_line(tax_exact)
    ee = r.round_line(ee_exact)
    er = r.round_line(er_exact)
    c1a = r.round_line(c1a_exact)
    benefit_cost = r.round_line(items.benefit_cost)
    exempt_cost = r.round_line(items.exempt_cost)
    net_cash = gross - tax - ee

    gross_up = GrossUpResult(
        net_target=target,
        taxable_benefits=items.taxable_benefits,
        gross_exact=r.round_minor(solution.gross),
        gross_exact_precise=r.round_precise(solution.gross),
        gross_rounded=gross,
        segment=solution.segment_label,
        segment_from=r.round_precise(solution.segment_from),
        segment_to=None if solution.segment_to is None else r.round_precise(solution.segment_to),
        marginal_rate=solution.marginal_rate,
        income_tax_marginal_rate=solution.income_tax_rate,
        nic_marginal_rate=solution.nic_rate,
        method=solution.method,
        evaluations=solution.evaluations,
        net_delivered=r.round_minor(net_delivered),
    )
    gross_ref = trace.add(
        "gross_up",
        "Solve G - IncomeTax(G + BIK) - EmployeeNICs(G) = N exactly on the linear segment "
        "containing N, then ceil G to the pound so the guarantee is never under-delivered.",
        assignment_year=year,
        refs=GROSS_UP_REFS,
        values={
            "method": solution.method,
            "segment": solution.segment_label,
            "marginal_rate": fmt_rate(solution.marginal_rate),
            "net_target": fmt_money(target),
            "taxable_benefits": fmt_money(items.taxable_benefits),
            "breakpoints": ", ".join(fmt_money(p) for p in gross_breakpoints(problem)),
            "evaluations": str(solution.evaluations),
            "gross_exact": fmt_money(solution.gross),
            "gross_exact_precise": str(r.round_precise(solution.gross)),
            "gross_rounded": str(gross),
            "net_delivered_exact": fmt_money(net_delivered),
        },
    )
    tax_ref = trace.add(
        "income_tax",
        "Income tax recomputed on the rounded gross plus benefits: personal allowance "
        "tapered by 1 for every 2 over the taper start, then the bands on taxable income.",
        assignment_year=year,
        refs=("HS212",),
        values={
            "total_income": fmt_money(total_income),
            "personal_allowance": fmt_money(allowance),
            "taxable_income": fmt_money(max(ZERO, total_income - allowance)),
            "bands": "; ".join(
                f"{fmt_percent(s.rate)} on {fmt_money(s.amount)} = {fmt_money(s.tax)}"
                for s in band_slices(total_income, it_rates)
            ),
            "income_tax_exact": fmt_money(tax_exact),
            "income_tax": str(tax),
        },
    )
    nic_ref = trace.add(
        "employee_nic",
        (
            "Employee Class 1 NICs on cash pay only: main rate between the primary threshold "
            "and the upper earnings limit, upper rate above."
            if nic_applies
            else "No UK employee NICs: the employee stays in the Turkish scheme."
        ),
        assignment_year=year,
        values={"employee_nic_exact": fmt_money(ee_exact), "employee_nic": str(ee)},
    )

    # ---- home-scheme employer contribution
    home_line: Decimal | None = None
    home_values: dict[str, str] = {}
    if not nic_applies:
        fx = inputs.fx
        if fx is None or salary_try is None:  # pragma: no cover - guaranteed by validation
            raise EngineInvariantError("home-scheme mode needs an FX snapshot")
        gross_try = salary_try + items.equalised_gross * fx.rate
        contribution = employer_contribution(gross_try, year_rates.sgk)
        # One rounding for the line (exact lira / rate to the pound); the kuruş and
        # penny figures are for display only.
        exact_gbp = contribution.amount / fx.rate
        amount_try = r.round_minor(contribution.amount)
        amount_gbp = r.round_minor(exact_gbp)
        home_line = r.round_line(exact_gbp)
        home_values = {
            "home_employer_rate": fmt_rate(contribution.rate),
            "home_employer_base_monthly_try": fmt_money(contribution.monthly_base),
            "home_employer_try": fmt_money(amount_try),
            "home_employer_gbp": fmt_money(amount_gbp),
        }
        collector.warn(
            Code.HOME_EMPLOYER_SOCIAL_SECURITY_ESTIMATED,
            assignment_year=year,
            amount_gbp=fmt_money(amount_gbp),
            amount_try=fmt_money(amount_try),
            rate=fmt_percent(contribution.rate),
            ceiling=fmt_money(year_rates.sgk.ceiling_monthly),
            points=fmt_rate(year_rates.sgk.incentive_points_default),
        )
        if year == 1:
            collector.warn(Code.NIC_EXEMPTION_ASSUMED)

    employer_ref = trace.add(
        "employer_charges",
        (
            "Employer Class 1 NICs on cash pay above the secondary threshold; Class 1A on "
            "the cash equivalent of taxable benefits."
            if nic_applies
            else "No UK employer NICs or Class 1A; the Turkish employer contribution "
            "(employer rates less the incentive, on earnings capped at the ceiling) is shown "
            "instead."
        ),
        assignment_year=year,
        values={
            "employer_nic_exact": fmt_money(er_exact),
            "employer_nic": str(er),
            "class_1a_exact": fmt_money(c1a_exact),
            "class_1a": str(c1a),
            **home_values,
        },
    )

    if it_rates.taper_start < total_income < it_rates.taper_end:
        collector.warn(
            Code.PERSONAL_ALLOWANCE_TAPER_BAND,
            assignment_year=year,
            total_income=fmt_money(total_income),
            taper_start=fmt_money(it_rates.taper_start),
            taper_end=fmt_money(it_rates.taper_end),
            effective_rate=fmt_percent(_taper_effective_rate(it_rates, total_income)),
        )

    # ---- totals and the two decompositions
    home = home_line if home_line is not None else ZERO
    total = gross + er + c1a + benefit_cost + exempt_cost + home
    recipient_view = net_cash + tax + ee + er + c1a + benefit_cost + exempt_cost + home
    if recipient_view != total:
        raise EngineInvariantError(f"year {year}: decompositions do not foot")

    employer_rate = (
        nic_rates.employer.rate
        if nic_applies and gross > nic_rates.employer.secondary_threshold
        else ZERO
    )
    marginal_cost = r.round_ratio((1 / (1 - solution.marginal_rate)) * (1 + employer_rate))
    totals_ref = trace.add(
        "year_total",
        "Total employer cost = gross cash + employer NICs + Class 1A + benefit cost + exempt "
        "cost (+ Turkish employer contributions); checked against net cash + taxes + "
        "employer charges + costs.",
        assignment_year=year,
        values={
            "total_employer_cost": str(total),
            "recipient_view": str(recipient_view),
            "marginal_cost_formula": (
                f"1 / (1 - {fmt_rate(solution.marginal_rate)}) x (1 + {fmt_rate(employer_rate)})"
            ),
        },
    )

    lines = [
        _line(LineCode.GROSS_CASH, gross, gross_ref),
        _line(LineCode.TAXABLE_PAY, r.round_line(total_income), tax_ref),
        _line(LineCode.PERSONAL_ALLOWANCE, r.round_line(allowance), tax_ref),
        _line(LineCode.INCOME_TAX, tax, tax_ref),
        _line(LineCode.EMPLOYEE_NIC, ee, nic_ref),
        _line(LineCode.NET_CASH, net_cash, gross_ref),
        _line(LineCode.EMPLOYER_NIC, er, employer_ref),
        _line(LineCode.CLASS_1A, c1a, employer_ref),
        _line(LineCode.BENEFIT_COST, benefit_cost, benefits_ref),
        _line(LineCode.EXEMPT_COST, exempt_cost, benefits_ref),
    ]
    if home_line is not None:
        lines.append(_line(LineCode.HOME_EMPLOYER_SOCIAL_SECURITY, home_line, employer_ref))
    lines.append(_line(LineCode.TOTAL_EMPLOYER_COST, total, totals_ref))
    if salary_gbp > 0:
        lines.append(
            _line(LineCode.MULTIPLE_OF_SALARY, r.round_ratio(total / salary_gbp), totals_ref)
        )
    lines.append(_line(LineCode.MARGINAL_COST_PER_NET_POUND, marginal_cost, totals_ref))

    return YearResult(
        assignment_year=year,
        uk_tax_year=period.primary.uk_tax_year,
        tr_calendar_year=period.primary.tr_calendar_year,
        fraction=period.primary.fraction,
        rate_set_ids=year_rates.ids,
        rates_carried_forward=any(res.carried_forward for res in year_rates.all()),
        net_guarantee=guarantee,
        gross_up=gross_up,
        lines=tuple(lines),
        decomposition=Decomposition(
            employer_view=total, recipient_view=recipient_view, foots=recipient_view == total
        ),
    )


def _taper_effective_rate(rates: UkIncomeTaxData, total_income: Decimal) -> Decimal:
    """The income-tax marginal rate inside the taper band: band rate x (1 + taper rate)."""
    taxable = max(ZERO, total_income - personal_allowance(total_income, rates))
    band_rate = rates.bands[-1].rate
    for band in rates.bands:
        if band.upto is None or taxable <= band.upto:
            band_rate = band.rate
            break
    with engine_context():
        return band_rate * (1 + rates.taper_rate)
