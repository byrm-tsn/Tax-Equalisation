"""Schemas for rate-set data (plan Appendix C).

A rate set is an envelope (jurisdiction, category, label, version, effective dates,
sources, checksum) around category-specific ``data``. Amounts and rates are strings in
YAML and ``Decimal`` here; a bare YAML number is refused, as is a missing key, an
unknown key or a marginal rate at or above 100%. Validation happens when a set is
loaded, never at request time.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum
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

from teq_engine.errors import RateSetError
from teq_engine.money import engine_context
from teq_engine.types import IsoDate, Money, canonical_json, coerce_decimal, sha256_prefixed

__all__ = [
    "SUPPORTED_SCHEMA_VERSIONS",
    "Band",
    "FxBand",
    "RateCategory",
    "RateSet",
    "RateSetData",
    "RelocationRules",
    "Source",
    "TrIncomeTaxData",
    "TrSgkData",
    "TrSgkEmployee",
    "TrSgkEmployer",
    "TrStampData",
    "UkBenefitRulesData",
    "UkClass1A",
    "UkIncomeTaxData",
    "UkNicData",
    "UkNicEmployee",
    "UkNicEmployer",
    "parse_rate_set",
]

SUPPORTED_SCHEMA_VERSIONS: Final = frozenset({1})

Rate = Annotated[Decimal, BeforeValidator(coerce_decimal), Field(ge=0, lt=1)]
"""A rate in [0, 1); a 100% rate would break the gross-up's monotonicity."""

Amount = Annotated[Decimal, BeforeValidator(coerce_decimal), Field(ge=0)]
"""A non-negative amount or count, any precision."""


class RateCategory(StrEnum):
    UK_INCOME_TAX = "UK_INCOME_TAX"
    UK_NIC = "UK_NIC"
    UK_BENEFIT_RULES = "UK_BENEFIT_RULES"
    TR_INCOME_TAX = "TR_INCOME_TAX"
    TR_SGK = "TR_SGK"
    TR_STAMP = "TR_STAMP"


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")


class Source(_Frozen):
    url: Annotated[str, StringConstraints(pattern=r"^https://")]
    verified_at: IsoDate
    verified_by: str | None = None
    note: str | None = None


class Band(_Frozen):
    """A progressive band: ``rate`` applies up to ``upto`` (``None`` means no limit)."""

    upto: Money | None
    rate: Rate


def _check_bands(bands: tuple[Band, ...], what: str) -> None:
    if not bands:
        raise ValueError(f"{what}: at least one band is required")
    previous = Decimal("0")
    for index, band in enumerate(bands):
        last = index == len(bands) - 1
        if last and band.upto is not None:
            raise ValueError(f"{what}: the last band must have upto: null")
        if not last:
            if band.upto is None:
                raise ValueError(f"{what}: only the last band may be open-ended")
            if band.upto <= previous:
                raise ValueError(f"{what}: band limits must increase")
            previous = band.upto


# ---------------------------------------------------------------- UK


class UkIncomeTaxData(_Frozen):
    """UK income tax for one region and tax year.

    ``bands`` apply to taxable income (total income less the personal allowance). The
    allowance is reduced by ``taper_rate`` (half) of total income above ``taper_start``.
    """

    personal_allowance: Money
    taper_start: Money
    taper_rate: Rate
    bands: Annotated[tuple[Band, ...], Strict(False)]

    @model_validator(mode="after")
    def _validate(self) -> UkIncomeTaxData:
        _check_bands(self.bands, "UK income tax")
        with engine_context():
            for band in self.bands:
                # Inside the taper band the effective rate is rate x (1 + taper_rate).
                if band.rate * (1 + self.taper_rate) >= 1:
                    raise ValueError("an effective marginal income tax rate reaches 100%")
        return self

    @property
    def taper_end(self) -> Decimal:
        """Total income at which the allowance reaches nil (125,140 for 2026-27)."""
        with engine_context():
            if self.taper_rate == 0:
                return self.taper_start
            return self.taper_start + self.personal_allowance / self.taper_rate


