"""The panel handed to the template: plain data, complete and honest."""

from __future__ import annotations

import dataclasses
import json
from datetime import date

import pytest

from teq_guidance import GuidancePack, TailoringAnswers, build_panel, critical_path


def _walk(value: object, path: str = "panel") -> None:
    """Fail on anything that is not plain data: no floats, Decimals, tuples or dates."""
    if isinstance(value, dict):
        for key, item in value.items():
            assert isinstance(key, str), f"{path}: non-string key {key!r}"
            _walk(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _walk(item, f"{path}[{index}]")
    else:
        assert value is None or type(value) in (str, int, bool), f"{path}: {type(value).__name__}"


def _ids(entries: object) -> list[str]:
    assert isinstance(entries, list)
    return [entry["id"] for entry in entries]


@pytest.fixture
def panel(reference: TailoringAnswers) -> dict[str, object]:
    return build_panel(reference)


def test_panel_is_plain_data_without_floats(panel: dict[str, object]) -> None:
    _walk(panel)
    assert json.loads(json.dumps(panel)) == panel


def test_family_panel_is_plain_data_too(reference: TailoringAnswers) -> None:
    answers = dataclasses.replace(
        reference,
        sponsor_licence_held=False,
        dependants_adults=1,
        dependants_children=2,
        application_location="inside_uk",
        visa_length_years=5,
        employer_of_record="undecided",
    )
    _walk(build_panel(answers, as_of=date(2027, 10, 8)))


def test_panel_sections(panel: dict[str, object]) -> None:
    for key in (
        "route",
        "pack_id",
        "verified_at",
        "disclaimer",
        "warnings",
        "tailoring",
        "route_notes",
        "eligibility",
        "documents",
        "costs",
        "maintenance_funds",
        "timeline",
        "employer_responsibilities",
        "sources",
        "verification_levels",
        "pack_vs_current_guidance",
    ):
        assert key in panel
    assert panel["route"] == "TR-GB"
    assert panel["pack_id"] == "GUIDANCE:TR-GB:v1"
    assert panel["verified_at"] == "2026-10-08"
    assert panel["warnings"] == []


def test_reference_costs_in_the_panel(panel: dict[str, object]) -> None:
    costs = panel["costs"]
    assert isinstance(costs, dict)
    assert costs["subtotals"]["employer_mandatory"] == "3165.00"
    assert costs["subtotals"]["applicant_side"] == "2889.00"
    assert costs["subtotals_display"]["employer_mandatory"] == "£3,165"
    assert costs["separate_from_employment_cost"] is True
    lines = {line["id"]: line for line in costs["lines"]}
    assert lines["immigration_skills_charge"]["amount"] == "2640.00"
    assert lines["immigration_skills_charge"]["payer_label"] == "Employer"
    assert lines["immigration_health_surcharge"]["payer"] == "EITHER_BY_POLICY"
    assert lines["tb_test_fee"]["display"] == "TRY 3,000 to TRY 5,000"
    assert lines["tb_test_fee"]["amount"] is None
    assert lines["cos_fee"]["source"]["url"].startswith("https://www.gov.uk/")
    assert set(costs["listed_separately"]) == {"tb_test_fee", "english_test_fee"}
    assert {line["id"] for line in costs["optional"]} == {
        "priority_visa_service",
        "super_priority_visa_service",
    }


def test_route_note_for_a_posted_worker(reference: TailoringAnswers) -> None:
    default_notes = _ids(build_panel(reference)["route_notes"])
    assert default_notes == ["skilled_worker_assumes_uk_employer"]
    for answer in ("turkish_employer_posts", "undecided"):
        posted = build_panel(dataclasses.replace(reference, employer_of_record=answer))
        notes = posted["route_notes"]
        assert "posted_worker_route" in _ids(notes)
        assert isinstance(notes, list)
        text = next(n["text"] for n in notes if n["id"] == "posted_worker_route")
        assert "Global Business Mobility" in text
        assert "not a decision" in text


def test_unanswered_questions_are_listed_with_the_assumption(panel: dict[str, object]) -> None:
    tailoring = panel["tailoring"]
    assert isinstance(tailoring, dict)
    unanswered = {entry["id"]: entry for entry in tailoring["unanswered"]}
    assert set(unanswered) == {
        "employer_of_record",
        "english_evidence",
        "sponsor_certifies_maintenance",
        "occupation_sector",
        "soc_code",
        "nationality",
    }
    assert unanswered["employer_of_record"]["assumed_value_label"] == (
        "The UK entity employs the worker"
    )
    assert unanswered["soc_code"]["assumed_value_label"] == "Not provided"
    assert tailoring["unanswered_heading"] == "What we would need to tailor this further"

    nothing = build_panel()["tailoring"]
    assert isinstance(nothing, dict)
    assert len(nothing["unanswered"]) == nothing["question_count"] == 13

    everything = TailoringAnswers(
        visa_length_years=2,
        application_location="outside_uk",
        sponsor_licence_held=True,
        sponsor_size="medium_or_large",
        dependants_adults=0,
        dependants_children=0,
        tb_listed_resident=True,
        employer_of_record="uk_entity",
        english_evidence="selt",
        sponsor_certifies_maintenance=True,
        occupation_sector="other",
        soc_code="2136",
        nationality="Turkish",
    )
    complete = build_panel(everything)["tailoring"]
    assert isinstance(complete, dict)
    assert complete["unanswered"] == []


def test_stale_content_warning(reference: TailoringAnswers) -> None:
    assert build_panel(reference, as_of=date(2026, 10, 9))["warnings"] == []
    stale = build_panel(reference, as_of=date(2027, 6, 1))["warnings"]
    assert isinstance(stale, list)
    assert [w["code"] for w in stale] == ["IMMIGRATION_CONTENT_STALE"]
    tight = build_panel(reference, as_of=date(2026, 11, 8), max_content_age_days=30)["warnings"]
    assert isinstance(tight, list)
    assert len(tight) == 1


def test_eligibility_statuses(panel: dict[str, object]) -> None:
    eligibility = panel["eligibility"]
    assert isinstance(eligibility, dict)
    assert "does not decide eligibility" in eligibility["note"]
    general = _ids(eligibility["general"])
    assert {"salary_threshold", "eligible_occupation_rqf6", "english_b2"} <= set(general)
    status = {entry["id"]: entry["status"] for entry in eligibility["circumstance_dependent"]}
    assert status["tb_test_certificate"] == "APPLIES"
    assert status["criminal_record_certificate"] == "DOES_NOT_APPLY"
    assert status["reduced_salary_thresholds"] == "MAY_APPLY"
    assert status["partner_dependant"] == "DOES_NOT_APPLY"
    tb = next(e for e in eligibility["circumstance_dependent"] if e["id"] == "tb_test_certificate")
    assert "Turkey" in tb["why"]


def test_documents_split_with_reasons(panel: dict[str, object]) -> None:
    documents = panel["documents"]
    assert isinstance(documents, dict)
    assert "passport" in _ids(documents["general"])
    conditional = {entry["id"]: entry for entry in documents["conditional"]}
    assert "tb_certificate" in conditional
    assert conditional["tb_certificate"]["why_it_applies"]
    assert conditional["tb_certificate"]["condition_text"] == "Lived in a TB-test country: Yes"
    assert "criminal_record_certificate" in _ids(documents["not_needed"])


def test_timeline_in_the_panel(
    pack: GuidancePack, panel: dict[str, object], reference: TailoringAnswers
) -> None:
    timeline = panel["timeline"]
    assert isinstance(timeline, dict)
    result = critical_path(pack, reference)
    assert timeline["critical_path"] == list(result.critical_path)
    assert timeline["total_text"] == "about 6 to 16 weeks (43 to 112 days)"
    for stage in timeline["stages"]:
        assert stage["on_critical_path"] is (stage["id"] in timeline["critical_path"])
        assert stage["days_min"] <= stage["days_max"]
    assert "sponsor_licence" in _ids(timeline["not_applicable"])
    assert timeline["notes"]
    assert "never a date" in timeline["method_note"]


def test_reference_pack_comparison_and_sources(panel: dict[str, object]) -> None:
    rows = panel["pack_vs_current_guidance"]
    assert isinstance(rows, list)
    assert len(rows) == 15
    assert all(row["verification_label"] for row in rows)
    sources = panel["sources"]
    assert isinstance(sources, list)
    assert all(source["url"].startswith("https://www.gov.uk/") for source in sources)
    assert panel["employer_responsibilities"]
