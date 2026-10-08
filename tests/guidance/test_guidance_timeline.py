"""Critical path over the timeline dependency graph."""

from __future__ import annotations

import dataclasses
import itertools

import pytest

from teq_guidance import GuidancePack, TailoringAnswers, TimelineResult, applicable, critical_path


def _stage(result: TimelineResult, stage_id: str) -> object:
    return next(stage for stage in result.stages if stage.id == stage_id)


def _longest_by_enumeration(pack: GuidancePack, answers: TailoringAnswers) -> tuple[int, int]:
    """Independent oracle: enumerate every path through the applicable stages."""
    stages = {stage.id: stage for stage in applicable(pack, answers).stages}
    deps = {sid: [d for d in stage.depends_on if d in stages] for sid, stage in stages.items()}

    def paths_ending_at(sid: str) -> list[list[str]]:
        if not deps[sid]:
            return [[sid]]
        return [[*path, sid] for dep in deps[sid] for path in paths_ending_at(dep)]

    every_path = [path for sid in stages for path in paths_ending_at(sid)]
    low = max(sum(stages[s].typical_days_min for s in path) for path in every_path)
    high = max(sum(stages[s].typical_days_max for s in path) for path in every_path)
    return low, high


def _assert_marked(result: TimelineResult) -> None:
    on_path = set(result.critical_path)
    on_path_min = set(result.critical_path_min)
    assert on_path
    for stage in result.stages:
        assert stage.on_critical_path is (stage.id in on_path)
        assert stage.on_critical_path_min is (stage.id in on_path_min)
        assert (stage.slack_days_max == 0) is stage.on_critical_path


def test_licence_held_and_tb_test_needed(pack: GuidancePack, reference: TailoringAnswers) -> None:
    result = critical_path(pack, reference)
    assert (result.total_days_min, result.total_days_max) == (43, 112)
    assert (result.total_weeks_min, result.total_weeks_max) == (6, 16)
    expected = (
        "english_test",
        "application_and_biometrics",
        "decision_outside_uk",
        "travel_and_start",
    )
    assert result.critical_path == expected
    assert result.critical_path_min == expected
    assert "tb_test" in {stage.id for stage in result.stages}
    assert set(result.not_applicable) == {"sponsor_licence", "decision_inside_uk"}
    _assert_marked(result)


def test_without_a_licence(pack: GuidancePack, reference: TailoringAnswers) -> None:
    answers = dataclasses.replace(reference, sponsor_licence_held=False)
    result = critical_path(pack, answers)
    assert (result.total_days_min, result.total_days_max) == (70, 145)
    assert (result.total_weeks_min, result.total_weeks_max) == (10, 21)
    expected = (
        "sponsor_licence",
        "assign_cos",
        "application_and_biometrics",
        "decision_outside_uk",
        "travel_and_start",
    )
    assert result.critical_path == expected
    assert result.critical_path_min == expected
    english = _stage(result, "english_test")
    assert english.slack_days_max == 75 - 42  # type: ignore[attr-defined]
    assert not english.on_critical_path  # type: ignore[attr-defined]
    _assert_marked(result)


def test_critical_path_durations_add_up_to_the_total(
    pack: GuidancePack, reference: TailoringAnswers
) -> None:
    for answers in (reference, dataclasses.replace(reference, sponsor_licence_held=False)):
        result = critical_path(pack, answers)
        by_id = {stage.id: stage for stage in result.stages}
        assert sum(by_id[s].days_max for s in result.critical_path) == result.total_days_max
        assert sum(by_id[s].days_min for s in result.critical_path_min) == result.total_days_min
        for earlier, later in itertools.pairwise(result.critical_path):
            assert earlier in by_id[later].depends_on


@pytest.mark.parametrize(
    ("licence", "tb", "english", "location"),
    list(
        itertools.product(
            (True, False),
            (True, False),
            ("selt", "degree_taught_in_english"),
            ("outside_uk", "inside_uk"),
        )
    ),
)
def test_totals_match_path_enumeration(
    pack: GuidancePack, licence: bool, tb: bool, english: str, location: str
) -> None:
    answers = TailoringAnswers(
        sponsor_licence_held=licence,
        tb_listed_resident=tb,
        english_evidence=english,
        application_location=location,
    )
    result = critical_path(pack, answers)
    assert (result.total_days_min, result.total_days_max) == _longest_by_enumeration(pack, answers)
    assert result.total_weeks_min * 7 <= result.total_days_min
    assert result.total_weeks_max * 7 >= result.total_days_max
    _assert_marked(result)


