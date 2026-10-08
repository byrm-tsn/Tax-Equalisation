"""Tailoring answers and the decision table that selects the applicable items.

Each conditional item in the pack carries a condition over the answers, written
as data: ``{"question": "tb_listed_resident", "equals": true}``,
``{"question": "sponsor_licence_held", "equals": false}``,
``{"question": "dependants_adults", "gte": 1}`` or
``{"question": "occupation_sector", "in": [...]}``, combined with ``all`` and
``any`` where needed. Nothing here infers eligibility; the answers only decide
which documents, stages, costs and notes are shown.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, fields
from types import MappingProxyType
from typing import Final, get_type_hints

from teq_guidance.loader import answer_fits
from teq_guidance.model import (
    Answer,
    Condition,
    ConditionKind,
    CostItem,
    Document,
    GuidancePack,
    Operator,
    Question,
    QuestionType,
    Requirement,
    RouteNote,
    Stage,
)

MONTHS_PER_YEAR: Final = 12

REFINEMENT_FIELDS: Final = frozenset({"visa_length_months"})
"""``TailoringAnswers`` fields that refine a pack question rather than being one.

``visa_length_months`` refines ``visa_length_years``: it is not shown as a question of
its own, and when it is given the years question takes ``ceil(months / 12)``.
"""


class TailoringError(ValueError):
    """An answer is the wrong type or outside the allowed values."""

    def __init__(self, problems: list[str]) -> None:
        self.problems: tuple[str, ...] = tuple(problems)
        super().__init__("Invalid tailoring answers: " + "; ".join(self.problems))


@dataclass(frozen=True, slots=True)
class TailoringAnswers:
    """Answers to the pack's tailoring questions; field names are question ids.

    ``None`` means "not answered": the pack's ``assumed_if_unanswered`` value is
    used for the calculation, and the panel lists the question under what would
    be needed to tailor the guidance further, together with the assumption made.

    ``visa_length_months`` is optional and refines ``visa_length_years`` for a part
    year (30 months is two and a half years). When it is absent the length is
    ``12 x visa_length_years``. When both are given they must agree:
    ``visa_length_years`` must equal ``ceil(visa_length_months / 12)``.
    """

    visa_length_years: int | None = None
    application_location: str | None = None
    sponsor_licence_held: bool | None = None
    sponsor_size: str | None = None
    dependants_adults: int | None = None
    dependants_children: int | None = None
    tb_listed_resident: bool | None = None
    employer_of_record: str | None = None
    english_evidence: str | None = None
    sponsor_certifies_maintenance: bool | None = None
    occupation_sector: str | None = None
    soc_code: str | None = None
    nationality: str | None = None
    visa_length_months: int | None = None

    @classmethod
    def reference_example(cls) -> TailoringAnswers:
        """The reference scenario: a 2-year visa applied for from outside the UK by
        a Turkish resident, sponsored by a medium or large employer that already
        holds a licence, with no dependants. Other questions stay unanswered."""
        return cls(
            visa_length_years=2,
            application_location="outside_uk",
            sponsor_licence_held=True,
            sponsor_size="medium_or_large",
            dependants_adults=0,
            dependants_children=0,
            tb_listed_resident=True,
        )

    @classmethod
    def from_mapping(cls, data: Mapping[str, object]) -> TailoringAnswers:
        """Build answers from form or query-string data.

        Strings are coerced: ``"true"``, ``"yes"``, ``"on"`` and ``"1"`` (and their
        opposites) for yes-or-no questions, digits for counts. An empty string means
        not answered. Keys that are not question ids are ignored, so a whole form
        can be passed in.
        """
        hints = get_type_hints(cls)
        values: dict[str, Answer] = {}
        problems: list[str] = []
        for item in fields(cls):
            if item.name not in data:
                continue
            raw = data[item.name]
            kind = hints[item.name]
            try:
                values[item.name] = _coerce(raw, kind)
            except ValueError as exc:
                problems.append(f"{item.name}: {exc}")
        if problems:
            raise TailoringError(problems)
        return cls(**values)  # type: ignore[arg-type]

    def answered(self) -> dict[str, Answer]:
        """The answers actually given (not ``None``), by question id."""
        return {
            item.name: getattr(self, item.name)
            for item in fields(self)
            if getattr(self, item.name) is not None
        }


def _coerce(raw: object, kind: object) -> Answer:
    if raw is None:
        return None
    if isinstance(raw, str):
        raw = raw.strip()
        if raw == "":
            return None
    if kind == bool | None:
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, str) and raw.lower() in {"true", "yes", "on", "1"}:
            return True
        if isinstance(raw, str) and raw.lower() in {"false", "no", "off", "0"}:
            return False
        raise ValueError(f"expected yes or no, got {raw!r}")
    if kind == int | None:
        if isinstance(raw, int) and not isinstance(raw, bool):
            return raw
        if isinstance(raw, str) and raw.lstrip("-").isdigit():
            return int(raw)
        raise ValueError(f"expected a whole number, got {raw!r}")
    if isinstance(raw, str):
        return raw
    raise ValueError(f"expected text, got {raw!r}")


@dataclass(frozen=True, slots=True)
class ResolvedAnswers:
    """Every question's effective value, with the ids that were assumed.

    ``visa_months`` is the requested visa length in months: ``visa_length_months``
    when given, otherwise ``12 x visa_length_years``.
    """

    values: Mapping[str, Answer]
    assumed: frozenset[str]
    visa_months: int | None = None

    def __getitem__(self, question_id: str) -> Answer:
        return self.values[question_id]

    def count(self, question_id: str) -> int:
        """An integer answer (visa length, dependants)."""
        value = self.values[question_id]
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError(f"{question_id} is not an integer answer")
        return value

    def months(self) -> int:
        """The requested visa length in months."""
        if self.visa_months is not None:
            return self.visa_months
        return MONTHS_PER_YEAR * self.count("visa_length_years")


def resolve_answers(pack: GuidancePack, answers: TailoringAnswers | None = None) -> ResolvedAnswers:
    """Validate the answers against the pack and fill unanswered questions.

    Raises ``TailoringError`` for a value of the wrong type or out of range.
    """
    answers = answers if answers is not None else TailoringAnswers()
    given = answers.answered()
    field_names = {item.name for item in fields(TailoringAnswers)}
    question_ids = {question.id for question in pack.questions}
    problems: list[str] = []
    for missing in sorted(question_ids - field_names):
        problems.append(f"pack question {missing!r} has no TailoringAnswers field")
    for extra in sorted(field_names - question_ids - REFINEMENT_FIELDS):
        if extra in given:
            problems.append(f"{extra!r} is not a question in pack {pack.pack_id}")

    months = given.pop("visa_length_months", None)
    if months is not None:
        derived = _years_from_months(pack, months, given.get("visa_length_years"), problems)
        if derived is not None:
            given["visa_length_years"] = derived

    values: dict[str, Answer] = {}
    assumed: set[str] = set()
    for question in pack.questions:
        if question.id in given:
            value = given[question.id]
            if not answer_fits(question, value):
                problems.append(
                    f"{question.id}: {value!r} is not a valid answer ({_expected(question)})"
                )
            values[question.id] = value
        else:
            values[question.id] = question.assumed_if_unanswered
            assumed.add(question.id)
    if problems:
        raise TailoringError(problems)
    return ResolvedAnswers(
        values=MappingProxyType(values),
        assumed=frozenset(assumed),
        visa_months=months if isinstance(months, int) else None,
    )


def _years_from_months(
    pack: GuidancePack, months: Answer, years: Answer, problems: list[str]
) -> int | None:
    """Validate ``visa_length_months`` and return the whole years it implies.

    The months must be a whole number from 1 to 12 x the years question's maximum, and
    must agree with ``visa_length_years`` when that is also given.
    """
    question = next((q for q in pack.questions if q.id == "visa_length_years"), None)
    if question is None:
        problems.append(f"visa_length_months needs a visa_length_years question in {pack.pack_id}")
        return None
    top = MONTHS_PER_YEAR * question.maximum if question.maximum is not None else None
    if isinstance(months, bool) or not isinstance(months, int) or months < 1:
        problems.append(
            f"visa_length_months: {months!r} is not a whole number of months of 1 or more"
        )
        return None
    if top is not None and months > top:
        problems.append(f"visa_length_months: {months} is more than the maximum of {top} months")
        return None
    derived = -(-months // MONTHS_PER_YEAR)
    if years is not None and years != derived:
        problems.append(
            f"visa_length_months: {months} months is {derived} years when rounded up, but "
            f"visa_length_years is {years!r}; give one of them or make them agree"
        )
        return None
    return derived


def _expected(question: Question) -> str:
    match question.type:
        case QuestionType.BOOL:
            return "expected true or false"
        case QuestionType.INT:
            return f"expected a whole number from {question.minimum} to {question.maximum}"
        case QuestionType.CHOICE:
            return "expected one of " + ", ".join(option.value for option in question.options)
        case QuestionType.TEXT:
            return "expected text"


def evaluate(condition: Condition | None, values: Mapping[str, Answer]) -> bool:
    """Evaluate a condition; no condition means the item always applies."""
    if condition is None:
        return True
    if condition.kind is ConditionKind.ALL:
        return all(evaluate(child, values) for child in condition.children)
    if condition.kind is ConditionKind.ANY:
        return any(evaluate(child, values) for child in condition.children)
    value = values.get(condition.question)
    target = condition.value
    match condition.operator:
        case Operator.EQUALS:
            return _same(value, target)
        case Operator.IN:
            return isinstance(target, tuple) and any(_same(value, option) for option in target)
        case Operator.GTE | Operator.GT | Operator.LTE:
            if isinstance(value, bool) or isinstance(target, bool):
                return False
            if not isinstance(value, int) or not isinstance(target, int):
                return False
            if condition.operator is Operator.GTE:
                return value >= target
            if condition.operator is Operator.GT:
                return value > target
            return value <= target
        case None:
            return False


def _same(left: object, right: object) -> bool:
    """Equality that does not treat ``True`` as ``1``."""
    return type(left) is type(right) and left == right


@dataclass(frozen=True, slots=True)
class Applicable:
    """The pack items that apply to one set of answers, in pack order."""

    answers: ResolvedAnswers
    route_notes: tuple[RouteNote, ...]
    requirements: tuple[Requirement, ...]
    documents: tuple[Document, ...]
    stages: tuple[Stage, ...]
    costs: tuple[CostItem, ...]


def applicable(pack: GuidancePack, answers: TailoringAnswers | None = None) -> Applicable:
    """Select the route notes, requirements, documents, stages and costs that apply."""
    resolved = resolve_answers(pack, answers)
    values = resolved.values
    return Applicable(
        answers=resolved,
        route_notes=tuple(n for n in pack.route_notes if evaluate(n.condition, values)),
        requirements=tuple(r for r in pack.requirements if evaluate(r.condition, values)),
        documents=tuple(d for d in pack.documents if evaluate(d.condition, values)),
        stages=tuple(s for s in pack.stages if evaluate(s.condition, values)),
        costs=tuple(c for c in pack.costs if evaluate(c.condition, values)),
    )


# ---------------------------------------------------------------- plain-English text


def format_answer(question: Question, value: Answer) -> str:
    """A value as the panel shows it, for example ``Yes`` or ``Outside the UK``."""
    if value is None:
        return "Not provided"
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if question.type is QuestionType.CHOICE and isinstance(value, str):
        return question.option_label(value)
    return str(value)


def describe_condition(pack: GuidancePack, condition: Condition | None) -> str:
    """A condition in plain English, for example ``Sponsor licence held: No``."""
    if condition is None:
        return "Always"
    if condition.kind is not ConditionKind.TEST:
        joiner = " and " if condition.kind is ConditionKind.ALL else " or "
        parts = []
        for child in condition.children:
            text = describe_condition(pack, child)
            parts.append(f"({text})" if child.kind is not ConditionKind.TEST else text)
        return joiner.join(parts)
    question = pack.question(condition.question)
    target = condition.value
    match condition.operator:
        case Operator.EQUALS:
            shown = format_answer(question, target if not isinstance(target, tuple) else None)
        case Operator.IN:
            options = target if isinstance(target, tuple) else ()
            shown = "one of " + ", ".join(format_answer(question, option) for option in options)
        case Operator.GTE:
            shown = f"at least {target}"
        case Operator.GT:
            shown = f"more than {target}"
        case Operator.LTE:
            shown = f"{target} or fewer"
        case None:
            shown = "?"
    return f"{question.short_label}: {shown}"
