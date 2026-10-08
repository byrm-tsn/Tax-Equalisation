"""Domain types: validated scenario inputs and the calculation result.

All models are pydantic v2, ``frozen=True``, ``strict=True`` and ``extra="forbid"``.
Amounts are :class:`~decimal.Decimal` and serialise to JSON as strings; floats and bare
JSON numbers are refused for amounts. Input money is canonicalised to two decimal places
so that ``"90000"`` and ``"90000.00"`` hash identically.

Strict mode would refuse a plain string for an enum, a list for a tuple and an ISO string
for a date when validating Python dictionaries (for example form data). Those fields are
therefore declared with ``Strict(False)`` or a date coercer; booleans, integers and
amounts remain strict.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from types import MappingProxyType
from typing import Annotated, Any, Final, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    Strict,
    StrictInt,
    StringConstraints,
    ValidationError,
    model_validator,
)
from pydantic_core import InitErrorDetails, PydanticCustomError

from teq_engine.money import PENNY, MoneyTypeError, engine_context, to_decimal
from teq_engine.treatments import ItemKind, NicClass, Treatment, default_treatment

__all__ = [
    "DEFAULT_REGIONS",
    "MAX_AMOUNT",
    "MAX_ITEMS",
    "MAX_YEARS",
    "AllocationLine",
    "Assignment",
    "Assumption",
    "Assumptions",
    "CalculationResult",
    "CompensationItem",
    "Currency",
    "Dec",
    "Decomposition",
    "Frequency",
    "FxSnapshot",
    "FxUsed",
    "GrossUpResult",
    "HypoTaxBase",
    "HypoTaxComponents",
    "HypoTaxMethod",
    "HypoTaxResult",
    "HypotheticalTaxSpec",
    "IsoDate",
    "ItemAllocation",
    "ItemResult",
    "Line",
    "LineCode",
    "Money",
    "NetGuarantee",
    "PeriodMode",
    "ProvenanceEntry",
    "RoundingBlock",
    "Route",
    "Salary",
    "SalaryFrequency",
    "ScenarioInput",
    "SocialSecurityMode",
    "Totals",
    "TraceStep",
    "Warning",
    "YearResult",
    "canonical_json",
    "coerce_decimal",
    "sha256_prefixed",
]

MAX_AMOUNT: Final = Decimal("1000000000")
"""Upper bound for any single amount (one billion), a sanity limit. It applies to every
amount as entered and to every amount the engine derives from one by annualising a monthly
figure or converting at the FX snapshot."""
MAX_FX_RATE: Final = Decimal("100000")
_ZERO_MONEY: Final = Decimal("0.00")

DEFAULT_REGIONS: Final[Mapping[str, str]] = MappingProxyType({"GB": "ENG"})
"""The region a host country defaults to when none is given (England for GB)."""
MAX_ITEMS: Final = 50
MAX_YEARS: Final = 10


# --------------------------------------------------------------------------- coercers


def coerce_decimal(value: object) -> Decimal:
    """Accept ``Decimal`` or a decimal string; refuse floats, ints and booleans.

    Output models use this so that a stored result round-trips through JSON, where
    amounts are strings.
    """
    if isinstance(value, int | float):  # bool is an int subclass, refused here too
        raise PydanticCustomError(
            "decimal_string_required",
            "amounts must be decimal strings such as '90000.00', not bare numbers",
        )
    try:
        return to_decimal(value)
    except MoneyTypeError as exc:
        raise PydanticCustomError("decimal_invalid", str(exc)) from exc


def _coerce_money(value: object) -> Decimal:
    """Accept a money amount with at most two decimal places, canonicalised to 2 dp."""
    amount = coerce_decimal(value)
    if abs(amount) > MAX_AMOUNT:
        raise PydanticCustomError("money_range", "amounts must not exceed one billion")
    with engine_context():
        canonical = amount.quantize(PENNY, rounding=ROUND_HALF_UP)
    if canonical != amount:
        raise PydanticCustomError("money_precision", "amounts may have at most two decimal places")
    return canonical


def _coerce_fx_rate(value: object) -> Decimal:
    """Accept an exchange rate with at most six decimal places, canonicalised to 6 dp."""
    rate = coerce_decimal(value)
    if abs(rate) > MAX_FX_RATE:
        raise PydanticCustomError("fx_range", "exchange rates must not exceed 100000")
    with engine_context():
        canonical = rate.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
    if canonical != rate:
        raise PydanticCustomError(
            "fx_precision", "exchange rates may have at most six decimal places"
        )
    return canonical


def _coerce_date(value: object) -> date:
    """Accept a ``date`` or an ISO ``YYYY-MM-DD`` string; refuse datetimes."""
    if isinstance(value, datetime):
        raise PydanticCustomError("date_required", "a date is required, not a date-time")
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise PydanticCustomError("date_invalid", "dates must be ISO YYYY-MM-DD") from exc
    raise PydanticCustomError("date_invalid", "dates must be ISO YYYY-MM-DD strings")


Dec = Annotated[Decimal, BeforeValidator(coerce_decimal)]
"""Any engine decimal (output models)."""

Money = Annotated[Decimal, BeforeValidator(_coerce_money), Field(ge=0, le=MAX_AMOUNT)]
"""A non-negative input amount with at most two decimal places."""

FxRate = Annotated[Decimal, BeforeValidator(_coerce_fx_rate), Field(gt=0, le=MAX_FX_RATE)]
"""Turkish lira per pound sterling."""

IsoDate = Annotated[date, BeforeValidator(_coerce_date)]

CountryCode = Annotated[str, StringConstraints(pattern=r"^[A-Z]{2}$")]
RegionCode = Annotated[str, StringConstraints(pattern=r"^[A-Z]{2,3}$")]
ItemId = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9_-]{0,39}$")]


# --------------------------------------------------------------------------- enums


class Currency(StrEnum):
    GBP = "GBP"
    TRY = "TRY"


class SalaryFrequency(StrEnum):
    ANNUAL = "ANNUAL"
    MONTHLY = "MONTHLY"


class Frequency(StrEnum):
    ANNUAL = "ANNUAL"
    MONTHLY = "MONTHLY"
    ONE_OFF = "ONE_OFF"


class HypoTaxMethod(StrEnum):
    CALCULATED = "CALCULATED"
    OVERRIDE = "OVERRIDE"


class HypoTaxBase(StrEnum):
    SALARY_ONLY = "SALARY_ONLY"
    ALL_EQUALISED = "ALL_EQUALISED"


class SocialSecurityMode(StrEnum):
    UK_NIC = "UK_NIC"
    HOME_SCHEME_AGREEMENT = "HOME_SCHEME_AGREEMENT"


class PeriodMode(StrEnum):
    ILLUSTRATIVE_WHOLE_YEAR = "ILLUSTRATIVE_WHOLE_YEAR"


class LineCode(StrEnum):
    """Stable codes for result lines; templates, tests and the API key on these."""

    GROSS_CASH = "GROSS_CASH"
    TAXABLE_PAY = "TAXABLE_PAY"
    PERSONAL_ALLOWANCE = "PERSONAL_ALLOWANCE"
    INCOME_TAX = "INCOME_TAX"
    EMPLOYEE_NIC = "EMPLOYEE_NIC"
    NET_CASH = "NET_CASH"
    EMPLOYER_NIC = "EMPLOYER_NIC"
    CLASS_1A = "CLASS_1A"
    BENEFIT_COST = "BENEFIT_COST"
    EXEMPT_COST = "EXEMPT_COST"
    HOME_EMPLOYER_SOCIAL_SECURITY = "HOME_EMPLOYER_SOCIAL_SECURITY"
    TOTAL_EMPLOYER_COST = "TOTAL_EMPLOYER_COST"
    MULTIPLE_OF_SALARY = "MULTIPLE_OF_SALARY"
    MARGINAL_COST_PER_NET_POUND = "MARGINAL_COST_PER_NET_POUND"


# Lax enum and tuple fields (see the module docstring).
_LaxCurrency = Annotated[Currency, Strict(False)]
_LaxItemKind = Annotated[ItemKind, Strict(False)]
_LaxTreatment = Annotated[Treatment, Strict(False)]


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")


# --------------------------------------------------------------------------- inputs


class Route(_Frozen):
    """Home country, host country and host region (ISO 3166 style codes)."""

    home: CountryCode
    host: CountryCode
    region: RegionCode | None = None


class Assignment(_Frozen):
    """Assignment length in whole years (1 to 10) and the period mode."""

    length_years: StrictInt = Field(ge=1, le=MAX_YEARS)
    period_mode: Annotated[PeriodMode, Strict(False)] = PeriodMode.ILLUSTRATIVE_WHOLE_YEAR
    start_date: IsoDate | None = None


class Salary(_Frozen):
    """Base salary; GBP or TRY, annual or monthly."""

    amount: Money
    currency: _LaxCurrency = Currency.GBP
    frequency: Annotated[SalaryFrequency, Strict(False)] = SalaryFrequency.ANNUAL

    @property
    def annual_amount(self) -> Decimal:
        """The salary annualised (monthly amounts times 12), in its own currency."""
        if self.frequency is SalaryFrequency.MONTHLY:
            with engine_context():
                return self.amount * 12
        return self.amount


class HypotheticalTaxSpec(_Frozen):
    """How the hypothetical home tax is set.

    ``OVERRIDE`` uses ``override`` (an annual GBP amount). ``CALCULATED`` runs the
    Turkish module on the salary (and, with base ``ALL_EQUALISED``, on the other
    gross-equalised items).
    """

    method: Annotated[HypoTaxMethod, Strict(False)]
    override: Money | None = None
    includes_social_security: bool = True
    base: Annotated[HypoTaxBase, Strict(False)] = HypoTaxBase.SALARY_ONLY


class CompensationItem(_Frozen):
    """One element of the assignment package.

    ``amount`` is per year for ``ANNUAL``, per month for ``MONTHLY`` (annualised times
    12) and per listed year for ``ONE_OFF``. ``years`` is ``"ALL"`` or a list of
    assignment years. ``treatment`` defaults from ``kind``. ``employee_contribution``
    (same frequency as ``amount``) reduces a benefit's cash equivalent.
    """

    id: ItemId
    kind: _LaxItemKind
    label: Annotated[str, StringConstraints(max_length=80)] | None = None
    amount: Money
    currency: _LaxCurrency = Currency.GBP
    frequency: Annotated[Frequency, Strict(False)] = Frequency.ANNUAL
    years: Literal["ALL"] | Annotated[tuple[StrictInt, ...], Strict(False)] = "ALL"
    treatment: _LaxTreatment | None = None
    employee_contribution: Money | None = None

    @property
    def effective_treatment(self) -> Treatment:
        """The treatment in force: the explicit one, else the default for the kind."""
        return self.treatment if self.treatment is not None else default_treatment(self.kind)

    @property
    def annual_amount(self) -> Decimal:
        """The amount paid in each year the item applies to."""
        return self._annualise(self.amount)

    @property
    def annual_employee_contribution(self) -> Decimal:
        """The employee contribution in each year the item applies to."""
        if self.employee_contribution is None:
            return Decimal("0.00")
        return self._annualise(self.employee_contribution)

    def applies_to(self, assignment_year: int) -> bool:
        """Whether the item is paid in the given assignment year."""
        return self.years == "ALL" or assignment_year in self.years

    def _annualise(self, value: Decimal) -> Decimal:
        if self.frequency is Frequency.MONTHLY:
            with engine_context():
                return value * 12
        return value


class FxSnapshot(_Frozen):
    """A dated GBP to TRY rate supplied with the scenario; never fetched by the engine."""

    pair: Literal["GBPTRY"] = "GBPTRY"
    rate: FxRate
    as_of: IsoDate
    source: Annotated[str, StringConstraints(min_length=1, max_length=200)] = "user supplied"


class Assumptions(_Frozen):
    """Scenario-level assumptions the user can toggle."""

    uk_resident: bool = True
    social_security: Annotated[SocialSecurityMode, Strict(False)] = SocialSecurityMode.UK_NIC
    owr_claimed: bool = False


_RELOCATION_TREATMENTS: Final = frozenset({Treatment.EXEMPT_CAPPED, Treatment.TAXABLE_BIK})
"""The only treatments a relocation item may take: anything else would escape the cap."""


def _rule(loc: tuple[str | int, ...], message: str, value: object) -> InitErrorDetails:
    return InitErrorDetails(
        type=PydanticCustomError("scenario_rule", message),
        loc=loc,
        input=value,
    )


class ScenarioInput(_Frozen):
    """A complete, validated scenario. Frozen; hashable via :meth:`inputs_hash`."""

    route: Route
    assignment: Assignment
    salary: Salary
    hypothetical_tax: HypotheticalTaxSpec
    items: Annotated[tuple[CompensationItem, ...], Strict(False)] = Field(
        default=(), max_length=MAX_ITEMS
    )
    assumptions: Assumptions = Assumptions()
    fx: FxSnapshot | None = None

    @model_validator(mode="after")
    def _cross_field_rules(self) -> ScenarioInput:
        errors: list[InitErrorDetails] = []
        length = self.assignment.length_years
        hypo = self.hypothetical_tax
        social = self.assumptions.social_security

        if not self.assumptions.uk_resident:
            errors.append(
                _rule(
                    ("assumptions", "uk_resident"),
                    "v1 models a UK-resident employee only; non-resident treatment is not "
                    "supported",
                    False,
                )
            )

        needs_fx = []
        if self.salary.currency is Currency.TRY:
            needs_fx.append("a salary in TRY")
        if hypo.method is HypoTaxMethod.CALCULATED:
            needs_fx.append("a calculated hypothetical tax")
        if social is SocialSecurityMode.HOME_SCHEME_AGREEMENT:
            needs_fx.append("home-scheme social security")
        if needs_fx and self.fx is None:
            errors.append(
                _rule(
                    ("fx",),
                    "an FX snapshot (GBP to TRY rate and date) is required for "
                    + " and ".join(needs_fx),
                    None,
                )
            )

        if hypo.method is HypoTaxMethod.OVERRIDE and hypo.override is None:
            errors.append(
                _rule(
                    ("hypothetical_tax", "override"),
                    "an override amount is required when the method is OVERRIDE",
                    None,
                )
            )
        if hypo.method is HypoTaxMethod.CALCULATED and hypo.override is not None:
            errors.append(
                _rule(
                    ("hypothetical_tax", "override"),
                    "an override amount must not be given when the method is CALCULATED",
                    str(hypo.override),
                )
            )
        if hypo.override is not None and (
            self.salary.currency is Currency.GBP or self.fx is not None
        ):
            salary_gbp = self.annual_salary_gbp()
            if salary_gbp > 0 and hypo.override >= salary_gbp:
                errors.append(
                    _rule(
                        ("hypothetical_tax", "override"),
                        "the hypothetical tax must be below the annual salary in GBP",
                        str(hypo.override),
                    )
                )
            if salary_gbp == 0 and hypo.override != 0:
                errors.append(
                    _rule(
                        ("hypothetical_tax", "override"),
                        "the hypothetical tax must be zero when the salary is zero",
                        str(hypo.override),
                    )
                )

        seen: set[str] = set()
        for index, item in enumerate(self.items):
            loc: tuple[str | int, ...] = ("items", index)
            if item.id in seen:
                errors.append(_rule((*loc, "id"), f"duplicate item id {item.id!r}", item.id))
            seen.add(item.id)
            if item.currency is not Currency.GBP:
                errors.append(
                    _rule(
                        (*loc, "currency"),
                        "in v1 all compensation items must be in GBP",
                        str(item.currency),
                    )
                )
            if item.kind is ItemKind.SALARY:
                errors.append(
                    _rule(
                        (*loc, "kind"),
                        "salary is entered in the salary block, not as an item",
                        str(item.kind),
                    )
                )
            if item.years != "ALL":
                if not item.years:
                    errors.append(_rule((*loc, "years"), "list at least one year", []))
                if len(set(item.years)) != len(item.years):
                    errors.append(_rule((*loc, "years"), "years must not repeat", list(item.years)))
                bad = [y for y in item.years if y < 1 or y > length]
                if bad:
                    errors.append(
                        _rule(
                            (*loc, "years"),
                            f"every year must be within the assignment length (1 to {length})",
                            bad,
                        )
                    )
            elif item.frequency is Frequency.ONE_OFF:
                errors.append(
                    _rule(
                        (*loc, "years"),
                        "a one-off item must list the year(s) it is paid in",
                        "ALL",
                    )
                )
            treatment = item.effective_treatment
            if treatment is Treatment.EXEMPT_CAPPED and item.kind is not ItemKind.RELOCATION:
                errors.append(
                    _rule(
                        (*loc, "treatment"),
                        "EXEMPT_CAPPED applies to relocation items only",
                        str(treatment),
                    )
                )
            if item.kind is ItemKind.RELOCATION and treatment not in _RELOCATION_TREATMENTS:
                errors.append(
                    _rule(
                        (*loc, "treatment"),
                        "a relocation item must be EXEMPT_CAPPED (exempt up to the per-move "
                        f"cap, any excess taxable) or TAXABLE_BIK, not {treatment.value}: "
                        "relocation is exempt only within the cap",
                        str(treatment),
                    )
                )
            if item.employee_contribution is not None:
                if treatment is not Treatment.TAXABLE_BIK:
                    errors.append(
                        _rule(
                            (*loc, "employee_contribution"),
                            "an employee contribution applies to a taxable benefit in kind only",
                            str(item.employee_contribution),
                        )
                    )
                elif item.employee_contribution > item.amount:
                    errors.append(
                        _rule(
                            (*loc, "employee_contribution"),
                            "the employee contribution cannot exceed the item amount",
                            str(item.employee_contribution),
                        )
                    )

        errors.extend(self._amount_limit_errors())
        if errors:
            raise ValidationError.from_exception_data("ScenarioInput", errors)
        return self

    def _amount_limit_errors(self) -> list[InitErrorDetails]:
        """Every annualised or converted amount must respect ``MAX_AMOUNT``, as an entered
        one does: the annual salary in GBP and in TRY (when an FX snapshot converts it),
        each item's annual amount and employee contribution, and the TRY value of each
        gross-equalised item that enters the Turkish base."""
        errors: list[InitErrorDetails] = []
        limit = "one billion"
        salary = self.salary
        with engine_context():
            annual = salary.annual_amount
            if annual > MAX_AMOUNT:
                errors.append(
                    _rule(
                        ("salary", "amount"),
                        f"the annual salary ({annual} {salary.currency.value}) must not exceed "
                        f"{limit}",
                        str(salary.amount),
                    )
                )
            elif self.fx is not None:
                if salary.currency is Currency.TRY:
                    converted, other = self.annual_salary_gbp(), Currency.GBP
                else:
                    converted, other = self.annual_salary_try() or _ZERO_MONEY, Currency.TRY
                if converted > MAX_AMOUNT:
                    errors.append(
                        _rule(
                            ("salary", "amount"),
                            f"the annual salary converts to {converted} {other.value} at the "
                            f"FX snapshot rate, which must not exceed {limit}",
                            str(salary.amount),
                        )
                    )
            converts_items = self.fx is not None and (
                (
                    self.hypothetical_tax.method is HypoTaxMethod.CALCULATED
                    and self.hypothetical_tax.base is HypoTaxBase.ALL_EQUALISED
                )
                or self.assumptions.social_security is SocialSecurityMode.HOME_SCHEME_AGREEMENT
            )
            for index, item in enumerate(self.items):
                loc: tuple[str | int, ...] = ("items", index)
                if item.annual_amount > MAX_AMOUNT:
                    errors.append(
                        _rule(
                            (*loc, "amount"),
                            f"the annual amount ({item.annual_amount} GBP) must not exceed {limit}",
                            str(item.amount),
                        )
                    )
                elif (
                    converts_items
                    and self.fx is not None
                    and item.effective_treatment is Treatment.GROSS_EQUALISED
                    and item.annual_amount * self.fx.rate > MAX_AMOUNT
                ):
                    errors.append(
                        _rule(
                            (*loc, "amount"),
                            f"the item converts to {item.annual_amount * self.fx.rate} TRY a "
                            f"year at the FX snapshot rate, which must not exceed {limit}",
                            str(item.amount),
                        )
                    )
                if item.annual_employee_contribution > MAX_AMOUNT:
                    errors.append(
                        _rule(
                            (*loc, "employee_contribution"),
                            f"the annual employee contribution "
                            f"({item.annual_employee_contribution} GBP) must not exceed {limit}",
                            str(item.employee_contribution),
                        )
                    )
        return errors

    def annual_salary_gbp(self) -> Decimal:
        """The annual salary in GBP; a TRY salary converts at the FX snapshot.

        The conversion rounds half-up to the penny (``ROUND_HALF_UP``), whatever the
        engine context's default rounding.
        """
        annual = self.salary.annual_amount
        if self.salary.currency is Currency.GBP:
            return annual
        if self.fx is None:
            raise ValueError("a TRY salary needs an FX snapshot")
        with engine_context():
            return (annual / self.fx.rate).quantize(PENNY, rounding=ROUND_HALF_UP)

    def annual_salary_try(self) -> Decimal | None:
        """The annual salary in TRY: as entered, or converted from GBP at the snapshot.

        The conversion rounds half-up to the kuruş (``ROUND_HALF_UP``).
        """
        annual = self.salary.annual_amount
        if self.salary.currency is Currency.TRY:
            return annual
        if self.fx is None:
            return None
        with engine_context():
            return (annual * self.fx.rate).quantize(PENNY, rounding=ROUND_HALF_UP)

    def canonical_json(self) -> str:
        """Canonical JSON: sorted keys, no whitespace, decimals as strings.

        Inputs that calculate identically serialise identically:

        * amounts are canonical two-decimal strings and negative zero never survives
          coercion (``"-0"``, ``"0"`` and ``"0.00"`` are all ``"0.00"``);
        * a missing route region becomes the host's default region (``ENG`` for GB),
          which is the region the calculation uses;
        * an item's ``years`` list is sorted. Repeated years are refused by validation,
          so the sorted list is also free of duplicates.
        """
        data = self.model_dump(mode="json")
        route = data["route"]
        if route.get("region") is None:
            route["region"] = DEFAULT_REGIONS.get(route["host"])
        for item in data["items"]:
            if isinstance(item["years"], list):
                item["years"] = sorted(item["years"])
        return canonical_json(data)

    def inputs_hash(self) -> str:
        """``sha256:<hex>`` over :meth:`canonical_json`."""
        return sha256_prefixed(self.canonical_json())


def canonical_json(data: Any) -> str:
    """Serialise JSON-compatible data with sorted keys and no insignificant whitespace."""
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_prefixed(text: str) -> str:
    """Return ``sha256:<hex digest>`` of the UTF-8 encoding of ``text``."""
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- outputs


class Line(_Frozen):
    """One result line: a stable code, a display label and a rounded amount."""

    code: LineCode
    label: str
    amount: Dec
    trace_ref: str | None = None


class TraceStep(_Frozen):
    """One step of the calculation trace; ``values`` are display strings."""

    id: str
    step: str
    description: str
    assignment_year: StrictInt | None = None
    refs: tuple[str, ...] = ()
    values: dict[str, str] = Field(default_factory=dict)


class Warning(_Frozen):  # the domain name; shadows the builtin inside this module only
    """A coded flag shown to the user, rendered from the catalogue."""

    code: str
    severity: Literal["error", "warning", "info"]
    text: str
    question: str
    assignment_year: StrictInt | None = None
    params: dict[str, str] = Field(default_factory=dict)


class Assumption(_Frozen):
    """A coded assumption the result depends on, rendered from the catalogue."""

    code: str
    text: str
    question: str


class FxUsed(_Frozen):
    """The exchange rate a result is pinned to."""

    pair: Literal["GBPTRY"]
    rate: Dec
    as_of: IsoDate
    source: str


class HypoTaxComponents(_Frozen):
    """Every component of the calculated Turkish hypothetical tax (TRY and GBP)."""

    tax_year: StrictInt
    gross_try: Dec
    monthly_gross_try: Dec
    sgk_base_monthly_try: Dec
    sgk_ceiling_applied: bool
    sgk_try: Dec
    income_tax_base_try: Dec
    income_tax_before_exemption_try: Dec
    minimum_wage_exemption_try: Dec
    income_tax_try: Dec
    stamp_tax_base_try: Dec
    stamp_tax_try: Dec
    total_try: Dec
    sgk_gbp: Dec
    income_tax_gbp: Dec
    stamp_tax_gbp: Dec
    total_gbp: Dec


class HypoTaxResult(_Frozen):
    """The hypothetical home tax, labelled with its mode so it cannot be mislabelled."""

    mode: Literal["CALCULATED", "OVERRIDE"]
    amount: Dec
    currency: Literal["GBP"] = "GBP"
    includes_social_security: bool
    base: HypoTaxBase
    components: HypoTaxComponents | None = None
    fx: FxUsed | None = None
    calculated_for_comparison: Dec | None = None


class NetGuarantee(_Frozen):
    """The net cash the employee is promised for a year (all GBP).

    ``hypothetical_tax`` is everything charged for the year: the tax on salary
    (``hypothetical_tax_on_salary``) plus the share charged on gross-equalised items such
    as a bonus (``hypothetical_tax_on_equalised_items``). The share is zero when the
    hypothetical tax base is salary only. ``net_salary = salary + equalised_items -
    hypothetical_tax``. The two split fields are ``None`` only on results stored before
    they existed.
    """

    salary: Dec
    equalised_items: Dec
    hypothetical_tax: Dec
    net_salary: Dec
    net_allowances: Dec
    net_cash_target: Dec
    hypothetical_tax_on_salary: Dec | None = None
    hypothetical_tax_on_equalised_items: Dec | None = None


class GrossUpResult(_Frozen):
    """How the gross cash was found (see :mod:`teq_engine.solver`)."""

    net_target: Dec
    taxable_benefits: Dec
    gross_exact: Dec
    gross_exact_precise: Dec
    gross_rounded: Dec
    segment: str
    segment_from: Dec
    segment_to: Dec | None
    marginal_rate: Dec
    income_tax_marginal_rate: Dec
    nic_marginal_rate: Dec
    method: Literal["segment", "bisection"]
    evaluations: StrictInt
    net_delivered: Dec


class Decomposition(_Frozen):
    """Two independent sums of the year's cost; both must equal the total."""

    employer_view: Dec
    recipient_view: Dec
    foots: bool


