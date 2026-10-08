"""Turn a calculation result into the rows the results page shows, in the pack's order.

Presentation only: every figure comes straight from the result (lines, totals, the net
guarantee, items and the trace); nothing is recomputed here.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Final

from teq_engine import CATALOGUE, CalculationResult, Code, LineCode, Treatment, YearResult
from teq_engine.treatments import NicClass, display_label
from teq_engine.types import Frequency, ItemResult, SocialSecurityMode
from teq_engine.types import Warning as EngineWarning
from teq_engine.warnings import render
from teq_web.formatting import gbp, percent, year_list, year_span
from teq_web.scenarios.forms import KIND_LABELS

__all__ = ["Row", "Section", "immigration_view", "results_context"]

_NUMBER_WORDS: Final = {
    2: "Two",
    3: "Three",
    4: "Four",
    5: "Five",
    6: "Six",
    7: "Seven",
    8: "Eight",
    9: "Nine",
    10: "Ten",
}

_SEVERITY_ORDER: Final = {"error": 0, "warning": 1, "info": 2}

_NO_UK_NIC: Final = "No UK National Insurance: the employee stays in the Turkish scheme"

# What each treatment means when UK National Insurance applies.
_TREATMENT_NOTES: Final = {
    Treatment.GROSS_EQUALISED: (
        "Gross pay: joins the net guarantee after the hypothetical tax; Class 1 National Insurance."
    ),
    Treatment.NET_CASH: (
        "Promised net, so it is grossed up for income tax and Class 1 National Insurance."
    ),
    Treatment.TAXABLE_BIK: (
        "Not cash: its value is taxed as income inside the gross-up. No employee National "
        "Insurance; the employer pays Class 1A."
    ),
    Treatment.EXEMPT_CAPPED: "Exempt from tax and National Insurance up to the cap per move.",
    Treatment.EXEMPT: "No tax or National Insurance; an employer cost only.",
    Treatment.EMPLOYER_ONLY: "Employer cost outside pay; no tax effect on the employee.",
}

# The same, when the employee stays in the Turkish scheme (no UK National Insurance at all).
_HOME_SCHEME_NOTES: Final = {
    Treatment.GROSS_EQUALISED: (
        f"Gross pay: joins the net guarantee after the hypothetical tax. {_NO_UK_NIC}."
    ),
    Treatment.NET_CASH: f"Promised net, so it is grossed up for income tax. {_NO_UK_NIC}.",
    Treatment.TAXABLE_BIK: (
        f"Not cash: its value is taxed as income inside the gross-up. {_NO_UK_NIC}."
    ),
}

_NIC_LABELS: Final = {"CLASS_1": "Class 1", "CLASS_1A": "Class 1A"}

_YEAR_PREFIX: Final = re.compile(r"^Assignment year \d+: ")

# Flags whose text names a period that changes every year (rates carried forward): one
# card per value of these parameters, with the periods given as a range.
_SPAN_PARAMS: Final[dict[str, tuple[str, ...]]] = {
    Code.RATES_NOT_PUBLISHED_FOR_YEAR.value: ("jurisdiction", "proxy"),
}

_EMPLOYER_VIEW: Final = (
    LineCode.GROSS_CASH,
    LineCode.EMPLOYER_NIC,
    LineCode.CLASS_1A,
    LineCode.BENEFIT_COST,
    LineCode.EXEMPT_COST,
    LineCode.HOME_EMPLOYER_SOCIAL_SECURITY,
)
_RECIPIENT_VIEW: Final = (
    LineCode.NET_CASH,
    LineCode.INCOME_TAX,
    LineCode.EMPLOYEE_NIC,
    LineCode.EMPLOYER_NIC,
    LineCode.CLASS_1A,
    LineCode.BENEFIT_COST,
    LineCode.EXEMPT_COST,
    LineCode.HOME_EMPLOYER_SOCIAL_SECURITY,
)
_OTHER_FIGURES: Final = (
    LineCode.TAXABLE_PAY,
    LineCode.PERSONAL_ALLOWANCE,
    LineCode.MULTIPLE_OF_SALARY,
    LineCode.MARGINAL_COST_PER_NET_POUND,
)
_RATIOS: Final = (LineCode.MULTIPLE_OF_SALARY, LineCode.MARGINAL_COST_PER_NET_POUND)

_GROSS_UP_ROWS: Final = (
    ("Net cash target", "net_target", "money"),
    ("Taxable benefits inside the tax base", "taxable_benefits", "money"),
    ("Segment the answer falls on", "segment", "text"),
    ("Marginal rate on that segment", "marginal_rate", "percent"),
    ("Exact gross cash", "gross_exact", "money"),
    ("Gross cash, rounded up to the pound", "gross_rounded", "money"),
    ("Net delivered on the rounded gross", "net_delivered_exact", "money"),
    ("Method", "method", "text"),
)

_STEP_TITLES: Final = {
    "route": "Route",
    "period_plan": "Period plan",
    "fx": "Exchange rate",
    "hypothetical_tax": "Hypothetical tax",
    "net_guarantee": "Net guarantee",
    "benefits": "Benefits",
    "gross_up": "Gross-up",
    "income_tax": "Income tax",
    "employee_nic": "Employee National Insurance",
    "employer_charges": "Employer charges",
    "year_total": "Year total",
    "totals": "Assignment totals",
}


@dataclass(frozen=True, slots=True)
class Row:
    """One table row: a label, one display value per year and an optional total."""

    label: str
    values: tuple[str, ...]
    total: str = ""
    code: str = ""
    strong: bool = False


@dataclass(frozen=True, slots=True)
class Section:
    """A titled group of rows with a closing total row."""

    title: str
    note: str
    rows: tuple[Row, ...]
    total: Row | None = None
    extra: dict[str, Any] = field(default_factory=dict)


def _line_or_none(year: YearResult, code: LineCode) -> Decimal | None:
    return year.line(code) if year.has_line(code) else None


def _present(years: Sequence[YearResult], code: LineCode) -> bool:
    return any(year.has_line(code) for year in years)


def _display(code: LineCode, amount: Decimal | None) -> str:
    if amount is None:
        return ""
    return str(amount) if code in _RATIOS else gbp(amount)


def _line_row(result: CalculationResult, code: LineCode, *, strong: bool = False) -> Row:
    totals = {line.code: line.amount for line in result.totals.lines}
    label = next(
        (line.label for y in result.years for line in y.lines if line.code == code), code.value
    )
    return Row(
        label=label,
        values=tuple(_display(code, _line_or_none(y, code)) for y in result.years),
        total=_display(code, totals.get(code)),
        code=code.value,
        strong=strong,
    )


def _headline(result: CalculationResult) -> list[dict[str, str]]:
    years = result.years
    cards = [
        {
            "label": f"Year {y.assignment_year}",
            "value": gbp(y.line(LineCode.TOTAL_EMPLOYER_COST)),
            "note": f"UK tax year {y.uk_tax_year}",
        }
        for y in years[:2]
    ]
    if len(years) > 1:
        words = _NUMBER_WORDS.get(len(years), str(len(years)))
        cards.append(
            {
                "label": f"{words}-year total",
                "value": gbp(result.totals.total_employer_cost),
                "note": "Sum of the rounded year totals",
            }
        )
    multiple = _line_or_none(years[0], LineCode.MULTIPLE_OF_SALARY)
    if multiple is not None:
        cards.append(
            {
                "label": "Multiple of salary",
                "value": f"{multiple} times",
                "note": f"Year 1 total against a salary of {gbp(result.net_guarantee.salary)}",
            }
        )
    return cards


def _flags(result: CalculationResult) -> list[dict[str, Any]]:
    """One card per flag, not per year: repeats of a flag are grouped and their years listed.

    Warnings are grouped by code and severity (and, for flags naming a different period
    each year, by the parameters that stay the same); identical texts collapse to one.
    Cards keep the engine's order within each severity, errors first.
    """
    groups: dict[tuple[str, ...], list[EngineWarning]] = {}
    for warning in result.warnings:
        merge = _SPAN_PARAMS.get(warning.code, ())
        key = (warning.code, warning.severity, *(warning.params.get(p, "") for p in merge))
        groups.setdefault(key, []).append(warning)
    cards = [_flag_card(warnings) for warnings in groups.values()]
    return sorted(cards, key=lambda card: _SEVERITY_ORDER.get(card["severity"], 9))


def _flag_card(warnings: list[EngineWarning]) -> dict[str, Any]:
    first = warnings[0]
    try:
        title = CATALOGUE[Code(first.code)].title
    except (KeyError, ValueError):
        title = first.code
    years = sorted({w.assignment_year for w in warnings if w.assignment_year is not None})
    texts = list(dict.fromkeys(w.text for w in warnings))
    questions = list(dict.fromkeys(w.question for w in warnings))
    if years:
        stripped = list(dict.fromkeys(_unprefixed(text) for text in texts))
        if len(stripped) == 1 or len(years) == 1:  # the years are in the card's heading
            texts = stripped
        else:
            spanned = _spanned_text(warnings, years)
            if spanned is not None:
                texts, questions = [spanned[0]], [spanned[1]]
    return {
        "code": first.code,
        "severity": first.severity,
        "title": title,
        "texts": texts,
        "questions": questions,
        "years": year_span(years),
    }


def _unprefixed(text: str) -> str:
    """A flag's text without its ``Assignment year N:`` prefix, as a sentence."""
    stripped = _YEAR_PREFIX.sub("", text)
    return stripped[:1].upper() + stripped[1:]


