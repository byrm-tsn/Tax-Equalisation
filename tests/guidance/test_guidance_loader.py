"""The bundled pack loads, and validation rejects inconsistent packs."""

from __future__ import annotations

import dataclasses
from datetime import date
from typing import Any

import pytest

from teq_guidance import (
    GuidancePack,
    GuidancePackError,
    GuidancePackNotFoundError,
    TailoringAnswers,
    available_routes,
    load_pack,
    parse_pack,
)
from teq_guidance.loader import _decode_json
from teq_guidance.model import Basis, Payer, Verification


def _find(items: Any, item_id: str) -> dict[str, Any]:
    for item in items:
        if item["id"] == item_id:
            return item  # type: ignore[no-any-return]
    raise KeyError(item_id)


def _problems(data: dict[str, object]) -> tuple[str, ...]:
    with pytest.raises(GuidancePackError) as caught:
        parse_pack(data, expected_route="TR-GB")
    return caught.value.problems


def test_bundled_pack_loads_and_validates(pack: GuidancePack) -> None:
    assert pack.route == "TR-GB"
    assert pack.version == 1
    assert pack.verified_at == date(2026, 10, 8)
    assert pack.pack_id == "GUIDANCE:TR-GB:v1"
    assert pack.disclaimer.startswith("An illustration, not tax or immigration advice")
    assert len(pack.stages) >= 9
    assert len(pack.costs) >= 10


def test_load_pack_is_cached_and_immutable(pack: GuidancePack) -> None:
    assert load_pack("TR-GB") is pack
    with pytest.raises(dataclasses.FrozenInstanceError):
        pack.version = 2  # type: ignore[misc]


def test_routes() -> None:
    assert available_routes() == ("TR-GB",)
    with pytest.raises(GuidancePackNotFoundError, match="TR-GB"):
        load_pack("IN-GB")


def test_question_ids_are_exactly_the_tailoring_fields(pack: GuidancePack) -> None:
    from teq_guidance.tailoring import REFINEMENT_FIELDS

    # visa_length_months refines the visa_length_years question rather than being one.
    assert {"visa_length_months"} == REFINEMENT_FIELDS
    fields = {field.name for field in dataclasses.fields(TailoringAnswers)}
    assert {question.id for question in pack.questions} == fields - REFINEMENT_FIELDS


def test_every_item_cites_a_known_source(pack: GuidancePack) -> None:
    known = {source.id for source in pack.sources}
    cited: list[tuple[str, ...]] = [
        *(note.sources for note in pack.route_notes),
        *(item.sources for item in pack.requirements),
        *(item.sources for item in pack.employer_responsibilities),
        *(stage.sources for stage in pack.stages),
        *(document.sources for document in pack.documents),
        *(note.sources for note in pack.timeline_notes),
        pack.maintenance_funds.sources,
        *((cost.source,) for cost in pack.costs),
    ]
    for sources in cited:
        assert sources
        assert set(sources) <= known


def test_sources_are_official_https_pages(pack: GuidancePack) -> None:
    for source in pack.sources:
        assert source.url.startswith("https://www.gov.uk/"), source


def test_every_cost_has_basis_payer_verification_and_date(pack: GuidancePack) -> None:
    for cost in pack.costs:
        assert isinstance(cost.basis, Basis)
        assert isinstance(cost.payer, Payer)
        assert cost.verification in {
            Verification.SEARCH_CONFIRMED,
            Verification.THIRD_PARTY_REPORTED,
            Verification.FROM_KNOWLEDGE,
        }
        assert cost.verified_at == date(2026, 10, 8)


def test_employer_costs_cannot_be_recouped(pack: GuidancePack) -> None:
    for cost in pack.costs:
        if cost.payer is Payer.EMPLOYER:
            assert cost.cannot_be_recouped_from_worker, cost.id


def test_comparison_table_has_the_reference_rows(pack: GuidancePack) -> None:
    topics = [row.topic for row in pack.pack_vs_current_guidance]
    assert len(topics) == 15
    for expected in (
        "Visa decision time",
        "English requirement",
        "Tuberculosis test",
        "Immigration Skills Charge",
        "Salary requirement",
    ):
        assert expected in topics


def test_rejects_unknown_dependency(raw_pack: dict[str, Any]) -> None:
    _find(raw_pack["stages"], "assign_cos")["depends_on"].append("notary_visit")
    problems = _problems(raw_pack)
    assert any("unknown stage 'notary_visit'" in p for p in problems)


