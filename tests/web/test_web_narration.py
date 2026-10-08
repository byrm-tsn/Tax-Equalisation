"""The template narrator: plain English, from the result only, no invented numbers."""

from __future__ import annotations

from decimal import Decimal

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
}


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


def test_relocation_excess_is_explained() -> None:
    text = " ".join(TemplateNarrator().narrate(_result(**SCENARIOS["one_year"])))
    assert "£2,000 is above the cap" in text


def test_the_number_check_catches_an_invented_figure(reference_result) -> None:
    assert unknown_numbers(["The cost is £123,456.78."], reference_result) == {Decimal("123456.78")}
    assert numbers_in("47% of £1,000") == {Decimal("0.47"), Decimal("1000")}
    assert Decimal("0.47") in figure_set(reference_result)