class UkNicEmployee(_Frozen):
    primary_threshold: Money
    upper_earnings_limit: Money
    main_rate: Rate
    upper_rate: Rate

    @model_validator(mode="after")
    def _validate(self) -> UkNicEmployee:
        if self.upper_earnings_limit <= self.primary_threshold:
            raise ValueError("the upper earnings limit must exceed the primary threshold")
        return self


class UkNicEmployer(_Frozen):
    secondary_threshold: Money
    rate: Rate


class UkClass1A(_Frozen):
    rate: Rate


class UkNicData(_Frozen):
    """Class 1 employee and employer NICs and Class 1A, annual basis."""

    employee: UkNicEmployee
    employer: UkNicEmployer
    class_1a: UkClass1A


class RelocationRules(_Frozen):
    exemption_cap: Money
    cap_scope: Literal["PER_MOVE"]
    window: str
    window_tax_years: StrictInt = Field(ge=1)


class UkBenefitRulesData(_Frozen):
    relocation: RelocationRules


# ---------------------------------------------------------------- Turkey


class TrIncomeTaxData(_Frozen):
    """Turkish wage-income brackets (annual, lira) and the minimum-wage exemption flag."""

    wage_brackets: Annotated[tuple[Band, ...], Strict(False)]
    minimum_wage_exemption: bool

    @model_validator(mode="after")
    def _validate(self) -> TrIncomeTaxData:
        _check_bands(self.wage_brackets, "Turkish wage brackets")
        return self


class TrSgkEmployee(_Frozen):
    insurance: Rate
    unemployment: Rate

    @property
    def total(self) -> Decimal:
        with engine_context():
            return self.insurance + self.unemployment


class TrSgkEmployer(_Frozen):
    disability_old_age_death: Rate
    short_term: Rate
    health: Rate
    unemployment: Rate

    @property
    def total(self) -> Decimal:
        with engine_context():
            return self.disability_old_age_death + self.short_term + self.health + self.unemployment


class FxBand(_Frozen):
    low: Amount
    high: Amount

    @model_validator(mode="after")
    def _validate(self) -> FxBand:
        if self.high <= self.low:
            raise ValueError("fx band: high must exceed low")
        return self


class TrSgkData(_Frozen):
    """Turkish social security (SGK): minimum wage, ceiling and contribution rates."""

    minimum_wage_gross_monthly: Money
    ceiling_monthly: Money
    ceiling_multiple_of_minimum_wage: Amount | None = None
    employee: TrSgkEmployee
    employer: TrSgkEmployer
    incentive_points_default: Amount
    fx_sanity_band: dict[str, FxBand]

    @model_validator(mode="after")
    def _validate(self) -> TrSgkData:
        with engine_context():
            if self.ceiling_multiple_of_minimum_wage is not None and (
                self.ceiling_monthly
                != self.minimum_wage_gross_monthly * self.ceiling_multiple_of_minimum_wage
            ):
                raise ValueError("ceiling_monthly does not equal the minimum wage multiple")
            if self.incentive_points_default / 100 > self.employer.total:
                raise ValueError("the incentive exceeds the employer contribution rate")
        if self.ceiling_monthly < self.minimum_wage_gross_monthly:
            raise ValueError("the ceiling is below the minimum wage")
        return self


class TrStampData(_Frozen):
    rate: Rate
    minimum_wage_exempt: bool


RateSetData = (
    UkIncomeTaxData | UkNicData | UkBenefitRulesData | TrIncomeTaxData | TrSgkData | TrStampData
)

DATA_SCHEMAS: Final[Mapping[RateCategory, type[RateSetData]]] = {
    RateCategory.UK_INCOME_TAX: UkIncomeTaxData,
    RateCategory.UK_NIC: UkNicData,
    RateCategory.UK_BENEFIT_RULES: UkBenefitRulesData,
    RateCategory.TR_INCOME_TAX: TrIncomeTaxData,
    RateCategory.TR_SGK: TrSgkData,
    RateCategory.TR_STAMP: TrStampData,
}


