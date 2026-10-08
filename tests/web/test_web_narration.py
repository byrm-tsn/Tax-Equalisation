"""The template narrator: plain English, from the result only, no invented numbers."""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

import pytest

from teq_engine import REFERENCE_RATES_AS_OF, ScenarioInput, calculate, default_provider
from teq_engine.reference import reference_example_data
from teq_web.narration.figures import figure_set, numbers_in, unknown_numbers
from teq_web.narration.narrator import Narrator, TemplateNarrator


def _result(**changes: object):
    data = reference_example_data()
    data.update(changes)
    inputs = ScenarioInput.model_validate(data)
    return calculate(inputs, default_provider(), rates_as_of=REFERENCE_RATES_AS_OF)


FX = {"rate": "65.7", "as_of": "2026-10-01"}


def _items(**amounts: str) -> list[dict[str, Any]]:
    """The reference items, with amounts changed by item id."""
    items: list[dict[str, Any]] = [dict(item) for item in reference_example_data()["items"]]  # type: ignore[union-attr]
    for item in items:
        if item["id"] in amounts:
            item["amount"] = amounts[item["id"]]
    return items


YEAR_2_RELOCATION = {
    "id": "second-move",
    "kind": "RELOCATION",
    "amount": "10000.00",
    "frequency": "ONE_OFF",
    "years": [2],
}

SCENARIOS = {
    "reference": {},
    "home_scheme": {"assumptions": {"social_security": "HOME_SCHEME_AGREEMENT"}, "fx": FX},
    "calculated_tax": {
        "hypothetical_tax": {"method": "CALCULATED", "includes_social_security": True},
        "fx": FX,
    },
    "no_items": {"items": []},
    "one_year": {
        "assignment": {"length_years": 1},
        "items": [
            {
                "id": "reloc",
                "kind": "RELOCATION",
                "amount": "10000.00",
                "frequency": "ONE_OFF",
                "years": [1],
            }
        ],
    },
    "lira_salary": {
        "salary": {"amount": "6000000.00", "currency": "TRY"},
        "hypothetical_tax": {"method": "OVERRIDE", "override": "25000.00"},
        "fx": FX,
    },
    "relocation_excess": {"items": _items(relocation="10000.00")},
    "home_scheme_excess": {
        "assumptions": {"social_security": "HOME_SCHEME_AGREEMENT"},
        "fx": {"rate": "55.25", "as_of": "2026-10-01"},
        "items": _items(relocation="10000.00"),
    },
    "relocation_in_year_2": {"items": [*_items(), YEAR_2_RELOCATION]},
    "tiny_net": {"hypothetical_tax": {"method": "OVERRIDE", "override": "89999.00"}},
    "tiny_net_no_items": {
        "hypothetical_tax": {"method": "OVERRIDE", "override": "89999.00"},
        "items": [],
    },
    "ten_years": {"assignment": {"length_years": 10}},
}


def _text(name: str) -> str:
    return " ".join(TemplateNarrator().narrate(_result(**SCENARIOS[name])))


def _class_1_claims(text: str) -> list[str]:
    """Sentences that mention Class 1 or Class 1A without negating it."""
    sentences = re.split(r"(?<=[.;:])\s+", text)
    return [
        sentence
        for sentence in sentences
        if "Class 1" in sentence
        and not re.search(r"\b(no|not|none|without)\b", sentence, flags=re.IGNORECASE)
    ]


def test_the_template_narrator_satisfies_the_protocol() -> None:
    narrator: Narrator = TemplateNarrator()
    assert narrator.narrate(_result())


def test_reference_narrative_covers_the_story(reference_result) -> None:
    paragraphs = TemplateNarrator().narrate(reference_result)
    text = " ".join(paragraphs)
    assert 5 <= len(paragraphs) <= 8
    assert "2.10 times the salary of £90,000" in text
    assert "£66,000" in text  # the guarantee
    assert "tax is due on the tax" in text
    assert "£127,761.51" in text
    assert "£66,000.26" in text
    assert "Class 1A" in text
    assert "disappears from the later years" in text
    assert "social security agreement" in text
    assert "not tax advice" in text


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_every_number_is_in_the_result(name: str) -> None:
    result = _result(**SCENARIOS[name])
    paragraphs = TemplateNarrator().narrate(result)
    assert 5 <= len(paragraphs) <= 8
    assert unknown_numbers(paragraphs, result) == set()


def test_home_scheme_narrative_names_the_turkish_line() -> None:
    text = " ".join(TemplateNarrator().narrate(_result(**SCENARIOS["home_scheme"])))
    assert "Turkish employer contributions continue" in text
    assert "certificate of coverage" in text