def _spanned_text(warnings: list[EngineWarning], years: list[int]) -> tuple[str, str] | None:
    """One text for a flag that names a different period each year, or ``None``."""
    if warnings[0].code not in _SPAN_PARAMS:
        return None
    ordered = sorted(warnings, key=lambda w: w.assignment_year or 0)
    periods = list(dict.fromkeys(w.params.get("period", "") for w in ordered))
    if not all(periods):
        return None
    consecutive = years == list(range(years[0], years[-1] + 1))
    if len(periods) == 1:
        span = periods[0]
    elif consecutive and all(p.startswith("tax year ") for p in periods):
        span = f"tax years {periods[0].removeprefix('tax year ')} to "
        span += periods[-1].removeprefix("tax year ")
    elif consecutive:
        span = f"{periods[0]} to {periods[-1]}"
    else:
        span = ", ".join(periods[:-1]) + " and " + periods[-1]
    marker = "@@year@@"
    try:
        text, question = render(
            Code(warnings[0].code), {**warnings[0].params, "period": span, "year": marker}
        )
    except (KeyError, ValueError):
        return None
    text = text.replace(f"Assignment year {marker}: ", "", 1)
    if marker in text or marker in question:
        return None
    return text[:1].upper() + text[1:], question


def _net_guarantee(result: CalculationResult) -> list[Row]:
    years = result.years
    guarantee = [y.net_guarantee for y in years]
    hypo = result.hypothetical_tax
    how = "supplied" if hypo.mode == "OVERRIDE" else "calculated"
    rows = [Row("Salary (in pounds)", tuple(gbp(g.salary) for g in guarantee))]
    if any(g.equalised_items for g in guarantee):
        rows.append(
            Row(
                "Other gross pay equalised like salary",
                tuple(gbp(g.equalised_items) for g in guarantee),
            )
        )
    rows += [
        Row(
            f"Less hypothetical Turkish tax ({how})",
            tuple(gbp(g.hypothetical_tax) for g in guarantee),
        ),
        Row("Net salary", tuple(gbp(g.net_salary) for g in guarantee)),
        Row("Allowances promised net", tuple(gbp(g.net_allowances) for g in guarantee)),
        Row(
            "Net guarantee (cash in the employee's pocket)",
            tuple(gbp(g.net_cash_target) for g in guarantee),
            strong=True,
        ),
    ]
    return rows


