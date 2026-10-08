"""Narrators turn a calculation result into short plain-English paragraphs.

A narrator describes; it never computes. :class:`TemplateNarrator` reads only the result
object, and every number it states is a figure the result already holds (tested with
:func:`teq_web.narration.figures.unknown_numbers`). An optional generated narrator would
implement the same :class:`Narrator` protocol and be held to the same check.

Claims are conditional on the figures: a charge is mentioned only when its line is
non-zero in the year described, and items are placed in the years they are paid in.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Final, Protocol

from teq_engine import CalculationResult, LineCode, Treatment, YearResult
from teq_engine.types import ItemResult
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


def _home_scheme(result: CalculationResult) -> bool:
    """Whether the employee stays in the Turkish scheme (the engine adds its own line)."""
    return any(year.has_line(LineCode.HOME_EMPLOYER_SOCIAL_SECURITY) for year in result.years)


def _cost_drivers(year: YearResult) -> list[str]:
    """What the employer does that costs more than the salary, from the non-zero lines.

    Each driver completes "the employer ...".
    """
    drivers = []
    if _line(year, LineCode.INCOME_TAX):
        taxes = "the UK income tax"
        if _line(year, LineCode.EMPLOYEE_NIC):
            taxes += " and employee National Insurance"
        drivers.append(f"promises a net figure and pays {taxes} on top of it")
    if _line(year, LineCode.EMPLOYER_NIC):
        drivers.append("adds its own National Insurance")
    if _line(year, LineCode.HOME_EMPLOYER_SOCIAL_SECURITY):
        drivers.append("keeps paying Turkish employer social security")
    if _line(year, LineCode.BENEFIT_COST) or _line(year, LineCode.EXEMPT_COST):
        drivers.append("pays for the benefits as well")
    return drivers


def _benefit_name(item: ItemResult) -> str:
    """How the taxable part of an item is named: a capped relocation by its excess."""
    name = _item_name(item.label, item.kind.value)
    if item.treatment is Treatment.EXEMPT_CAPPED:
        name = (name if name.startswith("the ") else f"the {name}") + " above the cap"
    return name


def _amounts_by_year(entries: list[tuple[int, Decimal]]) -> str:
    """``£2,000``, or ``£2,000 in year 1 and £3,000 in year 2`` over several years."""
    if len(entries) == 1:
        return gbp(entries[0][1])
    return _join([f"{gbp(amount)} in year {year}" for year, amount in entries])


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
        reason = ""
        drivers = _cost_drivers(first)
        if drivers and first.line(LineCode.TOTAL_EMPLOYER_COST) > first.net_guarantee.salary:
            reason = " The cost is above the salary because the employer " + _join(drivers) + "."
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
        on_top = (
            "whatever UK tax arises"
            if _home_scheme(result)
            else "whatever UK tax and National Insurance arise"
        )
        return text + (
            f" The employer bears {on_top} on top, so the employee is no better and no "
            "worse off for moving."
        )

    def _gross_up(self, result: CalculationResult) -> str:
        first = result.years[0]
        gross_up = first.gross_up
        home = _home_scheme(result)
        after = "income tax" if home else "income tax and employee National Insurance"
        if _line(first, LineCode.INCOME_TAX):
            text = (
                "Because the employer pays the employee's UK tax, that payment is itself "
                "taxable pay, so tax is due on the tax. The gross pay is therefore the "
                f"figure that leaves exactly the net guarantee after {after}, and the tool "
                "solves for it exactly rather than by trial and error."
            )
        else:
            text = (
                "The employer would pay any UK tax on the employee's pay, so the gross pay "
                f"is the figure that leaves exactly the net guarantee after {after}."
            )
        if gross_up.marginal_rate > 0:
            income_tax = percent(gross_up.income_tax_marginal_rate)
            if gross_up.nic_marginal_rate > 0:
                rates = (
                    f"income tax is {income_tax} and employee National Insurance "
                    f"{percent(gross_up.nic_marginal_rate)}, a combined marginal rate of "
                    f"{percent(gross_up.marginal_rate)}"
                )
            elif home:
                rates = f"income tax is {income_tax} and no UK National Insurance applies"
            else:
                rates = (
                    f"income tax is {income_tax} and no employee National Insurance is due on them"
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
        """The taxable benefits of year 1 by item, then any that start later."""
        first = result.years[0]
        year_one: list[tuple[str, Decimal]] = []
        later: list[tuple[str, tuple[int, ...]]] = []
        for item in result.items:
            by_year = {
                allocation.assignment_year: allocation.taxable_benefit
                for allocation in item.allocations
                if allocation.taxable_benefit > 0
            }
            if first.assignment_year in by_year:
                year_one.append((_benefit_name(item), by_year[first.assignment_year]))
            elif by_year:
                later.append((_benefit_name(item), tuple(sorted(by_year))))
        if not year_one and not later:
            return None
        text = ""
        if year_one:
            plural = len(year_one) > 1
            verb, pronoun = ("are", "their") if plural else ("is", "its")
            kind = "taxable benefits in kind" if plural else "a taxable benefit in kind"
            if plural:
                what = _join([f"{name} at {gbp(value)}" for name, value in year_one])
                value = f"their combined value of {gbp(first.gross_up.taxable_benefits)}"
            else:
                what, value = year_one[0][0], f"its value of {gbp(year_one[0][1])}"
            text = (
                f"{_capitalise(what)} {verb} not cash pay, but {kind}: {value} in year 1 is "
                f"added to taxable pay, which becomes {gbp(first.line(LineCode.TAXABLE_PAY))}. "
                "That raises the income tax the employer must cover, and the extra tax is "
                "itself grossed up."
            )
            class_1a = _line(first, LineCode.CLASS_1A)
            if class_1a:
                text += (
                    " The employee pays no National Insurance on a benefit; instead the "
                    f"employer pays Class 1A National Insurance on {pronoun} value, "
                    f"{gbp(class_1a)} in year 1."
                )
            elif _home_scheme(result):
                text += (
                    " No Class 1A National Insurance is due while the employee stays in the "
                    "Turkish scheme."
                )
        if later:
            parts = _join([f"{name} in {year_list(years)}" for name, years in later])
            plural = len(later) > 1
            if year_one:
                kind = "taxable benefits in kind" if plural else "a taxable benefit in kind"
                text += (
                    f" Later in the assignment, {parts} {'are' if plural else 'is'} also {kind}."
                )
            else:
                kind = "taxable benefits in kind" if plural else "a taxable benefit in kind"
                text = (
                    f"{_capitalise(parts)} {'are' if plural else 'is'} not cash pay, but {kind}: "
                    "the value is added to taxable pay in the year it is paid, which raises the "
                    "income tax the employer must cover."
                )
        return text

    def _relocation(self, result: CalculationResult) -> str | None:
        """Each relocation under the cap: what is exempt, what is taxed, and when."""
        moves = [item for item in result.items if item.treatment is Treatment.EXEMPT_CAPPED]
        if not moves:
            return None
        last_year = result.years[-1].assignment_year
        class_1a_years = {y.assignment_year for y in result.years if _line(y, LineCode.CLASS_1A)}
        sentences = []
        for item in moves:
            name = _capitalise(_item_name(item.label, item.kind.value))
            exempt = [(a.assignment_year, a.exempt) for a in item.allocations if a.exempt > 0]
            excess = [
                (a.assignment_year, a.taxable_benefit)
                for a in item.allocations
                if a.taxable_benefit > 0
            ]
            cap = f" up to {gbp(item.cap)} per move" if item.cap is not None else ""
            sentence = f"{name} of {gbp(item.annual_amount)}"
            if not excess:
                sentence += (
                    f" is exempt from tax and National Insurance{cap}, so it costs the "
                    "employer only its face value"
                )
            else:
                if exempt:
                    verb = "is" if len(excess) == 1 else "are"
                    sentence += (
                        f" is exempt from tax and National Insurance{cap}; "
                        f"{_amounts_by_year(excess)} {verb} above the cap and taxed as a benefit"
                    )
                elif item.cap is not None:
                    sentence += (
                        f" comes after the {gbp(item.cap)} exemption per move is used up, so "
                        "all of it is taxed as a benefit"
                    )
                else:
                    sentence += " is taxed as a benefit"
                years = [year for year, _ in excess]
                with_class_1a = [year for year in years if year in class_1a_years]
                if with_class_1a == years:
                    sentence += ", with Class 1A"
                elif with_class_1a:
                    sentence += f", with Class 1A in {year_list(tuple(with_class_1a))}"
            sentence += f". It is paid in {year_list(item.years)}"
            if item.years != "ALL" and max(item.years) < last_year:
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
        parts = []
        employee = _line(first, LineCode.EMPLOYEE_NIC)
        employer = _line(first, LineCode.EMPLOYER_NIC)
        if employee:
            parts.append(f"{gbp(employee)} from the employee (which the employer grosses up)")
        if employer:
            parts.append(f"{gbp(employer)} from the employer")
        if parts:
            text = f"UK National Insurance is included: {_join(parts)} in year 1."
        else:
            text = "UK National Insurance applies, but none is due in year 1 at this level of pay."
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