@pytest.mark.parametrize("name", ["home_scheme", "home_scheme_excess"])
def test_home_scheme_narrative_makes_no_uk_national_insurance_claims(name: str) -> None:
    text = _text(name)
    assert _class_1_claims(text) == []
    assert "so no UK National Insurance or Class 1A is due" in text
    assert "No Class 1A National Insurance is due while the employee stays" in text
    assert "employee National Insurance on top" not in text
    assert "adds its own National Insurance" not in text
    assert "after income tax and employee National Insurance" not in text
    assert "is Turkish employer social security" in text
    assert "£0" not in text


def test_home_scheme_relocation_excess_has_no_class_1a() -> None:
    text = _text("home_scheme_excess")
    assert "£2,000 is above the cap and taxed as a benefit. It is paid in year 1" in text
    assert "the relocation above the cap at £2,000" in text


def test_relocation_excess_is_explained() -> None:
    text = " ".join(TemplateNarrator().narrate(_result(**SCENARIOS["one_year"])))
    assert "£2,000 is above the cap" in text


def test_relocation_excess_names_each_benefit_with_its_value() -> None:
    text = _text("relocation_excess")
    assert (
        "The housing (rent paid by the employer) at £30,000 and the relocation above the cap "
        "at £2,000 are not cash pay, but taxable benefits in kind: their combined value of "
        "£32,000 in year 1 is added to taxable pay"
    ) in text
    assert "Class 1A National Insurance on their value, £4,800 in year 1" in text
    assert "£2,000 is above the cap and taxed as a benefit, with Class 1A" in text


def test_items_are_placed_in_the_years_they_are_paid() -> None:
    paragraphs = TemplateNarrator().narrate(_result(**SCENARIOS["relocation_in_year_2"]))
    benefits = next(p for p in paragraphs if "taxable benefit in kind" in p)
    year_one = benefits.split(" Later in the assignment")[0]
    assert year_one.startswith("The housing (rent paid by the employer) is not cash pay")
    assert "relocation" not in year_one
    assert "its value of £30,000 in year 1" in year_one
    assert "Later in the assignment, the relocation above the cap in year 2 is also" in benefits
    relocation = next(p for p in paragraphs if p.startswith("The relocation of £8,000"))
    first, second = relocation.split(". Relocation of £10,000")
    assert "It is paid in year 1, which is why it disappears from the later years' totals" in first
    assert "comes after the £8,000 exemption per move is used up" in second
    assert "with Class 1A" in second
    assert second.endswith("It is paid in year 2.")
    assert "disappears" not in second


def test_cost_below_the_salary_is_not_called_above_it() -> None:
    text = _text("tiny_net")
    assert "0.62 times the salary" in text
    assert "The cost is above the salary" not in text


def test_no_income_tax_means_no_claim_that_the_employer_pays_it() -> None:
    text = _text("tiny_net_no_items")
    assert "income tax on top" not in text
    assert "tax is due on the tax" not in text
    assert "The cost is above the salary" not in text
    assert "UK National Insurance applies, but none is due in year 1" in text
    assert "£0" not in text


def test_national_insurance_sentences_skip_zero_lines() -> None:
    text = _text("tiny_net")
    assert "UK National Insurance is included: £1,029 from the employer in year 1." in text
    assert "from the employee" not in text


def test_the_number_check_ignores_digits_in_user_labels() -> None:
    items = [
        {"id": "flat", "kind": "HOUSING", "label": "Flat 4321", "amount": "30000.00"},
        {"id": "move", "kind": "RELOCATION", "label": "Move 2468", "amount": "8000.00"}
        | {"frequency": "ONE_OFF", "years": [1]},
    ]
    result = _result(items=items, fx={"rate": "65.7", "as_of": "2026-10-01", "source": "Feed 9753"})
    paragraphs = TemplateNarrator().narrate(result)
    text = " ".join(paragraphs)
    assert "the flat 4321" in text.lower()
    assert "move 2468" in text.lower()
    assert unknown_numbers(paragraphs, result) == set()
    # A label's digits cannot vouch for an invented figure.
    assert unknown_numbers(["The cost is £4,321 and £9,753."], result) == {
        Decimal("4321"),
        Decimal("9753"),
    }
    assert Decimal("4321") not in figure_set(result)


def test_the_number_check_catches_an_invented_figure(reference_result) -> None:
    assert unknown_numbers(["The cost is £123,456.78."], reference_result) == {Decimal("123456.78")}
    assert numbers_in("47% of £1,000") == {Decimal("0.47"), Decimal("1000")}
    assert Decimal("0.47") in figure_set(reference_result)


