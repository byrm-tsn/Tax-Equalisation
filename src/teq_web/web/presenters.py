"""Turn a calculation result into the rows the results page shows, in the pack's order.

Presentation only: every figure comes straight from the result (lines, totals, the net
guarantee, items and the trace); nothing is recomputed here.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Final

from teq_engine import CATALOGUE, CalculationResult, Code, LineCode, Treatment, YearResult
from teq_engine.treatments import display_label
from teq_engine.types import Frequency
from teq_web.formatting import gbp, percent, year_list
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

_NIC_LABELS: Final = {"CLASS_1": "Class 1", "CLASS_1A": "Class 1A"}

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
    flags = []
    for warning in sorted(result.warnings, key=lambda w: _SEVERITY_ORDER.get(w.severity, 9)):
        try:
            title = CATALOGUE[Code(warning.code)].title
        except (KeyError, ValueError):
            title = warning.code
        flags.append(
            {
                "code": warning.code,
                "severity": warning.severity,
                "title": title,
                "text": warning.text,
                "question": warning.question,
                "year": warning.assignment_year,
            }
        )
    return flags


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


def _treatments(result: CalculationResult) -> list[dict[str, str]]:
    salary = result.net_guarantee.salary
    rows = [
        {
            "name": "Base salary",
            "amount": f"{gbp(salary)} a year",
            "years": "every year",
            "label": display_label(Treatment.GROSS_EQUALISED),
            "treatment": Treatment.GROSS_EQUALISED.value,
            "nic": "Class 1",
            "how": "Equalised: the employee keeps salary less the hypothetical tax.",
        }
    ]
    for item in result.items:
        if item.frequency is Frequency.MONTHLY:
            amount = f"{gbp(item.amount)} a month ({gbp(item.annual_amount)} a year)"
        elif item.frequency is Frequency.ONE_OFF:
            amount = f"{gbp(item.amount)} one-off"
        else:
            amount = f"{gbp(item.amount)} a year"
        how = _TREATMENT_NOTES[item.treatment]
        if item.treatment is Treatment.EXEMPT_CAPPED and item.cap is not None:
            if item.excess:
                how = (
                    f"Exempt up to {gbp(item.cap)} per move: {gbp(item.exempt)} exempt, "
                    f"{gbp(item.excess)} above the cap taxed as a benefit with Class 1A."
                )
            else:
                how = f"Within the {gbp(item.cap)} cap per move, so all of it is exempt."
        if item.treatment_overridden:
            how += " (Treatment chosen in the inputs, not the default for this kind.)"
        rows.append(
            {
                "name": item.label or KIND_LABELS.get(item.kind.value, item.kind.value),
                "amount": amount,
                "years": year_list(item.years),
                "label": item.display_label,
                "treatment": item.treatment.value,
                "nic": _NIC_LABELS.get(item.nic_class.value, "") if item.nic_class else "None",
                "how": how,
            }
        )
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
) -> dict[str, Any]:
    """Everything ``web/results.html`` shows, in the reference pack's order."""
    years = result.years
    return {
        "result": result,
        "token": token,
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
        "treatments": _treatments(result),
        "gross_up": _gross_up(result),
        "employer_charges": _employer_charges(result),
        "home_scheme": _present(years, LineCode.HOME_EMPLOYER_SOCIAL_SECURITY),
        "year_table": _year_table(result),
        "assumptions": list(result.assumptions),
        "panel": panel,
        "immigration": immigration_view(panel),
        "tailoring_problems": list(tailoring_problems),
        "narrative": list(narrative),
        "trace": _trace(result),
        "provenance": list(result.provenance),
    }