def test_no_tb_test_and_degree_route(pack: GuidancePack, reference: TailoringAnswers) -> None:
    answers = dataclasses.replace(
        reference, tb_listed_resident=False, english_evidence="degree_taught_in_english"
    )
    result = critical_path(pack, answers)
    ids = {stage.id for stage in result.stages}
    assert "tb_test" not in ids
    assert "english_test" not in ids
    # documents (7 to 21 days) now outlasts defining the job and assigning the CoS (4 to 15)
    assert (result.total_days_min, result.total_days_max) == (36, 91)
    assert result.critical_path == (
        "documents",
        "application_and_biometrics",
        "decision_outside_uk",
        "travel_and_start",
    )


def test_inside_uk_uses_the_longer_decision(
    pack: GuidancePack, reference: TailoringAnswers
) -> None:
    result = critical_path(pack, dataclasses.replace(reference, application_location="inside_uk"))
    ids = {stage.id for stage in result.stages}
    assert "decision_inside_uk" in ids
    assert "decision_outside_uk" not in ids
    assert result.total_days_max == 112 - 21 + 56


def test_parallel_structure(pack: GuidancePack, reference: TailoringAnswers) -> None:
    answers = dataclasses.replace(reference, sponsor_licence_held=False)
    result = critical_path(pack, answers)
    assert result.phases[0] == (
        "sponsor_licence",
        "job_definition",
        "english_test",
        "tb_test",
        "documents",
    )
    assert result.phases[1] == ("assign_cos",)
    assert dict(result.parallel_groups) == {
        "applicant_preparation": ("english_test", "tb_test", "documents")
    }
    english = _stage(result, "english_test")
    assert set(english.parallel_with) == {  # type: ignore[attr-defined]
        "sponsor_licence",
        "job_definition",
        "assign_cos",
        "tb_test",
        "documents",
    }
    application = _stage(result, "application_and_biometrics")
    assert application.parallel_with == ()  # type: ignore[attr-defined]


# --------------------------------------------------------------------------- pass-through


def _synthetic_chain_pack(raw_pack: dict[str, object]) -> GuidancePack:
    """A pack whose timeline is the chain first -> middle -> last, plus a side stage
    that also waits for the middle one; ``middle`` applies only without a licence."""
    from teq_guidance import parse_pack

    template = {
        "actor": "EMPLOYER",
        "verification": "from_knowledge",
        "sources": ["gov_sw_your_job"],
    }
    raw_pack["stages"] = [
        {
            **template,
            "id": "first",
            "title": "First",
            "description": "First.",
            "depends_on": [],
            "typical_days_min": 10,
            "typical_days_max": 20,
        },
        {
            **template,
            "id": "middle",
            "title": "Middle",
            "description": "Middle.",
            "depends_on": ["first"],
            "typical_days_min": 5,
            "typical_days_max": 7,
            "condition": {"question": "sponsor_licence_held", "equals": False},
        },
        {
            **template,
            "id": "short",
            "title": "Short",
            "description": "Short.",
            "depends_on": [],
            "typical_days_min": 1,
            "typical_days_max": 2,
        },
        {
            **template,
            "id": "last",
            "title": "Last",
            "description": "Last.",
            "depends_on": ["middle", "short"],
            "typical_days_min": 3,
            "typical_days_max": 4,
        },
    ]
    return parse_pack(raw_pack, expected_route="TR-GB")


def test_a_stage_that_does_not_apply_passes_its_dependencies_through(
    raw_pack: dict[str, object], reference: TailoringAnswers
) -> None:
    pack = _synthetic_chain_pack(raw_pack)
    # Licence held: "middle" does not apply, so "last" must still wait for "first".
    result = critical_path(pack, reference)
    by_id = {stage.id: stage for stage in result.stages}
    assert "middle" not in by_id
    assert by_id["last"].depends_on == ("first", "short")
    assert by_id["last"].earliest_start_max == 20
    assert (result.total_days_min, result.total_days_max) == (13, 24)
    assert result.critical_path == ("first", "last")
    assert "first" not in by_id["last"].parallel_with
    assert result.phases == (("first", "short"), ("last",))
    # Without a licence the chain is whole and "middle" is on the critical path.
    whole = critical_path(pack, dataclasses.replace(reference, sponsor_licence_held=False))
    assert {stage.id: stage for stage in whole.stages}["last"].depends_on == ("middle", "short")
    assert (whole.total_days_min, whole.total_days_max) == (18, 31)
    assert whole.critical_path == ("first", "middle", "last")


def test_inherited_dependencies_already_implied_are_not_repeated(
    pack: GuidancePack, reference: TailoringAnswers
) -> None:
    # decision_inside_uk does not apply; travel_and_start would inherit
    # application_and_biometrics from it, but that is already implied through
    # decision_outside_uk, so the shown dependencies are unchanged.
    result = critical_path(pack, reference)
    travel = _stage(result, "travel_and_start")
    assert travel.depends_on == ("decision_outside_uk",)  # type: ignore[attr-defined]
