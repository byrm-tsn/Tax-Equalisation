"""The catalogue of coded warnings, assumptions and errors.

Every code the engine can emit has a kind (error, warning, info or assumption), a text
template, and a tailoring question for the user. Templates use ``str.format`` named
fields; missing parameters are an engine bug and raise.

The catalogue holds the codes of plan Appendix B, plus three assumption codes the
result schema (Appendix A) uses (``UK_RESIDENT_FULL_YEAR``, ``UK_NIC_APPLIES``,
``RATES_UNCHANGED_LATER_YEARS``), the error ``RATES_UNAVAILABLE`` raised when no rate
set covers a date, the error ``FX_RATE_IN_FUTURE`` raised when the FX snapshot is dated
after the rates date, and the warning ``EQUALISED_ITEM_NO_HYPO_SHARE`` emitted when a
gross-equalised item joins the net guarantee without a hypothetical-tax share.
"""

from __future__ import annotations

import string
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from enum import StrEnum
from types import MappingProxyType
from typing import Final, Literal

from teq_engine.types import Assumption, Warning

__all__ = [
    "CATALOGUE",
    "CatalogueEntry",
    "Code",
    "Kind",
    "catalogue_as_dicts",
    "make_assumption",
    "make_warning",
    "render",
]

type Kind = Literal["error", "warning", "info", "assumption"]


class Code(StrEnum):
    """Every warning, assumption and error code."""

    # errors
    ROUTE_UNSUPPORTED = "ROUTE_UNSUPPORTED"
    REGION_NOT_SUPPORTED = "REGION_NOT_SUPPORTED"
    GROSS_UP_NOT_CONVERGED = "GROSS_UP_NOT_CONVERGED"
    RATES_UNAVAILABLE = "RATES_UNAVAILABLE"
    FX_RATE_IN_FUTURE = "FX_RATE_IN_FUTURE"
    # warnings and information
    GROSS_UP_FALLBACK_BISECTION = "GROSS_UP_FALLBACK_BISECTION"
    EQUALISED_ITEM_NO_HYPO_SHARE = "EQUALISED_ITEM_NO_HYPO_SHARE"
    SOCIAL_SECURITY_AGREEMENT_MAY_APPLY = "SOCIAL_SECURITY_AGREEMENT_MAY_APPLY"
    NIC_EXEMPTION_ASSUMED = "NIC_EXEMPTION_ASSUMED"
    PERSONAL_ALLOWANCE_TAPER_BAND = "PERSONAL_ALLOWANCE_TAPER_BAND"
    RATES_NOT_PUBLISHED_FOR_YEAR = "RATES_NOT_PUBLISHED_FOR_YEAR"
    RATE_SET_SUPERSEDED = "RATE_SET_SUPERSEDED"
    RELOCATION_EXCESS_TAXABLE = "RELOCATION_EXCESS_TAXABLE"
    RELOCATION_OUTSIDE_WINDOW = "RELOCATION_OUTSIDE_WINDOW"
    SALARY_CONVERTED_AT_SNAPSHOT_FX = "SALARY_CONVERTED_AT_SNAPSHOT_FX"
    HOME_EMPLOYER_SOCIAL_SECURITY_ESTIMATED = "HOME_EMPLOYER_SOCIAL_SECURITY_ESTIMATED"
    FX_RATE_USER_SUPPLIED = "FX_RATE_USER_SUPPLIED"
    FX_RATE_STALE = "FX_RATE_STALE"
    FX_RATE_IMPLAUSIBLE = "FX_RATE_IMPLAUSIBLE"
    AUTO_ENROLMENT_MAY_APPLY = "AUTO_ENROLMENT_MAY_APPLY"
    APPRENTICESHIP_LEVY_MAY_APPLY = "APPRENTICESHIP_LEVY_MAY_APPLY"
    HOME_COUNTRY_TAX_RESIDENCE_RISK = "HOME_COUNTRY_TAX_RESIDENCE_RISK"
    DEGRADED_BUNDLED_RATES = "DEGRADED_BUNDLED_RATES"
    PERSISTENCE_UNAVAILABLE = "PERSISTENCE_UNAVAILABLE"
    NARRATIVE_FALLBACK = "NARRATIVE_FALLBACK"
    IMMIGRATION_CONTENT_STALE = "IMMIGRATION_CONTENT_STALE"
    # assumptions
    UK_RESIDENT_FULL_YEAR = "UK_RESIDENT_FULL_YEAR"
    ENGLAND_RATES = "ENGLAND_RATES"
    UK_NIC_APPLIES = "UK_NIC_APPLIES"
    HOME_SCHEME_CERTIFICATE_REQUIRED = "HOME_SCHEME_CERTIFICATE_REQUIRED"
    OWR_NOT_MODELLED = "OWR_NOT_MODELLED"
    RATES_UNCHANGED_LATER_YEARS = "RATES_UNCHANGED_LATER_YEARS"
    MODE_ILLUSTRATIVE_WHOLE_YEAR = "MODE_ILLUSTRATIVE_WHOLE_YEAR"
    ANNUAL_NIC_BASIS = "ANNUAL_NIC_BASIS"
    BIK_CASH_EQUIVALENT_AS_INPUT = "BIK_CASH_EQUIVALENT_AS_INPUT"
    RELOCATION_QUALIFYING_ASSUMED = "RELOCATION_QUALIFYING_ASSUMED"
    MODIFIED_PAYE_SAME_YEAR_GROSSUP = "MODIFIED_PAYE_SAME_YEAR_GROSSUP"
    PERSONAL_ALLOWANCE_ENTITLED = "PERSONAL_ALLOWANCE_ENTITLED"
    EMPLOYMENT_ALLOWANCE_NOT_APPLIED = "EMPLOYMENT_ALLOWANCE_NOT_APPLIED"
    PAYROLLING_REPORTING_CHANGE_2027 = "PAYROLLING_REPORTING_CHANGE_2027"
    HYPO_TAX_OVERRIDE = "HYPO_TAX_OVERRIDE"