# --------------------------------------------------------------------------- immigration


def _panel(**answers: object) -> dict[str, Any]:
    import dataclasses
    from datetime import date

    from teq_guidance import TailoringAnswers, build_panel

    tailored = dataclasses.replace(TailoringAnswers.reference_example(), **answers)  # type: ignore[arg-type]
    return build_panel(tailored, as_of=date(2026, 10, 8))


PANELS = {
    "reference": {},
    "no_licence_with_family": {
        "sponsor_licence_held": False,
        "dependants_adults": 1,
        "dependants_children": 2,
    },
    "inside_uk_long_visa": {
        "application_location": "inside_uk",
        "visa_length_years": 4,
        "tb_listed_resident": False,
        "english_evidence": "degree_taught_in_english",
        "sponsor_certifies_maintenance": True,
        "dependants_children": 1,
    },
    "small_sponsor_partner": {"sponsor_size": "small_or_charitable", "dependants_adults": 1},
    "six_year_visa": {"visa_length_years": 6},
}


def test_the_gross_up_sentence_describes_the_packs_iteration(reference_result) -> None:
    text = " ".join(TemplateNarrator().narrate(reference_result))
    assert "trial and error" not in text
    assert (
        "the tool finds that figure exactly and checks it against the step-by-step "
        "iteration the reference pack describes."
    ) in text


def test_the_immigration_narrative_puts_the_employer_first() -> None:
    paragraphs = TemplateNarrator().narrate_immigration(_panel())
    assert len(paragraphs) == 4
    employer, application, costs, family = paragraphs
    assert employer.startswith("The employer moves first. It already holds a sponsor licence")
    assert "Certificate of Sponsorship" in employer
    assert "eligible occupation at RQF level 6 (graduate level)" in employer
    assert "at least £41,700 a year or the occupation's going rate" in employer
    assert "applies online from outside the UK" in application
    assert "the Secure English Language Test result at B2, the TB test certificate" in application
    assert "typically takes 15 to 21 days" in application
    assert "about 6 to 16 weeks (43 to 112 days)" in application
    assert costs.startswith(
        "The employer must pay £3,165 itself: the Certificate of Sponsorship fee of £525 "
        "and the Immigration Skills Charge of £2,640."
    )
    assert "It cannot pass these to the employee." in costs
    assert "come to £2,889" in costs
    assert "many employers pay by policy" in costs
    assert "None of these immigration costs is included in the employment cost." in costs
    assert family.startswith("No partner or children are applying as dependants")


def test_the_immigration_narrative_follows_the_answers() -> None:
    employer, _, costs, family = TemplateNarrator().narrate_immigration(
        _panel(**PANELS["no_licence_with_family"])  # type: ignore[arg-type]
    )
    assert "It does not yet hold a sponsor licence, so it applies for one" in employer
    assert "the sponsor licence fee of £1,682" in costs
    assert "£4,847" in costs
    assert family.startswith("A partner and two children are applying as dependants")
    assert "the Immigration Health Surcharge (children) of £3,104" in family
    assert "The funds to show rise to £2,070" in family


def test_a_fee_the_pack_does_not_hold_is_named_not_guessed() -> None:
    _, _, costs, family = TemplateNarrator().narrate_immigration(
        _panel(**PANELS["inside_uk_long_visa"])  # type: ignore[arg-type]
    )
    assert "is not held by this tool: check it on GOV.UK" in costs
    assert "One child is applying as a dependant" in family
    assert "of Not held" not in family


@pytest.mark.parametrize("name", sorted(PANELS))
def test_every_immigration_number_is_in_the_panel(name: str) -> None:
    from teq_web.narration.figures import unknown_panel_numbers

    panel = _panel(**PANELS[name])  # type: ignore[arg-type]
    paragraphs = TemplateNarrator().narrate_immigration(panel)
    assert unknown_panel_numbers(paragraphs, panel) == set()


def test_typed_answers_cannot_vouch_for_a_number() -> None:
    from teq_web.narration.figures import panel_figure_set, unknown_panel_numbers

    panel = _panel(soc_code="9876", nationality="Atlantis 5432")
    figures = panel_figure_set(panel)
    assert Decimal("9876") not in figures
    assert Decimal("5432") not in figures
    assert unknown_panel_numbers(["The fee is £9,876 or £5,432."], panel) == {
        Decimal("9876"),
        Decimal("5432"),
    }
    assert unknown_panel_numbers(["The charge is £2,640."], panel) == set()
    assert Decimal("3165") in figures  # a subtotal
    assert Decimal("112") in figures  # the timeline's longest total in days