def _nic_text(nic_class: NicClass | None, *, home: bool) -> str:
    """The National Insurance column: the class, or why there is none."""
    if nic_class is None:
        return "None"
    if home:
        return _NO_UK_NIC
    return _NIC_LABELS.get(nic_class.value, nic_class.value)


def _treatment_note(treatment: Treatment, *, home: bool) -> str:
    if home and treatment in _HOME_SCHEME_NOTES:
        return _HOME_SCHEME_NOTES[treatment]
    return _TREATMENT_NOTES[treatment]


def _amount_text(item: ItemResult) -> str:
    if item.frequency is Frequency.MONTHLY:
        return f"{gbp(item.amount)} a month ({gbp(item.annual_amount)} a year)"
    if item.frequency is Frequency.ONE_OFF:
        return f"{gbp(item.amount)} one-off"
    return f"{gbp(item.amount)} a year"


def _parts(entries: list[tuple[int, Decimal]]) -> str:
    """``£8,000``, or ``£8,000 in year 1 and £3,000 in year 2`` when the years differ."""
    if len({amount for _, amount in entries}) == 1:
        return gbp(entries[0][1])
    parts = [f"{gbp(amount)} in year {year}" for year, amount in entries]
    return ", ".join(parts[:-1]) + " and " + parts[-1]