@dataclass(frozen=True, slots=True)
class CatalogueEntry:
    """One catalogue row: kind, short title, text template and tailoring question."""

    code: Code
    kind: Kind
    title: str
    template: str
    question: str

    def fields(self) -> frozenset[str]:
        """The named template fields the text and question need."""
        names: set[str] = set()
        for text in (self.template, self.question):
            for _, field, _, _ in string.Formatter().parse(text):
                if field:
                    names.add(field)
        return frozenset(names)


def _e(code: Code, kind: Kind, title: str, template: str, question: str) -> CatalogueEntry:
    return CatalogueEntry(code, kind, title, template, question)


_ENTRIES: Final = (
    # ------------------------------------------------------------------ errors
    _e(
        Code.ROUTE_UNSUPPORTED,
        "error",
        "Route not supported",
        "The route {home} to {host} is not supported. Supported routes: {supported}.",
        "Which home and host countries does the assignment involve? Adding a route needs "
        "that country's rate sets and rules.",
    ),
    _e(
        Code.REGION_NOT_SUPPORTED,
        "error",
        "Region not supported",
        "The host region {region} is not supported for {host}. Supported: {supported}.",
        "Will the employee live and work in England? Scottish income tax rates are a later "
        "addition (National Insurance is UK-wide either way).",
    ),
    _e(
        Code.GROSS_UP_NOT_CONVERGED,
        "error",
        "Gross-up not solved",
        "The gross-up could not be solved for assignment year {year}; no figures are shown.",
        "No action for the user: this is an engine fault and must be investigated.",
    ),
    _e(
        Code.RATES_UNAVAILABLE,
        "error",
        "No rates for this date",
        "No {category} rate set ({jurisdiction}) covers {as_of}, and none can be carried "
        "forward to it.",
        "Is the rates date inside a period the bundled rate tables cover?",
    ),
    _e(
        Code.FX_RATE_IN_FUTURE,
        "error",
        "Exchange rate dated after the rates date",
        "The exchange rate is dated {as_of}, after the rates date {rates_as_of}; a result "
        "cannot be pinned to a rate that was not yet known on its rates date.",
        "Enter an exchange rate dated on or before {rates_as_of}, or use a later rates date.",
    ),
    # ------------------------------------------------------------------ warnings / info
    _e(
        Code.GROSS_UP_FALLBACK_BISECTION,
        "warning",
        "Gross-up fallback used",
        "Assignment year {year}: the exact segment solve failed its check and bisection "
        "was used instead. The figures are valid; the fault should be investigated.",
        "No action for the user: an engine fault to investigate.",
    ),
    _e(
        Code.EQUALISED_ITEM_NO_HYPO_SHARE,
        "warning",
        "No hypothetical tax on a gross-equalised item",
        "The gross-equalised item {item} (£{amount} in each year it is paid) joins the net "
        "guarantee in full because the hypothetical tax base is salary only, so no "
        "hypothetical tax is deducted from it.",
        "Should the employee bear hypothetical home tax on {item}? If so, set the "
        "hypothetical tax base to all equalised items to charge a hypothetical share.",
    ),
    _e(
        Code.SOCIAL_SECURITY_AGREEMENT_MAY_APPLY,
        "info",
        "Social security agreement may apply",
        "The UK and Turkey have a social security agreement. The employee may be able to "
        "stay in the Turkish scheme for a period with a certificate of coverage, which would "
        "remove UK National Insurance but keep Turkish employer contributions.",
        "Will a certificate of coverage be obtained? If so, recalculate with home-scheme "
        "social security.",
    ),
    _e(
        Code.NIC_EXEMPTION_ASSUMED,
        "warning",
        "UK National Insurance removed",
        "UK employee and employer National Insurance and Class 1A have been removed on the "
        "assumption that the employee stays in the Turkish scheme. Turkish employer "
        "contributions continue and are shown as their own line.",
        "Has a certificate of coverage been applied for, and for which dates?",
    ),
    _e(
        Code.PERSONAL_ALLOWANCE_TAPER_BAND,
        "info",
        "Personal allowance taper band",
        "Assignment year {year}: total income of £{total_income} is inside the personal "
        "allowance taper band (£{taper_start} to £{taper_end}), where income tax has an "
        "effective marginal rate of {effective_rate}.",
        "No action needed: this explains why the cost rises steeply in this band.",
    ),
    _e(
        Code.RATES_NOT_PUBLISHED_FOR_YEAR,
        "info",
        "Rates not yet published",
        "Assignment year {year}: {jurisdiction} rates for {period} are not published; the "
        "{proxy} rates have been carried forward.",
        "Recalculate when the {period} rates are published.",
    ),
    _e(
        Code.RATE_SET_SUPERSEDED,
        "info",
        "Newer rules exist",
        "Newer rules have been approved since this result was calculated ({rate_set_id}).",
        "Rerun this scenario under the current rules?",
    ),
    _e(
        Code.RELOCATION_EXCESS_TAXABLE,
        "warning",
        "Relocation above the exemption",
        "Assignment year {year}: relocation of £{amount} exceeds the £{remaining} of "
        "exemption left (cap £{cap} per move); the excess of £{excess} is a taxable benefit "
        "with Class 1A.",
        "Can the relocation package be kept within the £{cap} cap, or is the excess intended?",
    ),
    _e(
        Code.RELOCATION_OUTSIDE_WINDOW,
        "warning",
        "Relocation outside the exemption window",
        "Assignment year {year}: relocation of £{amount} falls after the exemption window "
        "(the tax year of the job start plus the following tax year) and is treated as a "
        "taxable benefit.",
        "When does the job start? The window depends on the actual start date.",
    ),
    _e(
        Code.SALARY_CONVERTED_AT_SNAPSHOT_FX,
        "info",
        "Salary converted from lira",
        "The salary of TRY {salary_try} a year was converted to £{salary_gbp} at {rate} TRY "
        "per GBP (rate dated {as_of}) for the net guarantee and the UK gross-up.",
        "Is this the exchange rate the net guarantee should use?",
    ),
    _e(
        Code.HOME_EMPLOYER_SOCIAL_SECURITY_ESTIMATED,
        "warning",
        "Turkish employer contributions estimated",
        "Assignment year {year}: Turkish employer social security of about £{amount_gbp} "
        "(TRY {amount_try}) is included, at {rate} of earnings capped at TRY {ceiling} a "
        "month, after a {points}-point incentive.",
        "Confirm the employer rate, incentive and ceiling with the Turkish payroll.",
    ),
    _e(
        Code.FX_RATE_USER_SUPPLIED,
        "info",
        "Exchange rate supplied",
        "Exchange rate {rate} TRY per GBP dated {as_of} (source: {source}) is pinned to "
        "this result.",
        "Is this the rate to use?",
    ),
    _e(
        Code.FX_RATE_STALE,
        "warning",
        "Exchange rate is stale",
        "The exchange rate is dated {as_of}, {days} days before the rates date {rates_as_of}.",
        "Re-enter a current rate?",
    ),
    _e(
        Code.FX_RATE_IMPLAUSIBLE,
        "warning",
        "Exchange rate looks implausible",
        "The exchange rate {rate} TRY per GBP is outside the plausible band {low} to {high}.",
        "Was the rate entered the right way round (lira per pound)?",
    ),
    _e(
        Code.AUTO_ENROLMENT_MAY_APPLY,
        "info",
        "Pension auto-enrolment may apply",
        "Employer pension auto-enrolment contributions (at least 3% of qualifying earnings) "
        "may add to the cost; they are not included.",
        "Will the employee be auto-enrolled into a UK pension, or stay in a home plan?",
    ),
    _e(
        Code.APPRENTICESHIP_LEVY_MAY_APPLY,
        "info",
        "Apprenticeship Levy may apply",
        "The Apprenticeship Levy (0.5% of the pay bill above a £15,000 allowance) may add to "
        "the cost for a large employer; it is not included.",
        "Is the employer's annual UK pay bill above £3 million?",
    ),
    _e(
        Code.HOME_COUNTRY_TAX_RESIDENCE_RISK,
        "warning",
        "Home-country residence in the departure year",
        "The employee may remain Turkish tax resident in the departure year.",
        "What are the departure date and the treaty position? Calendar-accurate mode will "
        "model this.",
    ),
    _e(
        Code.DEGRADED_BUNDLED_RATES,
        "warning",
        "Bundled rates used",
        "The rates database was unavailable; the bundled rate sets were used.",
        "Retry later if the result must be saved.",
    ),
    _e(
        Code.PERSISTENCE_UNAVAILABLE,
        "warning",
        "Result not saved",
        "The result could not be saved because the database was unavailable.",
        "Retry later.",
    ),
    _e(
        Code.NARRATIVE_FALLBACK,
        "info",
        "Template narrative shown",
        "The template narrative is shown because generated narration was unavailable or rejected.",
        "No action needed.",
    ),
    _e(
        Code.IMMIGRATION_CONTENT_STALE,
        "warning",
        "Immigration guidance may be out of date",
        "Immigration guidance was last verified on {verified_at}.",
        "Re-verify fees and timings before relying on them.",
    ),
    # ------------------------------------------------------------------ assumptions
    _e(
        Code.UK_RESIDENT_FULL_YEAR,
        "assumption",
        "UK resident for whole years",
        "The employee is UK resident for each whole tax year of the assignment.",
        "Will the employee arrive or leave part-way through a tax year?",
    ),
    _e(
        Code.ENGLAND_RATES,
        "assumption",
        "England rates",
        "England income tax rates are used. Scotland has different income tax rates (not "
        "NICs) and is not supported in this version.",
        "Will the employee live in England?",
    ),
    _e(
        Code.UK_NIC_APPLIES,
        "assumption",
        "UK National Insurance applies",
        "UK employee and employer National Insurance apply (no certificate of coverage).",
        "Will the employee stay in the Turkish scheme under the social security agreement?",
    ),
    _e(
        Code.HOME_SCHEME_CERTIFICATE_REQUIRED,
        "assumption",
        "Certificate of coverage required",
        "Staying in the Turkish scheme requires a certificate of coverage, obtained before "
        "the assignment starts.",
        "Who will obtain the certificate, and by when?",
    ),
    _e(
        Code.OWR_NOT_MODELLED,
        "assumption",
        "Overseas Workday Relief not modelled",
        "Overseas Workday Relief is not modelled{claimed_note}.",
        "Are any non-UK workdays expected?",
    ),
    _e(
        Code.RATES_UNCHANGED_LATER_YEARS,
        "assumption",
        "Later-year rates unchanged",
        "Rates for years not yet published are assumed unchanged from the latest published year.",
        "Recalculate when the later rates are published.",
    ),
    _e(
        Code.MODE_ILLUSTRATIVE_WHOLE_YEAR,
        "assumption",
        "Illustrative whole years",
        "Each assignment year is one whole UK tax year and one whole Turkish calendar year; "
        "actual dates and part years are not modelled.",
        "What are the actual start and end dates? Calendar-accurate mode is future work.",
    ),
    _e(
        Code.ANNUAL_NIC_BASIS,
        "assumption",
        "Annual National Insurance basis",
        "National Insurance is computed on an annual basis; per-pay-period payroll can "
        "differ slightly.",
        "Is pay spread evenly across the year?",
    ),
    _e(
        Code.BIK_CASH_EQUIVALENT_AS_INPUT,
        "assumption",
        "Benefit values taken as entered",
        "Benefits in kind are taken at the cash equivalent entered; no statutory "
        "accommodation valuation is performed.",
        "Is the property rented by the employer, and does the employee contribute?",
    ),
    _e(
        Code.RELOCATION_QUALIFYING_ASSUMED,
        "assumption",
        "Relocation items qualify",
        "All relocation items are assumed to be qualifying expenses and benefits provided "
        "within the time limit.",
        "Are all relocation items on HMRC's qualifying list and within the time limit?",
    ),
    _e(
        Code.MODIFIED_PAYE_SAME_YEAR_GROSSUP,
        "assumption",
        "Same-year PAYE on grossed-up pay",
        "The employer operates PAYE on the grossed-up pay within the same tax year, as the "
        "pack's HMRC references describe (PAYE81740, PAYE72025), so no tax is settled in a "
        "later year.",
        "Will tax be settled through payroll in-year, or after the year end?",
    ),
    _e(
        Code.PERSONAL_ALLOWANCE_ENTITLED,
        "assumption",
        "Personal allowance available",
        "The employee is entitled to the UK personal allowance because they are UK resident.",
        "None at the reference salary, where the allowance tapers to nil; at lower salaries, "
        "is the employee entitled to it?",
    ),
    _e(
        Code.EMPLOYMENT_ALLOWANCE_NOT_APPLIED,
        "assumption",
        "No Employment Allowance",
        "No Employment Allowance is deducted from employer National Insurance.",
        "Is the employer eligible for the Employment Allowance?",
    ),
    _e(
        Code.PAYROLLING_REPORTING_CHANGE_2027,
        "assumption",
        "Payrolling of benefits from April 2027",
        "Payrolling of benefits becomes mandatory from April 2027; this changes reporting, "
        "not cost (living accommodation is excluded).",
        "Is the payroll provider ready to payroll benefits?",
    ),
    _e(
        Code.HYPO_TAX_OVERRIDE,
        "assumption",
        "Hypothetical tax supplied",
        "The hypothetical home tax of £{amount} a year was supplied, not calculated."
        "{comparison_note}",
        "Should the Turkish hypothetical tax be calculated from the 2026 rules instead?",
    ),
)

