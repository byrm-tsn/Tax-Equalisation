"""The scenario input form, its compensation-item rows, and the mapping to ``ScenarioInput``.

The form checks structure (numbers, dates, required fields) in plain English; the
engine's own validation (``ScenarioInput``) decides every business rule, and its errors
are mapped back onto the field that caused them. Amounts are handed to the engine as
decimal strings, never floats.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from datetime import date
from decimal import Decimal
from typing import Any, Final

from django import forms
from pydantic import ValidationError

from teq_engine import ScenarioInput, UnsupportedRouteError, resolve_route, supported_routes
from teq_engine.treatments import ItemKind, Treatment
from teq_engine.types import (
    Currency,
    Frequency,
    HypoTaxBase,
    HypoTaxMethod,
    SalaryFrequency,
    SocialSecurityMode,
)
from teq_web.scenarios.capability import Refusal, country_name, describe_refusal, region_name
from teq_web.scenarios.defaults import INDICATIVE_FX_HELP, indicative_fx_initial
from teq_web.scenarios.services import rates_date_problem, today

__all__ = [
    "MAX_EXTRA_ITEMS",
    "STANDARD_ITEMS",
    "ItemForm",
    "ItemFormSet",
    "ScenarioForm",
    "error_summary",
    "initial_from_inputs",
]

MAX_EXTRA_ITEMS: Final = 6

# The three standard items have fixed ids and labels (those of the reference example), so
# a scenario that goes through the form unchanged keeps the same inputs hash.
STANDARD_ITEMS: Final[dict[str, dict[str, Any]]] = {
    "cola": {
        "kind": ItemKind.COLA.value,
        "label": "Cost-of-living allowance",
        "frequency": Frequency.ANNUAL.value,
        "years": "ALL",
        "field": "cola_amount",
    },
    "housing": {
        "kind": ItemKind.HOUSING.value,
        "label": "Housing (rent paid by the employer)",
        "frequency": Frequency.ANNUAL.value,
        "years": "ALL",
        "field": "housing_amount",
    },
    "relocation": {
        "kind": ItemKind.RELOCATION.value,
        "label": "Relocation",
        "frequency": Frequency.ONE_OFF.value,
        "years": (1,),
        "field": "relocation_amount",
    },
}

KIND_LABELS: Final[dict[str, str]] = {
    ItemKind.BONUS.value: "Bonus",
    ItemKind.COLA.value: "Cost-of-living allowance",
    ItemKind.HOUSING.value: "Housing",
    ItemKind.RELOCATION.value: "Relocation",
    ItemKind.SCHOOL_FEES.value: "School fees",
    ItemKind.HOME_LEAVE.value: "Home leave",
    ItemKind.PENSION_EMPLOYER.value: "Employer pension contribution",
    ItemKind.OTHER.value: "Other",
}

TREATMENT_CHOICES: Final = [
    ("", "Default for the kind"),
    (Treatment.GROSS_EQUALISED.value, "Taxable cash, gross (equalised like salary)"),
    (Treatment.NET_CASH.value, "Taxable cash, promised net (grossed up)"),
    (Treatment.TAXABLE_BIK.value, "Taxable benefit in kind"),
    (Treatment.EXEMPT_CAPPED.value, "Exempt within the relocation cap"),
    (Treatment.EXEMPT.value, "Exempt"),
    (Treatment.EMPLOYER_ONLY.value, "Employer cost only (no tax effect)"),
]

FREQUENCY_CHOICES: Final = [
    (Frequency.ANNUAL.value, "A year"),
    (Frequency.MONTHLY.value, "A month"),
    (Frequency.ONE_OFF.value, "One-off"),
]

_HOME_CODES: Final = ("TR", "DE", "IN", "US")
_HOST_CODES: Final = ("GB", "TR", "DE")
_REGION_CODES: Final = ("ENG", "SCT")

# Plainer wording for engine messages that use internal names.
_ENGINE_WORDING: Final = (
    (
        "an override amount is required when the method is OVERRIDE",
        "Enter the hypothetical tax, or choose to calculate it from the Turkish rules.",
    ),
    (
        "the hypothetical tax must be below the annual salary in GBP",
        "The hypothetical tax must be less than the annual salary (in pounds).",
    ),
    (
        "the hypothetical tax must be zero when the salary is zero",
        "The hypothetical tax must be zero when the salary is zero.",
    ),
    (
        "EXEMPT_CAPPED applies to relocation items only",
        "Only relocation can be exempt within the relocation cap; choose another treatment.",
    ),
)
_FX_REASONS: Final = (
    ("a salary in TRY", "a salary in lira"),
    ("a calculated hypothetical tax", "a calculated hypothetical tax"),
    ("home-scheme social security", "staying in the Turkish social security scheme"),
)


def _country_choices(codes: Iterable[str], supported: set[str]) -> list[tuple[str, str]]:
    choices = []
    for code in codes:
        label = f"{country_name(code).removeprefix('the ')} ({code})"
        choices.append((code, label if code in supported else f"{label}, not supported yet"))
    return choices


def _home_choices() -> list[tuple[str, str]]:
    return _country_choices(_HOME_CODES, {r.home for r in supported_routes()})


def _host_choices() -> list[tuple[str, str]]:
    return _country_choices(_HOST_CODES, {r.host for r in supported_routes()})


def _region_choices() -> list[tuple[str, str]]:
    supported = {region for r in supported_routes() for region in r.regions}
    return [
        (code, region_name(code) + ("" if code in supported else " (not supported yet)"))
        for code in _REGION_CODES
    ]


def _sentence(message: str) -> str:
    """Engine messages are lower-case fragments; show them as sentences."""
    text = message.strip()
    if not text:
        return text
    text = text[0].upper() + text[1:]
    return text if text.endswith((".", "?", "!")) else text + "."


def plain_engine_message(message: str) -> str:
    """Reword an engine validation message for the form, keeping its meaning."""
    for engine, plain in _ENGINE_WORDING:
        if message == engine:
            return plain
    prefix = "an FX snapshot (GBP to TRY rate and date) is required for "
    if message.startswith(prefix):
        reasons = message[len(prefix) :]
        for engine, plain in _FX_REASONS:
            reasons = reasons.replace(engine, plain)
        return f"Enter the exchange rate and its date: they are needed for {reasons}."
    return _sentence(message)


# --------------------------------------------------------------------------- fields


class MoneyField(forms.DecimalField):
    """A pound amount that accepts ``90,000``, ``£90,000.00`` or ``90000``."""

    def __init__(self, *, label: str, required: bool = False, **kwargs: Any) -> None:
        name = label[0].lower() + label[1:]
        super().__init__(
            label=label,
            required=required,
            max_digits=12,
            decimal_places=2,
            min_value=Decimal("0"),
            max_value=Decimal("1000000000"),
            widget=forms.TextInput(attrs={"inputmode": "decimal", "autocomplete": "off"}),
            error_messages={
                "required": f"Enter the {name}.",
                "invalid": f"Enter the {name} as a number, such as 90000 or 90,000.00.",
                "min_value": f"The {name} cannot be negative.",
                "max_value": f"The {name} must be no more than 1,000,000,000.",
                "max_decimal_places": f"Enter the {name} to the penny at most.",
                "max_digits": f"The {name} must be no more than 1,000,000,000.",
                "max_whole_digits": f"The {name} must be no more than 1,000,000,000.",
            },
            **kwargs,
        )

    def to_python(self, value: Any) -> Decimal | None:  # type: ignore[override]
        if isinstance(value, str):
            value = value.replace(",", "").replace("£", "").replace(" ", "").strip()
        result: Decimal | None = super().to_python(value)
        return result

    def run_validators(self, value: Any) -> None:
        """As Django, but one message once: a huge amount fails both the digit and the
        maximum check, which share the same wording."""
        try:
            super().run_validators(value)
        except forms.ValidationError as exc:
            raise forms.ValidationError(list(dict.fromkeys(exc.messages))) from None


# Control characters (including ESC) and the bidirectional embedding, override and
# isolate characters, which can make a description display differently from its content.
_BIDI_CONTROLS: Final = frozenset(chr(c) for c in (*range(0x202A, 0x202F), *range(0x2066, 0x206A)))


def plain_text_problem(text: str, what: str) -> str | None:
    """Why ``text`` cannot be used as free text on the page, or ``None``."""
    if any(unicodedata.category(ch) == "Cc" or ch in _BIDI_CONTROLS for ch in text):
        return (
            f"The {what} cannot contain control characters or text-direction marks; "
            "type it as plain text."
        )
    return None


def _date_input() -> forms.DateInput:
    return forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")


def _amount(value: Decimal | None) -> str | None:
    """A form amount as the decimal string the engine expects (never a float)."""
    return None if value is None else format(value, "f")


def _positive(value: Decimal | None) -> bool:
    return value is not None and value > 0


# --------------------------------------------------------------------------- item rows


_YEARS_SPLIT = re.compile(r"[\s,;]+|\band\b")


def parse_years(text: str) -> str | list[int]:
    """``ALL`` (or blank) means every year; otherwise year numbers such as ``1, 2``."""
    cleaned = text.strip()
    if cleaned == "" or cleaned.upper() in {"ALL", "ALL YEARS"}:
        return "ALL"
    parts = [part for part in _YEARS_SPLIT.split(cleaned) if part]
    if not parts or not all(part.isdigit() for part in parts):
        raise forms.ValidationError(
            "Enter ALL, or the years as numbers separated by commas, such as 1 or 1, 2."
        )
    return [int(part) for part in parts]


class ItemForm(forms.Form):
    """One optional additional compensation item."""

    kind = forms.ChoiceField(
        label="Kind",
        required=False,
        choices=[("", "Choose"), *KIND_LABELS.items()],
    )
    label = forms.CharField(
        label="Description",
        required=False,
        max_length=80,
        error_messages={"max_length": "Keep the description to 80 characters or fewer."},
    )
    amount = MoneyField(label="Amount")
    frequency = forms.ChoiceField(
        label="Paid", required=False, choices=FREQUENCY_CHOICES, initial=Frequency.ANNUAL.value
    )
    years = forms.CharField(label="Years", required=False, max_length=40, initial="ALL")
    treatment = forms.ChoiceField(label="Treatment", required=False, choices=TREATMENT_CHOICES)

    def clean_label(self) -> str:
        label: str = self.cleaned_data.get("label") or ""
        problem = plain_text_problem(label, "description")
        if problem is not None:
            raise forms.ValidationError(problem)
        return label

    def is_used(self) -> bool:
        """Whether the row holds an item (a blank row is ignored)."""
        data = getattr(self, "cleaned_data", None) or {}
        return bool(data.get("kind") or data.get("label") or data.get("amount") is not None)

    def clean(self) -> dict[str, Any]:
        cleaned: dict[str, Any] = super().clean() or {}
        if not self.is_used():
            return cleaned
        if not cleaned.get("kind"):
            self.add_error("kind", "Choose what this item is.")
        if cleaned.get("amount") is None and "amount" not in self.errors:
            self.add_error("amount", "Enter the amount.")
        if "years" not in self.errors:
            try:
                cleaned["years_value"] = parse_years(cleaned.get("years") or "")
            except forms.ValidationError as exc:
                self.add_error("years", exc)
        return cleaned


ItemFormSet = forms.formset_factory(
    ItemForm,
    extra=MAX_EXTRA_ITEMS,
    max_num=MAX_EXTRA_ITEMS,
    absolute_max=MAX_EXTRA_ITEMS,
    validate_max=True,
)


# --------------------------------------------------------------------------- the form


class ScenarioForm(forms.Form):
    """Everything a scenario needs; additional items come from :data:`ItemFormSet`."""

    home_country = forms.ChoiceField(label="Home country", choices=_home_choices, initial="TR")
    host_country = forms.ChoiceField(
        label="Destination country", choices=_host_choices, initial="GB"
    )
    region = forms.ChoiceField(
        label="Region",
        choices=_region_choices,
        initial="ENG",
        help_text="Scotland has its own income tax rates and is not supported yet.",
    )
    length_years = forms.IntegerField(
        label="Assignment length in years",
        min_value=1,
        max_value=10,
        initial=2,
        widget=forms.NumberInput(attrs={"inputmode": "numeric"}),
        error_messages={
            "required": "Enter the assignment length in years.",
            "invalid": "Enter the assignment length as a whole number of years.",
            "min_value": "The assignment must last at least 1 year.",
            "max_value": "The assignment can last at most 10 years.",
        },
    )
    salary_amount = MoneyField(label="Base salary", required=True)
    salary_currency = forms.ChoiceField(
        label="Salary currency",
        choices=[(Currency.GBP.value, "Pounds (GBP)"), (Currency.TRY.value, "Turkish lira (TRY)")],
        initial=Currency.GBP.value,
    )
    salary_frequency = forms.ChoiceField(
        label="Salary is",
        choices=[
            (SalaryFrequency.ANNUAL.value, "A year"),
            (SalaryFrequency.MONTHLY.value, "A month"),
        ],
        initial=SalaryFrequency.ANNUAL.value,
    )
    hypo_method = forms.ChoiceField(
        label="Hypothetical home tax",
        widget=forms.RadioSelect,
        choices=[
            (HypoTaxMethod.OVERRIDE.value, "Use a figure I supply"),
            (
                HypoTaxMethod.CALCULATED.value,
                "Calculate it from the 2026 Turkish rules (needs an exchange rate)",
            ),
        ],
        initial=HypoTaxMethod.CALCULATED.value,
    )
    hypo_override = MoneyField(
        label="Hypothetical tax a year, in pounds",
        help_text="The tax the employee would have paid at home. Ignored when calculated.",
    )
    hypo_includes_social_security = forms.BooleanField(
        label="The hypothetical tax includes Turkish employee social security",
        required=False,
        initial=True,
    )
    hypo_base = forms.ChoiceField(
        label="Hypothetical tax is worked out on",
        choices=[
            (HypoTaxBase.SALARY_ONLY.value, "Salary only"),
            (HypoTaxBase.ALL_EQUALISED.value, "Salary and other gross items such as a bonus"),
        ],
        initial=HypoTaxBase.SALARY_ONLY.value,
    )
    cola_amount = MoneyField(
        label="Cost-of-living allowance a year, paid net",
        help_text="Promised to the employee after tax, so it is grossed up.",
    )
    housing_amount = MoneyField(
        label="Housing provided by the employer (annual rent or value)",
        help_text="A taxable benefit in kind, taken at the value entered (for example the rent).",
    )
    housing_contribution = MoneyField(
        label="Employee contribution to housing a year",
        help_text="Optional. Reduces the taxable value of the housing.",
    )
    relocation_amount = MoneyField(
        label="Relocation, one-off in year 1",
        help_text="Exempt up to £8,000 per move; any excess is taxed as a benefit.",
    )
    social_security = forms.ChoiceField(
        label="Social security",
        widget=forms.RadioSelect,
        choices=[
            (SocialSecurityMode.UK_NIC.value, "UK National Insurance applies"),
            (
                SocialSecurityMode.HOME_SCHEME_AGREEMENT.value,
                "The employee stays in the Turkish scheme under the social security "
                "agreement (needs an exchange rate)",
            ),
        ],
        initial=SocialSecurityMode.UK_NIC.value,
    )
    fx_rate = forms.DecimalField(
        label="Exchange rate, Turkish lira per pound",
        required=False,
        max_digits=12,
        decimal_places=6,
        min_value=Decimal("0.000001"),
        widget=forms.TextInput(attrs={"inputmode": "decimal", "autocomplete": "off"}),
        help_text="Needed for a salary in lira, a calculated hypothetical tax or the "
        "Turkish scheme. For example 55.25.",
        error_messages={
            "invalid": "Enter the exchange rate as a number, such as 55.25.",
            "min_value": "The exchange rate must be more than zero.",
            "max_decimal_places": "Enter the exchange rate to at most 6 decimal places.",
            "max_digits": "The exchange rate is too large.",
            "max_whole_digits": "The exchange rate is too large.",
        },
    )
    fx_date = forms.DateField(
        label="Date of the exchange rate",
        required=False,
        widget=_date_input(),
        error_messages={"invalid": "Enter the date of the exchange rate, such as 2026-10-08."},
    )
    fx_source = forms.CharField(
        label="Source of the exchange rate",
        required=False,
        max_length=200,
        help_text="Optional, for example the bank or feed used.",
    )
    rates_as_of = forms.DateField(
        label="Rates as at",
        widget=_date_input(),
        help_text="Tax rates in force on this date are used for year 1; later years follow.",
        error_messages={
            "required": "Enter the date the rates should be taken from.",
            "invalid": "Enter the rates date as a date, such as 2026-10-08.",
        },
    )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        if not self.is_bound and not self.initial:
            # A fresh form (no scenario link): calculate the hypothetical tax from the
            # Turkish rules, starting from the indicative rate, labelled as such.
            self.initial.update(indicative_fx_initial())
            self.fields["fx_rate"].help_text = INDICATIVE_FX_HELP
        if not self.is_bound and "rates_as_of" not in self.initial:
            self.initial["rates_as_of"] = today()
        self.refusal: Refusal | None = None
        # Set by to_inputs, so engine errors on items can find the row they came from.
        self._formset: Any = None
        self._sources: list[tuple[str, int | None]] = []

    def clean_rates_as_of(self) -> date:
        value: date = self.cleaned_data["rates_as_of"]
        problem = rates_date_problem(value)
        if problem is not None:
            raise forms.ValidationError(problem)
        return value

    def clean_fx_source(self) -> str:
        source: str = self.cleaned_data.get("fx_source") or ""
        problem = plain_text_problem(source, "source of the exchange rate")
        if problem is not None:
            raise forms.ValidationError(problem)
        return source

    def clean(self) -> dict[str, Any]:
        cleaned: dict[str, Any] = super().clean() or {}
        if (
            _positive(cleaned.get("housing_contribution"))
            and not _positive(cleaned.get("housing_amount"))
            and "housing_amount" not in self.errors
        ):
            self.add_error(
                "housing_contribution",
                "Enter the housing amount, or leave the employee contribution blank.",
            )
        rate, as_of = cleaned.get("fx_rate"), cleaned.get("fx_date")
        if rate is not None and as_of is None and "fx_date" not in self.errors:
            self.add_error("fx_date", "Enter the date of the exchange rate.")
        if as_of is not None and rate is None and "fx_rate" not in self.errors:
            self.add_error("fx_rate", "Enter the exchange rate for that date.")
        return cleaned

    # ------------------------------------------------------------------ to inputs

    def to_inputs(self, formset: Any) -> tuple[ScenarioInput, date] | None:
        """Build validated scenario inputs, or add errors to the forms and return ``None``.

        Call after ``is_valid()`` on both the form and the formset. The engine's
        validation runs here and its errors land on the fields that caused them; a
        route the engine refuses is reported on the route fields, with
        :attr:`refusal` explaining what is supported.
        """
        data, sources = self._scenario_data(formset)
        self._formset, self._sources = formset, sources
        try:
            inputs = ScenarioInput.model_validate(data)
        except ValidationError as exc:
            for error in exc.errors():
                self.add_engine_error(tuple(error["loc"]), error["msg"])
            return None
        try:
            resolve_route(inputs.route)
        except UnsupportedRouteError as exc:
            self.refusal = describe_refusal(exc)
            field = "region" if exc.code == "REGION_NOT_SUPPORTED" else "home_country"
            self.add_error(field, _sentence(exc.message))
            return None
        rates_as_of: date = self.cleaned_data["rates_as_of"]
        return inputs, rates_as_of

    def _scenario_data(self, formset: Any) -> tuple[dict[str, Any], list[tuple[str, int | None]]]:
        c = self.cleaned_data
        method = c["hypo_method"]
        items: list[dict[str, Any]] = []
        sources: list[tuple[str, int | None]] = []
        for item_id, spec in STANDARD_ITEMS.items():
            amount = c.get(spec["field"])
            if not _positive(amount):
                continue
            item: dict[str, Any] = {
                "id": item_id,
                "kind": spec["kind"],
                "label": spec["label"],
                "amount": _amount(amount),
                "currency": Currency.GBP.value,
                "frequency": spec["frequency"],
                "years": list(spec["years"]) if spec["years"] != "ALL" else "ALL",
            }
            if item_id == "housing" and _positive(c.get("housing_contribution")):
                item["employee_contribution"] = _amount(c["housing_contribution"])
            items.append(item)
            sources.append((item_id, None))
        for index, row in enumerate(formset.forms):
            if not row.is_used():
                continue
            row_data = row.cleaned_data
            extra: dict[str, Any] = {
                "id": f"extra-{index + 1}",
                "kind": row_data["kind"],
                "label": row_data.get("label") or None,
                "amount": _amount(row_data["amount"]),
                "currency": Currency.GBP.value,
                "frequency": row_data.get("frequency") or Frequency.ANNUAL.value,
                "years": row_data.get("years_value", "ALL"),
            }
            if row_data.get("treatment"):
                extra["treatment"] = row_data["treatment"]
            items.append(extra)
            sources.append(("extra", index))

        fx: dict[str, Any] | None = None
        if c.get("fx_rate") is not None and c.get("fx_date") is not None:
            fx = {
                "pair": "GBPTRY",
                "rate": _amount(c["fx_rate"]),
                "as_of": c["fx_date"].isoformat(),
                "source": (c.get("fx_source") or "").strip() or "user supplied",
            }
        data: dict[str, Any] = {
            "route": {
                "home": c["home_country"],
                "host": c["host_country"],
                "region": c["region"],
            },
            "assignment": {"length_years": c["length_years"]},
            "salary": {
                "amount": _amount(c["salary_amount"]),
                "currency": c["salary_currency"],
                "frequency": c["salary_frequency"],
            },
            "hypothetical_tax": {
                "method": method,
                "override": (
                    _amount(c.get("hypo_override"))
                    if method == HypoTaxMethod.OVERRIDE.value
                    else None
                ),
                "includes_social_security": bool(c.get("hypo_includes_social_security")),
                "base": c["hypo_base"],
            },
            "items": items,
            "assumptions": {
                "uk_resident": True,
                "social_security": c["social_security"],
                "owr_claimed": False,
            },
            "fx": fx,
        }
        return data, sources

    def add_engine_error(self, loc: tuple[Any, ...], message: str) -> None:
        """Put an engine error (a pydantic-style location and message) on its field.

        Item errors go to the standard item field or the additional-item row that
        produced the item; anything unplaceable becomes a form-level error.
        """
        formset, sources = self._formset, self._sources
        text = plain_engine_message(message)
        head = loc[0] if loc else None
        if head == "items" and len(loc) >= 2 and isinstance(loc[1], int):
            index = loc[1]
            attribute = str(loc[2]) if len(loc) > 2 else ""
            if index < len(sources):
                kind, row = sources[index]
                if kind == "extra" and row is not None and formset is not None:
                    row_form = formset.forms[row]
                    target = attribute if attribute in row_form.fields else None
                    row_form.add_error(target, text)
                    return
                if kind == "housing" and attribute == "employee_contribution":
                    self.add_error("housing_contribution", text)
                    return
                self.add_error(STANDARD_ITEMS[kind]["field"], text)
                return
        self.add_error(_FIELD_FOR_LOC.get(loc[:2], _FIELD_FOR_LOC.get(loc[:1])), text)


_FIELD_FOR_LOC: Final[dict[tuple[Any, ...], str]] = {
    ("route", "home"): "home_country",
    ("route", "host"): "host_country",
    ("route", "region"): "region",
    ("route",): "home_country",
    ("assignment", "length_years"): "length_years",
    ("assignment",): "length_years",
    ("salary", "amount"): "salary_amount",
    ("salary", "currency"): "salary_currency",
    ("salary", "frequency"): "salary_frequency",
    ("salary",): "salary_amount",
    ("hypothetical_tax", "override"): "hypo_override",
    ("hypothetical_tax", "base"): "hypo_base",
    ("hypothetical_tax", "includes_social_security"): "hypo_includes_social_security",
    ("hypothetical_tax",): "hypo_method",
    ("fx", "rate"): "fx_rate",
    ("fx", "as_of"): "fx_date",
    ("fx", "source"): "fx_source",
    ("fx",): "fx_rate",
    ("assumptions", "social_security"): "social_security",
    ("assumptions",): "social_security",
}


# --------------------------------------------------------------------------- prefill


def _decimal_text(value: Decimal | None) -> str:
    if value is None:
        return ""
    text = format(value, "f")
    return text[:-3] if text.endswith(".00") else text


def initial_from_inputs(
    inputs: ScenarioInput, rates_as_of: date
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Form and item-row initial values that reproduce ``inputs`` when resubmitted."""
    hypo = inputs.hypothetical_tax
    initial: dict[str, Any] = {
        "home_country": inputs.route.home,
        "host_country": inputs.route.host,
        "region": inputs.route.region or "ENG",
        "length_years": inputs.assignment.length_years,
        "salary_amount": _decimal_text(inputs.salary.amount),
        "salary_currency": inputs.salary.currency.value,
        "salary_frequency": inputs.salary.frequency.value,
        "hypo_method": hypo.method.value,
        "hypo_override": _decimal_text(hypo.override),
        "hypo_includes_social_security": hypo.includes_social_security,
        "hypo_base": hypo.base.value,
        "social_security": inputs.assumptions.social_security.value,
        "rates_as_of": rates_as_of,
    }
    if inputs.fx is not None:
        initial["fx_rate"] = format(inputs.fx.rate.normalize(), "f")
        initial["fx_date"] = inputs.fx.as_of
        initial["fx_source"] = "" if inputs.fx.source == "user supplied" else inputs.fx.source

    rows: list[dict[str, Any]] = []
    for item in inputs.items:
        spec = STANDARD_ITEMS.get(item.id)
        standard = (
            spec is not None
            and item.kind.value == spec["kind"]
            and item.frequency.value == spec["frequency"]
            and item.years == spec["years"]
            and item.treatment is None
            and item.label == spec["label"]
            and (item.employee_contribution is None or item.id == "housing")
        )
        if standard and spec is not None:
            initial[spec["field"]] = _decimal_text(item.amount)
            if item.employee_contribution is not None:
                initial["housing_contribution"] = _decimal_text(item.employee_contribution)
            continue
        rows.append(
            {
                "kind": item.kind.value,
                "label": item.label or "",
                "amount": _decimal_text(item.amount),
                "frequency": item.frequency.value,
                "years": "ALL" if item.years == "ALL" else ", ".join(str(y) for y in item.years),
                "treatment": item.treatment.value if item.treatment is not None else "",
            }
        )
    return initial, rows[:MAX_EXTRA_ITEMS]


# --------------------------------------------------------------------------- summary


def error_summary(form: forms.Form, formset: Any) -> list[dict[str, str]]:
    """Every error on the page, in page order, with the id of the field to jump to."""
    entries: list[dict[str, str]] = []
    for message in form.non_field_errors():
        entries.append({"anchor": "scenario-form", "message": str(message)})
    for name in form.fields:
        for message in form.errors.get(name, []):
            entries.append({"anchor": form[name].auto_id or name, "message": str(message)})
    if formset is not None:
        for message in formset.non_form_errors():
            entries.append({"anchor": "items", "message": str(message)})
        for index, row in enumerate(formset.forms):
            for message in row.non_field_errors():
                entries.append(
                    {"anchor": f"item-row-{index}", "message": f"Item {index + 1}: {message}"}
                )
            for name in row.fields:
                for message in row.errors.get(name, []):
                    entries.append(
                        {
                            "anchor": row[name].auto_id or name,
                            "message": f"Item {index + 1}: {message}",
                        }
                    )
    return entries