def _capped_rows(
    item: ItemResult, *, name: str, amount: str, home: bool
) -> list[dict[str, str]] | None:
    """A relocation under the per-move cap, one row per treatment line of its allocations.

    The engine splits each year's payment into the part exempt within the cap and the
    excess taxed as a benefit in kind (Class 1A under UK National Insurance); each part
    gets its own row with its own label and amount. ``None`` when there are no lines.
    """
    exempt: list[tuple[int, Decimal]] = []
    excess: list[tuple[int, Decimal]] = []
    labels: dict[Treatment, str] = {}
    for allocation in item.allocations:
        for line in allocation.lines:
            labels.setdefault(line.treatment, line.display_label)
            if line.amount > 0:
                target = exempt if line.treatment is Treatment.EXEMPT_CAPPED else excess
                target.append((allocation.assignment_year, line.amount))
    if not exempt and not excess:
        return None
    cap = gbp(item.cap) if item.cap is not None else "the cap"
    rows: list[dict[str, str]] = []
    if exempt:
        rows.append(
            {
                "name": name,
                "amount": amount,
                "years": year_list(tuple(year for year, _ in exempt)),
                "label": labels.get(Treatment.EXEMPT_CAPPED, display_label(Treatment.EXEMPT)),
                "part": _parts(exempt),
                "treatment": Treatment.EXEMPT_CAPPED.value,
                "nic": "None",
                "how": (
                    f"Exempt from tax and National Insurance within the {cap} cap per move."
                    if excess
                    else f"Within the {cap} cap per move, so all of it is exempt."
                ),
            }
        )
    if excess:
        lead = (
            f"Above the {cap} cap per move"
            if exempt
            else f"None of the {cap} exemption per move is left for it"
        )
        rows.append(
            {
                "name": f"{name} (above the cap)" if exempt else name,
                "amount": "" if exempt else amount,
                "years": year_list(tuple(year for year, _ in excess)),
                "label": labels.get(Treatment.TAXABLE_BIK, display_label(Treatment.TAXABLE_BIK)),
                "part": _parts(excess),
                "treatment": Treatment.TAXABLE_BIK.value,
                "nic": _NO_UK_NIC if home else "Class 1A on the excess",
                "how": (
                    f"{lead}, so it is taxed as a benefit in kind inside the gross-up. "
                    + (
                        f"{_NO_UK_NIC}."
                        if home
                        else "The employee pays no National Insurance on it; the employer "
                        "pays Class 1A."
                    )
                ),
            }
        )
    return rows