CATALOGUE: Final[Mapping[Code, CatalogueEntry]] = MappingProxyType(
    {entry.code: entry for entry in _ENTRIES}
)


def render(code: Code, params: Mapping[str, str] | None = None) -> tuple[str, str]:
    """Render the text and question for ``code`` with ``params``.

    Raises ``KeyError`` if a template field has no parameter, so a missing value is
    caught by the tests rather than shown as ``{field}`` to a user.
    """
    entry = CATALOGUE[code]
    values = dict(params or {})
    missing = entry.fields() - values.keys()
    if missing:
        raise KeyError(f"{code}: missing template parameters {sorted(missing)}")
    shown = {name: _for_prose(name, value) for name, value in values.items()}
    return entry.template.format(**shown), entry.question.format(**shown)


# Parameters that carry money. They stay plain decimal strings in ``Warning.params``
# (machine-readable) and gain thousands separators only in the rendered prose.
MONEY_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "amount",
        "amount_gbp",
        "amount_try",
        "cap",
        "ceiling",
        "excess",
        "remaining",
        "salary_gbp",
        "salary_try",
        "taper_end",
        "taper_start",
        "total_income",
    }
)


def _for_prose(name: str, value: str) -> str:
    """Format a money parameter for prose (``30,000`` or ``66,000.26``); others pass through."""
    if name not in MONEY_FIELDS:
        return value
    try:
        amount = Decimal(value)
    except (InvalidOperation, ValueError):
        return value
    if not amount.is_finite():
        return value
    quantised = amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if quantised == quantised.to_integral_value():
        return f"{quantised:,.0f}"
    return f"{quantised:,.2f}"