class AllocationLine(_Frozen):
    """One treatment line of an item in one assignment year (GBP, unrounded)."""

    treatment: Treatment
    display_label: str
    nic_class: NicClass | None
    amount: Dec


class ItemAllocation(_Frozen):
    """How one item lands in one assignment year (GBP, unrounded).

    ``hypothetical_tax_share`` is set for gross-equalised items only: the hypothetical
    tax charged on the item that year (zero when the base is salary only).

    ``lines`` is set for a relocation item under the per-move cap: two lines every year
    it is paid, the part exempt within the cap (``EXEMPT_CAPPED``) and the excess taxed
    as a benefit in kind with Class 1A (``TAXABLE_BIK``, zero within the cap). They add
    up to ``amount``.
    """

    assignment_year: StrictInt
    amount: Dec
    net_cash: Dec
    equalised_gross: Dec
    taxable_benefit: Dec
    benefit_cost: Dec
    exempt: Dec
    hypothetical_tax_share: Dec | None = None
    lines: tuple[AllocationLine, ...] = ()


class ItemResult(_Frozen):
    """An input item with its treatment, cap usage and per-year allocation."""

    id: str
    kind: ItemKind
    label: str | None
    treatment: Treatment
    default_treatment: Treatment
    treatment_overridden: bool
    display_label: str
    nic_class: NicClass | None
    frequency: Frequency
    amount: Dec
    annual_amount: Dec
    years: Literal["ALL"] | tuple[StrictInt, ...]
    cap: Dec | None = None
    exempt: Dec | None = None
    excess: Dec | None = None
    allocations: tuple[ItemAllocation, ...] = ()