def _treatments(result: CalculationResult, *, home: bool) -> list[dict[str, str]]:
    """The treatment table: salary, then each item (a capped relocation by its parts)."""
    salary = result.net_guarantee.salary
    rows = [
        {
            "name": "Base salary",
            "amount": f"{gbp(salary)} a year",
            "years": "every year",
            "label": display_label(Treatment.GROSS_EQUALISED),
            "part": "",
            "treatment": Treatment.GROSS_EQUALISED.value,
            "nic": _NO_UK_NIC if home else "Class 1",
            "how": "Equalised: the employee keeps salary less the hypothetical tax.",
        }
    ]
    for item in result.items:
        name = item.label or KIND_LABELS.get(item.kind.value, item.kind.value)
        amount = _amount_text(item)
        item_rows = None
        if item.treatment is Treatment.EXEMPT_CAPPED:
            item_rows = _capped_rows(item, name=name, amount=amount, home=home)
        if item_rows is None:
            item_rows = [
                {
                    "name": name,
                    "amount": amount,
                    "years": year_list(item.years),
                    "label": item.display_label,
                    "part": "",
                    "treatment": item.treatment.value,
                    "nic": _nic_text(item.nic_class, home=home),
                    "how": _treatment_note(item.treatment, home=home),
                }
            ]
        if item.treatment_overridden:
            item_rows[0]["how"] += (
                " (Treatment chosen in the inputs, not the default for this kind.)"
            )
        rows += item_rows
    return rows


def _gross_up(result: CalculationResult) -> dict[str, Any]:
    steps = {
        step.assignment_year: step
        for step in result.trace
        if step.step == "gross_up" and step.assignment_year is not None
    }
    rows = []
    for label, key, kind in _GROSS_UP_ROWS:
        values = []
        for year in result.years:
            value = (
                steps[year.assignment_year].values.get(key, "")
                if year.assignment_year in steps
                else ""
            )
            if kind == "money":
                value = gbp(value)
            elif kind == "percent":
                value = percent(value)
            values.append(value)
        rows.append(Row(label, tuple(values)))
    first = result.years[0].gross_up
    return {
        "rows": rows,
        "step_ids": [
            steps[y.assignment_year].id if y.assignment_year in steps else "" for y in result.years
        ],
        "income_tax_rate": percent(first.income_tax_marginal_rate),
        "nic_rate": percent(first.nic_marginal_rate),
    }


def _employer_charges(result: CalculationResult) -> list[Row]:
    codes = [LineCode.EMPLOYER_NIC, LineCode.CLASS_1A, LineCode.HOME_EMPLOYER_SOCIAL_SECURITY]
    return [_line_row(result, code) for code in codes if _present(result.years, code)]


def _year_table(result: CalculationResult) -> list[Section]:
    years = result.years
    total_row = _line_row(result, LineCode.TOTAL_EMPLOYER_COST, strong=True)
    employer = Section(
        title="What the employer pays",
        note="Gross cash, employer charges and the cost of benefits and exempt items.",
        rows=tuple(_line_row(result, c) for c in _EMPLOYER_VIEW if _present(years, c)),
        total=total_row,
    )
    recipient = Section(
        title="Where the money goes",
        note=(
            "The same total split by who receives it: the employee, HMRC (income tax and "
            "National Insurance) and the providers of benefits."
        ),
        rows=tuple(_line_row(result, c) for c in _RECIPIENT_VIEW if _present(years, c)),
        total=Row(
            "Total (second decomposition)",
            tuple(gbp(y.decomposition.recipient_view) for y in years),
            total=gbp(result.totals.total_employer_cost),
            strong=True,
        ),
        extra={"foots": all(y.decomposition.foots for y in years)},
    )
    other = Section(
        title="Other figures",
        note="Not added up across years.",
        rows=tuple(
            Row(r.label, r.values, "", r.code)
            for r in (_line_row(result, c) for c in _OTHER_FIGURES if _present(years, c))
        ),
    )
    return [employer, recipient, other]