class _Envelope(_Frozen):
    schema_version: StrictInt = 1
    jurisdiction: Annotated[str, StringConstraints(pattern=r"^[A-Z]{2}(-[A-Z]{3})?$")]
    category: Annotated[RateCategory, Strict(False)]
    label: Annotated[str, StringConstraints(min_length=1, max_length=40)]
    version: StrictInt = Field(ge=1)
    effective_from: IsoDate
    effective_to: IsoDate | None
    sources: Annotated[tuple[Source, ...], Strict(False)] = Field(min_length=1)
    checksum: str | None = None
    data: dict[str, Any]


@dataclass(frozen=True, slots=True)
class RateSet:
    """A validated rate set; ``data`` is the category's schema instance."""

    schema_version: int
    jurisdiction: str
    category: RateCategory
    label: str
    version: int
    effective_from: date
    effective_to: date | None
    sources: tuple[Source, ...]
    checksum: str
    data: RateSetData

    @property
    def id(self) -> str:
        """``CATEGORY:label:vN``, the identifier pinned in results."""
        return f"{self.category.value}:{self.label}:v{self.version}"

    def covers(self, as_of: date) -> bool:
        """Whether ``as_of`` lies in the effective range (both ends inclusive)."""
        if as_of < self.effective_from:
            return False
        return self.effective_to is None or as_of <= self.effective_to

    def data_as[T: BaseModel](self, cls: type[T]) -> T:
        """Return ``data`` as ``cls``, raising if the category does not match."""
        if not isinstance(self.data, cls):
            raise RateSetError(f"{self.id} holds {type(self.data).__name__}, not {cls.__name__}")
        return self.data

    @property
    def verified_at(self) -> date | None:
        """The most recent verification date across sources."""
        return max((s.verified_at for s in self.sources), default=None)


def _checksum(envelope: _Envelope, data: BaseModel) -> str:
    payload = {
        "schema_version": envelope.schema_version,
        "jurisdiction": envelope.jurisdiction,
        "category": envelope.category.value,
        "label": envelope.label,
        "version": envelope.version,
        "effective_from": envelope.effective_from.isoformat(),
        "effective_to": envelope.effective_to.isoformat() if envelope.effective_to else None,
        "data": data.model_dump(mode="json"),
    }
    return sha256_prefixed(canonical_json(payload))


def parse_rate_set(raw: Mapping[str, Any], *, origin: str = "<memory>") -> RateSet:
    """Validate a raw rate-set mapping (from YAML or a database row).

    The checksum is computed over the canonical envelope and data. If ``raw`` carries a
    checksum it must match; otherwise the computed one is used.
    """
    try:
        envelope = _Envelope.model_validate(dict(raw))
    except ValidationError as exc:
        raise RateSetError(f"{origin}: invalid rate-set envelope: {exc}") from exc
    if envelope.schema_version not in SUPPORTED_SCHEMA_VERSIONS:
        raise RateSetError(
            f"{origin}: schema_version {envelope.schema_version} is not supported "
            f"(supported: {sorted(SUPPORTED_SCHEMA_VERSIONS)})"
        )
    if envelope.effective_to is not None and envelope.effective_to <= envelope.effective_from:
        raise RateSetError(f"{origin}: effective_to must be after effective_from")
    schema = DATA_SCHEMAS[envelope.category]
    try:
        data: RateSetData = schema.model_validate(envelope.data)
    except ValidationError as exc:
        raise RateSetError(f"{origin}: invalid {envelope.category.value} data: {exc}") from exc
    checksum = _checksum(envelope, data)
    if envelope.checksum is not None and envelope.checksum != checksum:
        raise RateSetError(f"{origin}: checksum mismatch (expected {checksum})")
    return RateSet(
        schema_version=envelope.schema_version,
        jurisdiction=envelope.jurisdiction,
        category=envelope.category,
        label=envelope.label,
        version=envelope.version,
        effective_from=envelope.effective_from,
        effective_to=envelope.effective_to,
        sources=envelope.sources,
        checksum=checksum,
        data=data,
    )