class YearResult(_Frozen):
    """One assignment year: its fiscal mapping, net guarantee, gross-up and lines."""

    assignment_year: StrictInt
    uk_tax_year: str
    tr_calendar_year: StrictInt
    fraction: Dec
    rate_set_ids: tuple[str, ...]
    rates_carried_forward: bool
    net_guarantee: NetGuarantee
    gross_up: GrossUpResult
    lines: tuple[Line, ...]
    decomposition: Decomposition

    def line(self, code: LineCode | str) -> Decimal:
        """Return the amount of the line with ``code``; ``KeyError`` if absent."""
        for line in self.lines:
            if line.code == code:
                return line.amount
        raise KeyError(code)

    def has_line(self, code: LineCode | str) -> bool:
        """Whether a line with ``code`` is present."""
        return any(line.code == code for line in self.lines)


class Totals(_Frozen):
    """Sums of rounded year lines over the assignment."""

    total_employer_cost: Dec
    lines: tuple[Line, ...]


class RoundingBlock(_Frozen):
    gross_cash: str
    lines: str
    totals: str
    ratios: str


class ProvenanceEntry(_Frozen):
    """Where a rate set came from and which years used it."""

    id: str
    jurisdiction: str
    category: str
    label: str
    version: StrictInt
    checksum: str
    effective_from: IsoDate
    effective_to: IsoDate | None
    sources: tuple[str, ...]
    verified_at: IsoDate | None
    approved_at: IsoDate | None = None
    used_for_years: tuple[StrictInt, ...]