def make_warning(code: Code, *, assignment_year: int | None = None, **params: str) -> Warning:
    """Build a :class:`~teq_engine.types.Warning` (error, warning or info) from the catalogue.

    ``assignment_year`` also fills the ``{year}`` template field unless given explicitly.
    """
    entry = CATALOGUE[code]
    if entry.kind == "assumption":
        raise ValueError(f"{code} is an assumption, not a warning")
    if assignment_year is not None:
        params.setdefault("year", str(assignment_year))
    text, question = render(code, params)
    return Warning(
        code=code.value,
        severity=entry.kind,
        text=text,
        question=question,
        assignment_year=assignment_year,
        params=dict(params),
    )


def make_assumption(code: Code, **params: str) -> Assumption:
    """Build an :class:`~teq_engine.types.Assumption` from the catalogue."""
    entry = CATALOGUE[code]
    if entry.kind != "assumption":
        raise ValueError(f"{code} is not an assumption")
    text, question = render(code, params)
    return Assumption(code=code.value, text=text, question=question)


def catalogue_as_dicts() -> list[dict[str, str]]:
    """The catalogue as plain dictionaries (for an API listing)."""
    return [
        {
            "code": entry.code.value,
            "kind": entry.kind,
            "title": entry.title,
            "template": entry.template,
            "question": entry.question,
        }
        for entry in _ENTRIES
    ]
