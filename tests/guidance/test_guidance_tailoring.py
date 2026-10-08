"""Tailoring answers decide which conditional items apply."""

from __future__ import annotations

import dataclasses

import pytest

from teq_guidance import GuidancePack, TailoringAnswers, TailoringError, applicable
from teq_guidance.model import Answer, Condition, ConditionKind, Operator
from teq_guidance.tailoring import describe_condition, evaluate, resolve_answers


def _document_ids(pack: GuidancePack, answers: TailoringAnswers) -> set[str]:
    return {document.id for document in applicable(pack, answers).documents}


def _test(question: str, operator: Operator, value: object) -> Condition:
    return Condition(kind=ConditionKind.TEST, question=question, operator=operator, value=value)  # type: ignore[arg-type]


def test_general_documents_always_apply(pack: GuidancePack, reference: TailoringAnswers) -> None:
    ids = _document_ids(pack, reference)
    assert {"passport", "cos_reference", "job_details", "translations"} <= ids


def test_tb_certificate_follows_residence(pack: GuidancePack, reference: TailoringAnswers) -> None:
    assert "tb_certificate" in _document_ids(pack, reference)
    moved = dataclasses.replace(reference, tb_listed_resident=False)
    assert "tb_certificate" not in _document_ids(pack, moved)
    assert "tb_test" not in {stage.id for stage in applicable(pack, moved).stages}
    assert "tb_test_fee" not in {cost.id for cost in applicable(pack, moved).costs}


def test_criminal_record_certificate_only_for_listed_sectors(
    pack: GuidancePack, reference: TailoringAnswers
) -> None:
    assert "criminal_record_certificate" not in _document_ids(pack, reference)
    for sector in ("education", "healthcare", "social_care"):
        answers = dataclasses.replace(reference, occupation_sector=sector)
        assert "criminal_record_certificate" in _document_ids(pack, answers)


def test_funds_evidence_disappears_when_sponsor_certifies(
    pack: GuidancePack, reference: TailoringAnswers
) -> None:
    assert "maintenance_evidence" in _document_ids(pack, reference)
    certified = dataclasses.replace(reference, sponsor_certifies_maintenance=True)
    assert "maintenance_evidence" not in _document_ids(pack, certified)


def test_dependant_documents(pack: GuidancePack, reference: TailoringAnswers) -> None:
    alone = _document_ids(pack, reference)
    assert not alone & {
        "partner_relationship_evidence",
        "child_birth_certificates",
        "dependant_tb_certificates",
        "dependant_maintenance_evidence",
    }
    family = _document_ids(
        pack, dataclasses.replace(reference, dependants_adults=1, dependants_children=2)
    )
    assert {
        "partner_relationship_evidence",
        "child_birth_certificates",
        "dependant_tb_certificates",
        "dependant_maintenance_evidence",
    } <= family

    child_only = _document_ids(pack, dataclasses.replace(reference, dependants_children=1))
    assert "dependant_tb_certificates" in child_only
    assert "partner_relationship_evidence" not in child_only

    certified_no_tb = dataclasses.replace(
        reference,
        dependants_adults=1,
        sponsor_certifies_maintenance=True,
        tb_listed_resident=False,
    )
    ids = _document_ids(pack, certified_no_tb)
    assert "partner_relationship_evidence" in ids
    assert "dependant_tb_certificates" not in ids
    assert "dependant_maintenance_evidence" not in ids


@pytest.mark.parametrize(
    ("evidence", "expected", "test_stage"),
    [
        ("selt", "english_selt_certificate", True),
        ("degree_taught_in_english", "english_degree_ecctis", False),
        ("exempt_nationality", "english_exempt_nationality", False),
    ],
)
def test_english_evidence_variants(
    pack: GuidancePack,
    reference: TailoringAnswers,
    evidence: str,
    expected: str,
    test_stage: bool,
) -> None:
    answers = dataclasses.replace(reference, english_evidence=evidence)
    english_docs = {d for d in _document_ids(pack, answers) if d.startswith("english_")}
    assert english_docs == {expected}
    selected = applicable(pack, answers)
    assert ("english_test" in {s.id for s in selected.stages}) is test_stage
    assert ("english_test_fee" in {c.id for c in selected.costs}) is test_stage