class CalculationResult(_Frozen):
    """The complete, reproducible result of one calculation (docs/ARCHITECTURE.md, Appendix A).

    Identity fields:

    * ``inputs_hash``: ``sha256`` over the canonical inputs (see
      :meth:`ScenarioInput.canonical_json`).
    * ``rate_set_fingerprint``: ``sha256`` over the sorted ``(id, content checksum)``
      pairs of the rate sets actually used, so a figure changed under an unchanged
      identifier changes the fingerprint.
    * ``cache_key``: ``sha256`` over ``inputs_hash``, ``rate_set_fingerprint``,
      ``engine_version`` and ``rates_as_of``. Two results with the same cache key are
      identical; **a cache or de-duplication must key on this**, never on
      ``inputs_hash`` alone (the same inputs give a different result under another
      rates date, other rate figures or another engine version). ``None`` only on a
      result stored before the field existed.
    """

    schema_version: Literal["1"] = "1"
    engine_version: str
    status: Literal["OK"] = "OK"
    rates_as_of: IsoDate
    rate_set_ids: tuple[str, ...]
    rate_set_fingerprint: str
    period_mode: PeriodMode
    inputs_hash: str
    cache_key: str | None = None
    currency: Literal["GBP"] = "GBP"
    rounding: RoundingBlock
    route: Route
    hypothetical_tax: HypoTaxResult
    net_guarantee: NetGuarantee
    items: tuple[ItemResult, ...]
    years: tuple[YearResult, ...]
    totals: Totals
    warnings: tuple[Warning, ...]
    assumptions: tuple[Assumption, ...]
    trace: tuple[TraceStep, ...]
    provenance: tuple[ProvenanceEntry, ...]
    disclaimer_version: str

    def year(self, assignment_year: int) -> YearResult:
        """Return the result for one assignment year (1-based)."""
        for year in self.years:
            if year.assignment_year == assignment_year:
                return year
        raise KeyError(assignment_year)

    def warning_codes(self) -> tuple[str, ...]:
        """All warning codes, in emission order."""
        return tuple(w.code for w in self.warnings)

    def assumption_codes(self) -> tuple[str, ...]:
        """All assumption codes, in emission order."""
        return tuple(a.code for a in self.assumptions)

    def to_json(self) -> str:
        """Canonical JSON of the whole result (sorted keys, decimals as strings)."""
        return canonical_json(self.model_dump(mode="json"))