def test_rejects_dependency_cycle(raw_pack: dict[str, Any]) -> None:
    _find(raw_pack["stages"], "job_definition")["depends_on"] = ["travel_and_start"]
    problems = _problems(raw_pack)
    assert any("dependency cycle" in p for p in problems)


def test_rejects_condition_on_unknown_question(raw_pack: dict[str, Any]) -> None:
    _find(raw_pack["documents"], "tb_certificate")["condition"] = {
        "question": "has_pets",
        "equals": True,
    }
    problems = _problems(raw_pack)
    assert any("unknown question 'has_pets'" in p for p in problems)


def test_rejects_condition_on_unknown_question_inside_a_tier(raw_pack: dict[str, Any]) -> None:
    tier = _find(raw_pack["costs"], "visa_application_fee")["tiers"][0]
    tier["condition"] = [{"question": "visa_colour", "equals": "blue"}]
    problems = _problems(raw_pack)
    assert any("unknown question 'visa_colour'" in p for p in problems)


def test_rejects_condition_value_outside_the_options(raw_pack: dict[str, Any]) -> None:
    _find(raw_pack["stages"], "decision_outside_uk")["condition"] = {
        "question": "application_location",
        "equals": "on_the_moon",
    }
    problems = _problems(raw_pack)
    assert any("'on_the_moon' does not fit" in p for p in problems)


@pytest.mark.parametrize("key", ["basis", "payer"])
def test_rejects_cost_without_basis_or_payer(raw_pack: dict[str, Any], key: str) -> None:
    del _find(raw_pack["costs"], "cos_fee")[key]
    problems = _problems(raw_pack)
    assert any(f"missing {key}" in p for p in problems)


def test_rejects_unknown_basis(raw_pack: dict[str, Any]) -> None:
    _find(raw_pack["costs"], "cos_fee")["basis"] = "PER_FORTNIGHT"
    problems = _problems(raw_pack)
    assert any("'PER_FORTNIGHT' is not one of" in p for p in problems)


def test_rejects_unknown_source(raw_pack: dict[str, Any]) -> None:
    _find(raw_pack["documents"], "passport")["sources"] = ["a_blog_post"]
    _find(raw_pack["costs"], "cos_fee")["source"] = "a_forum_thread"
    problems = _problems(raw_pack)
    assert any("unknown source 'a_blog_post'" in p for p in problems)
    assert any("unknown source 'a_forum_thread'" in p for p in problems)


def test_rejects_item_without_sources(raw_pack: dict[str, Any]) -> None:
    _find(raw_pack["employer_responsibilities"], "key_personnel")["sources"] = []
    problems = _problems(raw_pack)
    assert any("employer_responsibilities" in p and "source" in p for p in problems)


def test_rejects_bare_number_amounts(raw_pack: dict[str, Any]) -> None:
    _find(raw_pack["costs"], "cos_fee")["amount"] = 525.0
    problems = _problems(raw_pack)
    assert any("amounts must be decimal strings" in p for p in problems)
    with pytest.raises(GuidancePackError, match="bare number"):
        _decode_json('{"amount": 525.0}')


def test_rejects_unknown_verification_level(raw_pack: dict[str, Any]) -> None:
    _find(raw_pack["requirements"], "salary_threshold")["verification"] = "heard_it_somewhere"
    problems = _problems(raw_pack)
    assert any("'heard_it_somewhere' is not one of" in p for p in problems)


def test_reports_every_problem_at_once(raw_pack: dict[str, Any]) -> None:
    _find(raw_pack["stages"], "assign_cos")["depends_on"].append("ghost")
    _find(raw_pack["documents"], "passport")["sources"] = ["nowhere"]
    del _find(raw_pack["costs"], "cos_fee")["payer"]
    assert len(_problems(raw_pack)) >= 3


def test_rejects_wrong_route(raw_pack: dict[str, Any]) -> None:
    raw_pack["route"] = "IN-GB"
    assert any("does not match" in p for p in _problems(raw_pack))


def test_requirements_may_carry_a_family_topic(pack: GuidancePack) -> None:
    topics = {item.id: item.topic for item in pack.requirements}
    assert {rid for rid, topic in topics.items() if topic == "family"} == {
        "partner_dependant",
        "child_dependants",
        "no_dependants_temporary_shortage_list",
    }
    assert topics["salary_threshold"] == ""


def test_rejects_an_unknown_requirement_topic(raw_pack: dict[str, Any]) -> None:
    _find(raw_pack["requirements"], "partner_dependant")["topic"] = "pets"
    problems = _problems(raw_pack)
    assert any("topic: 'pets' is not one of family" in p for p in problems)
