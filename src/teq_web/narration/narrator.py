"""Narrators turn a calculation result into short plain-English paragraphs.

A narrator describes; it never computes. :class:`TemplateNarrator` reads only the result
object, and every number it states is a figure the result already holds (tested with
:func:`teq_web.narration.figures.unknown_numbers`). An optional generated narrator would
implement the same :class:`Narrator` protocol and be held to the same check.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Final, Protocol

from teq_engine import CalculationResult, LineCode, Treatment, YearResult
from teq_web.formatting import gbp, percent, uk_date, year_list

__all__ = ["Narrator", "TemplateNarrator"]

_KIND_NAMES: Final = {
    "BONUS": "the bonus",
    "COLA": "the cost-of-living allowance",
    "HOUSING": "the housing",
    "RELOCATION": "relocation",
    "SCHOOL_FEES": "school fees",
    "HOME_LEAVE": "home leave",
    "PENSION_EMPLOYER": "the employer pension contribution",
    "OTHER": "the other item",
}

_WHERE_IT_GOES: Final = (
    (LineCode.NET_CASH, "reaches the employee as net cash"),
    (LineCode.INCOME_TAX, "is income tax"),
    (LineCode.EMPLOYEE_NIC, "is employee National Insurance"),
    (LineCode.EMPLOYER_NIC, "is employer National Insurance"),
    (LineCode.CLASS_1A, "is Class 1A National Insurance on benefits"),
    (LineCode.BENEFIT_COST, "is the cost of taxable benefits"),
    (LineCode.EXEMPT_COST, "is the cost of exempt items"),
    (LineCode.HOME_EMPLOYER_SOCIAL_SECURITY, "is Turkish employer social security"),
)

_NUMBER_WORDS: Final = {
    1: "one",
    2: "two",
    3: "three",
    4: "four",
    5: "five",
    6: "six",
    7: "seven",
    8: "eight",
    9: "nine",
    10: "ten",
}


class Narrator(Protocol):
    """Anything that can describe a result in plain English."""

    def narrate(self, result: CalculationResult) -> list[str]:
        """Return short paragraphs describing ``result``; never compute new figures."""
        ...


def _join(parts: list[str]) -> str:
    if len(parts) <= 1:
        return "".join(parts)
    return ", ".join(parts[:-1]) + " and " + parts[-1]


def _line(year: YearResult, code: LineCode) -> Decimal | None:
    return year.line(code) if year.has_line(code) else None


def _item_name(label: str | None, kind: str) -> str:
    """``Housing (rent paid by the employer)`` -> ``the housing (rent paid by the employer)``."""
    if not label:
        return _KIND_NAMES.get(kind, "the item")
    text = label if label[:2].isupper() else label[0].lower() + label[1:]
    return text if text.startswith("the ") else f"the {text}"


def _cost_drivers(year: YearResult) -> list[str]:
    """Why a year costs more than the salary, from the lines that are present."""
    taxes = "the UK income tax"
    if _line(year, LineCode.EMPLOYEE_NIC):
        taxes += " and employee National Insurance"
    drivers = [f"the employer promises a net figure and pays {taxes} on top of it"]
    if _line(year, LineCode.EMPLOYER_NIC):
        drivers.append("adds its own National Insurance")
    if _line(year, LineCode.HOME_EMPLOYER_SOCIAL_SECURITY):
        drivers.append("keeps paying Turkish employer social security")
    if _line(year, LineCode.BENEFIT_COST) or _line(year, LineCode.EXEMPT_COST):
        drivers.append("pays for the benefits as well")
    return drivers


def _capitalise(text: str) -> str:
    return text[:1].upper() + text[1:]


class TemplateNarrator:
    """The default narrator: fixed sentence templates filled from the result."""

    def narrate(self, result: CalculationResult) -> list[str]:
        """Five to eight paragraphs, in the order a reader needs them."""
        paragraphs = [
            self._what_it_costs(result),
            self._guarantee(result),
            self._gross_up(result),
        ]
        for optional in (self._benefits(result), self._relocation(result)):
            if optional:
                paragraphs.append(optional)
        paragraphs.append(self._social_security(result))
        paragraphs.append(self._assumptions(result))
        return paragraphs

    # ------------------------------------------------------------------ paragraphs

    def _what_it_costs(self, result: CalculationResult) -> str:
        years = result.years
        first = years[0]
        by_year = _join(
            [
                f"{gbp(y.line(LineCode.TOTAL_EMPLOYER_COST))} in year {y.assignment_year}"
                for y in years
            ]
        )
        if len(years) == 1:
            opening = (
                f"The employer pays {gbp(result.totals.total_employer_cost)} for this "
                "one-year assignment."
            )
        else:
            words = _NUMBER_WORDS.get(len(years), "all")
            opening = (
                f"Over the {words} years of the assignment the employer pays "
                f"{gbp(result.totals.total_employer_cost)}: {by_year}."
            )
        multiple = _line(first, LineCode.MULTIPLE_OF_SALARY)
        if multiple is not None:
            opening += (
                f" In year 1 that is {multiple} times the salary of "
                f"{gbp(result.net_guarantee.salary)}."
            )
        parts = []
        for code, phrase in _WHERE_IT_GOES:
            amount = _line(first, code)
            if amount:
                parts.append(f"{gbp(amount)} {phrase}")
        reason = " The cost is above the salary because " + _join(_cost_drivers(first)) + "."
        split = (
            f" Of the {gbp(first.line(LineCode.TOTAL_EMPLOYER_COST))} in year 1, {_join(parts)}."
        )
        return opening + reason + split

    def _guarantee(self, result: CalculationResult) -> str:
        guarantee = result.net_guarantee
        hypo = result.hypothetical_tax
        if hypo.mode == "OVERRIDE":
            how = "a figure supplied as an assumption rather than calculated"
            if hypo.calculated_for_comparison is not None:
                how += (
                    f"; under the Turkish rules it would be {gbp(hypo.calculated_for_comparison)}"
                )
        elif hypo.components is not None and hypo.fx is not None:
            how = (
                f"calculated under the {hypo.components.tax_year} Turkish rules at "
                f"{hypo.fx.rate.normalize():f} lira to the pound"
            )
        else:
            how = "calculated under the Turkish rules"
        converted = (
            " (converted from lira at the exchange rate supplied)"
            if "SALARY_CONVERTED_AT_SNAPSHOT_FX" in result.warning_codes()
            else ""
        )
        text = (
            "Tax equalisation promises the employee the same net pay as if they had stayed "
            f"at home. The salary of {gbp(guarantee.salary)}{converted}"
        )
        if guarantee.equalised_items:
            text += f", plus {gbp(guarantee.equalised_items)} of other gross pay,"
        text += (
            f" less a hypothetical Turkish tax of {gbp(guarantee.hypothetical_tax)} ({how}) "
            f"leaves a net salary of {gbp(guarantee.net_salary)}."
        )
        net_items = [
            _item_name(item.label, item.kind.value)
            for item in result.items
            if item.treatment is Treatment.NET_CASH
        ]
        per_year = (
            "a year"
            if len({y.net_guarantee.net_cash_target for y in result.years}) == 1
            else "in year 1"
        )
        if guarantee.net_allowances:
            text += (
                f" Adding {_join(net_items) or 'the allowances'}, promised net at "
                f"{gbp(guarantee.net_allowances)}, gives a net guarantee of "
                f"{gbp(guarantee.net_cash_target)} {per_year}."
            )
        else:
            when = "" if per_year == "a year" else " in year 1"
            text += f" With no allowance promised net, that is the net guarantee{when}."
        return text + (
            " The employer bears whatever UK tax and National Insurance arise on top, so "
            "the employee is no better and no worse off for moving."
        )

    def _gross_up(self, result: CalculationResult) -> str:
        first = result.years[0]
        gross_up = first.gross_up
        text = (
            "Because the employer pays the employee's UK tax, that payment is itself "
            "taxable pay, so tax is due on the tax. The gross pay is therefore the figure "
            "that leaves exactly the net guarantee after income tax and employee National "
            "Insurance, and the tool solves for it exactly rather than by trial and error."
        )
        if gross_up.marginal_rate > 0:
            if gross_up.nic_marginal_rate > 0:
                rates = (
                    f"income tax is {percent(gross_up.income_tax_marginal_rate)} and employee "
                    f"National Insurance {percent(gross_up.nic_marginal_rate)}, a combined "
                    f"marginal rate of {percent(gross_up.marginal_rate)}"
                )
            else:
                rates = (
                    f"income tax is {percent(gross_up.income_tax_marginal_rate)} and no UK "
                    "National Insurance applies"
                )
            text += f" In year 1 the last pounds of pay fall where {rates},"
        else:
            text += " In year 1 no tax falls on the last pounds of pay,"
        text += (
            f" so {gbp(gross_up.net_target)} net needs gross pay of {gbp(gross_up.gross_exact)}."
        )
        if gross_up.gross_rounded != gross_up.gross_exact:
            text += (
                f" It is rounded up to {gbp(gross_up.gross_rounded)} so the employee is never "
                f"short: they receive {gbp(gross_up.net_delivered)}."
            )
        marginal = _line(first, LineCode.MARGINAL_COST_PER_NET_POUND)
        if marginal is not None:
            text += f" At this level each extra pound of net pay costs the employer {gbp(marginal)}"
            text += (
                " once its own National Insurance is added."
                if _line(first, LineCode.EMPLOYER_NIC)
                else "."
            )
        return text

    def _benefits(self, result: CalculationResult) -> str | None:
        first = result.years[0]
        benefit = _line(first, LineCode.BENEFIT_COST)
        if not benefit:
            return None
        names = [
            _item_name(item.label, item.kind.value)
            for item in result.items
            if item.treatment is Treatment.TAXABLE_BIK
        ]
        if any(item.excess for item in result.items):
            names.append("the relocation above the cap")
        plural = len(names) > 1
        what = _capitalise(_join(names)) if names else "The taxable benefit"
        verb, pronoun = ("are", "their") if plural else ("is", "its")
        kind = "taxable benefits in kind" if plural else "a taxable benefit in kind"
        text = (
            f"{what} {verb} not cash pay, but {kind}: {pronoun} value of "
            f"{gbp(benefit)} in year 1 is added to taxable pay, which becomes "
            f"{gbp(first.line(LineCode.TAXABLE_PAY))}. That raises the income tax the "
            "employer must cover, and the extra tax is itself grossed up."
        )
        class_1a = _line(first, LineCode.CLASS_1A)
        if class_1a:
            text += (
                " The employee pays no National Insurance on a benefit; instead the employer "
                f"pays Class 1A National Insurance on {pronoun} value, {gbp(class_1a)} in year 1."
            )
        else:
            text += (
                " No Class 1A National Insurance is due while the employee stays in the "
                "Turkish scheme."
            )
        return text

    def _relocation(self, result: CalculationResult) -> str | None:
        moves = [item for item in result.items if item.treatment is Treatment.EXEMPT_CAPPED]
        if not moves:
            return None
        sentences = []
        for item in moves:
            name = _capitalise(_item_name(item.label, item.kind.value))
            sentence = f"{name} of {gbp(item.annual_amount)}"
            if item.cap is not None:
                sentence += (
                    f" is exempt from tax and National Insurance up to {gbp(item.cap)} per move"
                )
            else:
                sentence += " is exempt from tax and National Insurance"
            if item.excess:
                sentence += (
                    f"; {gbp(item.excess)} is above the cap and is taxed as a benefit, with "
                    "Class 1A"
                )
            else:
                sentence += ", so it costs the employer only its face value"
            sentence += f". It is paid in {year_list(item.years)}"
            if len(result.years) > 1 and item.years != "ALL":
                sentence += ", which is why it disappears from the later years' totals"
            sentences.append(sentence + ".")
        return " ".join(sentences)

    def _social_security(self, result: CalculationResult) -> str:
        first = result.years[0]
        home = _line(first, LineCode.HOME_EMPLOYER_SOCIAL_SECURITY)
        if home is not None:
            return (
                "The employee stays in the Turkish social security scheme under the UK and "
                "Turkey agreement, so no UK National Insurance or Class 1A is due. The Turkish "
                f"employer contributions continue: they are estimated at {gbp(home)} in year 1 "
                "and shown as their own line, because leaving them out would make the "
                "assignment look cheaper than it is. A certificate of coverage is needed "
                "before the assignment starts."
            )
        text = (
            "UK National Insurance is included: "
            f"{gbp(first.line(LineCode.EMPLOYEE_NIC))} from the employee (which the employer "
            f"grosses up) and {gbp(first.line(LineCode.EMPLOYER_NIC))} from the employer in "
            "year 1."
        )
        if "SOCIAL_SECURITY_AGREEMENT_MAY_APPLY" in result.warning_codes():
            text += (
                " The UK and Turkey have a social security agreement: with a certificate of "
                "coverage the employee may be able to stay in the Turkish scheme for a period. "
                "That would remove UK National Insurance and Class 1A but keep the Turkish "
                "employer contributions, so recalculate with that option before relying on "
                "either figure."
            )
        return text

    def _assumptions(self, result: CalculationResult) -> str:
        first = result.years[0]
        text = (
            "This is an illustration, not tax advice. It works in whole tax years at the "
            f"rates in force on {uk_date(result.rates_as_of)}, with year 1 as the UK tax "
            f"year {first.uk_tax_year}."
        )
        carried = [y for y in result.years if y.rates_carried_forward]
        if carried:
            labels = _join([f"{y.uk_tax_year}" for y in carried])
            text += (
                f" Rates for {labels} are not yet published, so the latest published rates "
                "are carried forward."
            )
        marginal = _line(first, LineCode.MARGINAL_COST_PER_NET_POUND)
        if result.hypothetical_tax.mode == "OVERRIDE":
            text += (
                " The hypothetical tax is the most material assumption because it sets the "
                "net guarantee"
            )
            if marginal is not None and marginal > 1:
                text += f": each pound of it moves the year 1 cost by {gbp(marginal)}"
            text += "."
        text += (
            " Every assumption is listed with a question to confirm it, and the calculation "
            "trace shows each step."
        )
        return text