def test_licence_stage_and_fee_follow_the_licence_answer(
    pack: GuidancePack, reference: TailoringAnswers
) -> None:
    held = applicable(pack, reference)
    assert "sponsor_licence" not in {stage.id for stage in held.stages}
    not_held = applicable(pack, dataclasses.replace(reference, sponsor_licence_held=False))
    assert "sponsor_licence" in {stage.id for stage in not_held.stages}
    assert "sponsor_licence_fee" in {cost.id for cost in not_held.costs}


def test_operators() -> None:
    values: dict[str, Answer] = {"flag": True, "count": 2, "choice": "b"}
    assert evaluate(None, values)
    assert evaluate(_test("flag", Operator.EQUALS, True), values)
    assert not evaluate(_test("flag", Operator.EQUALS, False), values)
    assert not evaluate(_test("count", Operator.EQUALS, True), {"count": 1})
    assert not evaluate(_test("flag", Operator.EQUALS, 1), values)
    assert evaluate(_test("count", Operator.GTE, 2), values)
    assert not evaluate(_test("count", Operator.GTE, 3), values)
    assert evaluate(_test("count", Operator.GT, 1), values)
    assert evaluate(_test("count", Operator.LTE, 2), values)
    assert not evaluate(_test("flag", Operator.GTE, 1), values)
    assert evaluate(_test("choice", Operator.IN, ("a", "b")), values)
    assert not evaluate(_test("choice", Operator.IN, ("c",)), values)
    assert not evaluate(_test("missing", Operator.EQUALS, True), values)
    both = Condition(
        kind=ConditionKind.ALL,
        children=(_test("flag", Operator.EQUALS, True), _test("count", Operator.GTE, 3)),
    )
    either = dataclasses.replace(both, kind=ConditionKind.ANY)
    assert not evaluate(both, values)
    assert evaluate(either, values)


def test_unanswered_questions_use_the_assumed_values(pack: GuidancePack) -> None:
    resolved = resolve_answers(pack, TailoringAnswers())
    assert resolved.assumed == {question.id for question in pack.questions}
    assert resolved["visa_length_years"] == 2
    assert resolved["application_location"] == "outside_uk"
    assert resolved["sponsor_licence_held"] is True
    assert resolved["soc_code"] is None

    answered = resolve_answers(pack, TailoringAnswers(sponsor_licence_held=False))
    assert answered["sponsor_licence_held"] is False
    assert "sponsor_licence_held" not in answered.assumed


@pytest.mark.parametrize(
    "answers",
    [
        TailoringAnswers(visa_length_years=0),
        TailoringAnswers(visa_length_years=11),
        TailoringAnswers(dependants_adults=2),
        TailoringAnswers(dependants_children=-1),
        TailoringAnswers(application_location="somewhere"),
        TailoringAnswers(sponsor_licence_held="yes"),  # type: ignore[arg-type]
        TailoringAnswers(visa_length_years=True),
    ],
)
def test_invalid_answers_are_rejected(pack: GuidancePack, answers: TailoringAnswers) -> None:
    with pytest.raises(TailoringError):
        resolve_answers(pack, answers)


def test_from_mapping_coerces_form_values() -> None:
    answers = TailoringAnswers.from_mapping(
        {
            "sponsor_licence_held": "no",
            "tb_listed_resident": "on",
            "dependants_children": "2",
            "visa_length_years": "",
            "soc_code": " 2136 ",
            "salary": "90000",
        }
    )
    assert answers == TailoringAnswers(
        sponsor_licence_held=False, tb_listed_resident=True, dependants_children=2, soc_code="2136"
    )
    with pytest.raises(TailoringError, match="sponsor_licence_held"):
        TailoringAnswers.from_mapping({"sponsor_licence_held": "perhaps"})
    with pytest.raises(TailoringError, match="dependants_adults"):
        TailoringAnswers.from_mapping({"dependants_adults": "one"})


def test_describe_condition(pack: GuidancePack) -> None:
    licence = pack.stage("sponsor_licence").condition
    assert describe_condition(pack, licence) == "Sponsor licence held: No"
    tb_dependants = next(d for d in pack.documents if d.id == "dependant_tb_certificates")
    assert describe_condition(pack, tb_dependants.condition) == (
        "Lived in a TB-test country: Yes and (Partner applying as a dependant: at least 1 or "
        "Children applying as dependants: at least 1)"
    )
    sector = next(d for d in pack.documents if d.id == "criminal_record_certificate")
    assert describe_condition(pack, sector.condition) == (
        "Occupation sector: one of Education, Healthcare, Social care"
    )