def _trace(result: CalculationResult) -> list[dict[str, Any]]:
    return [
        {
            "id": step.id,
            "title": _STEP_TITLES.get(step.step, step.step.replace("_", " ").capitalize()),
            "step": step.step,
            "year": step.assignment_year,
            "description": step.description,
            "refs": list(step.refs),
            "values": [(key.replace("_", " "), value) for key, value in step.values.items()],
        }
        for step in result.trace
    ]


def immigration_view(panel: dict[str, Any]) -> dict[str, Any]:
    """Lookups the panel template needs but cannot do itself (titles by id, subtotals)."""
    costs: dict[str, Any] = panel["costs"]
    timeline: dict[str, Any] = panel["timeline"]
    titles = {stage["id"]: stage["title"] for stage in timeline["stages"]}
    titles.update({stage["id"]: stage["title"] for stage in timeline["not_applicable"]})
    stages = [
        {
            **stage,
            "depends_on_titles": [titles.get(i, i) for i in stage["depends_on"]],
            "parallel_with_titles": [titles.get(i, i) for i in stage["parallel_with"]],
        }
        for stage in timeline["stages"]
    ]
    cost_labels = {line["id"]: line["label"] for line in costs["lines"]}
    return {
        "subtotals": [
            {"key": key, "label": label, "display": costs["subtotals_display"][key]}
            for key, label in costs["subtotal_labels"].items()
        ],
        "listed_separately": [cost_labels.get(i, i) for i in costs["listed_separately"]],
        "stages": stages,
        "critical_path_titles": [titles.get(i, i) for i in timeline["critical_path"]],
        "phases": [
            {"phase": phase["phase"], "titles": [titles.get(i, i) for i in phase["stages"]]}
            for phase in timeline["phases"]
        ],
    }


def results_context(
    result: CalculationResult,
    *,
    token: str,
    panel: dict[str, Any],
    tailoring_problems: Sequence[str],
    narrative: Sequence[str],
    social_security: SocialSecurityMode | None = None,
    notices: Sequence[str] = (),
) -> dict[str, Any]:
    """Everything ``web/results.html`` shows, in the reference pack's order.

    ``social_security`` is the scenario's mode; when not given it is read from the
    result (the Turkish employer line is present only in home-scheme mode).
    """
    years = result.years
    if social_security is None:
        social_security = (
            SocialSecurityMode.HOME_SCHEME_AGREEMENT
            if _present(years, LineCode.HOME_EMPLOYER_SOCIAL_SECURITY)
            else SocialSecurityMode.UK_NIC
        )
    home = social_security is SocialSecurityMode.HOME_SCHEME_AGREEMENT
    return {
        "result": result,
        "token": token,
        "notices": list(notices),
        "year_headers": [f"Year {y.assignment_year} ({y.uk_tax_year})" for y in years],
        "year_count": len(years),
        "total_header": (
            f"{_NUMBER_WORDS.get(len(years), str(len(years)))}-year total"
            if len(years) > 1
            else "Total"
        ),
        "headline": _headline(result),
        "flags": _flags(result),
        "net_guarantee": _net_guarantee(result),
        "hypothetical_tax": result.hypothetical_tax,
        "treatments": _treatments(result, home=home),
        "gross_up": _gross_up(result),
        "employer_charges": _employer_charges(result),
        "home_scheme": home,
        "year_table": _year_table(result),
        "assumptions": list(result.assumptions),
        "panel": panel,
        "immigration": immigration_view(panel),
        "tailoring_problems": list(tailoring_problems),
        "narrative": list(narrative),
        "trace": _trace(result),
        "provenance": list(result.provenance),
    }
