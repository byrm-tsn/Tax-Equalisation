"""Typed model of an immigration guidance pack and of the results built from it.

Everything here is a frozen dataclass so that a loaded pack cannot be changed by
the code that reads it. Money is ``decimal.Decimal``; nothing in this package
uses floats.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum

#: The value of one tailoring answer once resolved.
type Answer = bool | int | str | None


class Verification(StrEnum):
    """How a fact in the pack was verified."""

    SEARCH_CONFIRMED = "search_confirmed"
    THIRD_PARTY_REPORTED = "third_party_reported"
    FROM_KNOWLEDGE = "from_knowledge"
    #: Only used in the reference-pack comparison table, for arithmetic.
    COMPUTED = "computed"


class Basis(StrEnum):
    """How a cost item scales with the visa length and the family."""

    FIXED = "FIXED"
    PER_VISA_YEAR = "PER_VISA_YEAR"
    FIRST_YEAR_THEN_PER_6_MONTHS = "FIRST_YEAR_THEN_PER_6_MONTHS"
    PER_ADULT_DEPENDANT = "PER_ADULT_DEPENDANT"
    PER_CHILD_DEPENDANT = "PER_CHILD_DEPENDANT"
    PER_DEPENDANT_VISA_YEAR_ADULT = "PER_DEPENDANT_VISA_YEAR_ADULT"
    PER_DEPENDANT_VISA_YEAR_CHILD = "PER_DEPENDANT_VISA_YEAR_CHILD"


class Payer(StrEnum):
    """Who pays a cost: in law, and for EITHER_BY_POLICY, in practice."""

    EMPLOYER = "EMPLOYER"
    APPLICANT = "APPLICANT"
    EITHER_BY_POLICY = "EITHER_BY_POLICY"


class Actor(StrEnum):
    """Who carries out a timeline stage."""

    EMPLOYER = "EMPLOYER"
    EMPLOYEE = "EMPLOYEE"
    HOME_OFFICE = "HOME_OFFICE"
    THIRD_PARTY = "THIRD_PARTY"


class QuestionType(StrEnum):
    BOOL = "bool"
    INT = "int"
    CHOICE = "choice"
    TEXT = "text"


class Operator(StrEnum):
    """Comparison operators allowed in a condition test."""

    EQUALS = "equals"
    IN = "in"
    GTE = "gte"
    GT = "gt"
    LTE = "lte"


class ConditionKind(StrEnum):
    TEST = "test"
    ALL = "all"
    ANY = "any"


# --------------------------------------------------------------------------- pack


@dataclass(frozen=True, slots=True)
class Source:
    id: str
    title: str
    url: str


@dataclass(frozen=True, slots=True)
class Condition:
    """A predicate over tailoring answers.

    Either a single test (``kind`` TEST: ``question`` ``operator`` ``value``) or a
    group (``kind`` ALL or ANY over ``children``). In the JSON pack a test is
    written ``{"question": "tb_listed_resident", "equals": true}``, a group as
    ``{"all": [...]}`` or ``{"any": [...]}``, and a bare list means ``all``.
    """

    kind: ConditionKind
    question: str = ""
    operator: Operator | None = None
    value: Answer | tuple[str | int | bool, ...] = None
    children: tuple[Condition, ...] = ()

    def question_ids(self) -> tuple[str, ...]:
        """Every question id this condition refers to, in order."""
        if self.kind is ConditionKind.TEST:
            return (self.question,)
        ids: list[str] = []
        for child in self.children:
            ids.extend(child.question_ids())
        return tuple(ids)


@dataclass(frozen=True, slots=True)
class QuestionOption:
    value: str
    label: str


@dataclass(frozen=True, slots=True)
class Question:
    """A tailoring question; its id is also a field of ``TailoringAnswers``."""

    id: str
    short_label: str
    text: str
    type: QuestionType
    assumed_if_unanswered: Answer
    why_it_matters: str
    options: tuple[QuestionOption, ...] = ()
    minimum: int | None = None
    maximum: int | None = None

    def option_label(self, value: str) -> str:
        for option in self.options:
            if option.value == value:
                return option.label
        return value


@dataclass(frozen=True, slots=True)
class RouteNote:
    id: str
    title: str
    text: str
    verification: Verification
    sources: tuple[str, ...]
    condition: Condition | None = None


@dataclass(frozen=True, slots=True)
class Requirement:
    """An eligibility requirement: ``general`` (everyone) or ``circumstance``."""

    id: str
    category: str
    title: str
    text: str
    verification: Verification
    sources: tuple[str, ...]
    condition: Condition | None = None
    why_it_applies: str = ""


@dataclass(frozen=True, slots=True)
class EmployerResponsibility:
    id: str
    title: str
    text: str
    verification: Verification
    sources: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Stage:
    """One node of the timeline dependency graph. Durations are calendar days."""

    id: str
    title: str
    description: str
    actor: Actor
    depends_on: tuple[str, ...]
    typical_days_min: int
    typical_days_max: int
    verification: Verification
    sources: tuple[str, ...]
    also_involves: tuple[Actor, ...] = ()
    condition: Condition | None = None
    parallel_group: str | None = None
    alternative_note: str = ""


@dataclass(frozen=True, slots=True)
class Document:
    id: str
    title: str
    text: str
    why_it_applies: str
    verification: Verification
    sources: tuple[str, ...]
    condition: Condition | None = None


@dataclass(frozen=True, slots=True)
class CostTier:
    """One tier of a tiered cost: applies when ``condition`` holds.

    ``amount`` may be ``None`` when the pack does not hold the figure; ``note``
    then says so and the line is shown without an amount.
    """

    condition: Condition
    amount: Decimal | None
    label: str
    amount_per_period: Decimal | None = None
    note: str = ""


@dataclass(frozen=True, slots=True)
class CostItem:
    """A cost as a formula: an amount (or range, or tiers) times a basis."""

    id: str
    label: str
    description: str
    basis: Basis
    currency: str
    payer: Payer
    cannot_be_recouped_from_worker: bool
    verification: Verification
    source: str
    verified_at: date
    amount: Decimal | None = None
    amount_per_period: Decimal | None = None
    amount_min: Decimal | None = None
    amount_max: Decimal | None = None
    tiers: tuple[CostTier, ...] = ()
    condition: Condition | None = None
    optional: bool = False
    effective_from: date | None = None
    notes: str = ""


@dataclass(frozen=True, slots=True)
class MaintenanceFunds:
    main_applicant: Decimal
    partner: Decimal
    first_child: Decimal
    each_further_child: Decimal
    days_held: int
    condition: Condition | None
    text: str
    verification: Verification
    sources: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TimelineNote:
    id: str
    text: str
    verification: Verification
    sources: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PackComparison:
    """One row of the reference pack versus current guidance table."""

    topic: str
    reference_pack: str
    current_position: str
    effect_on_tool: str
    verification: Verification


@dataclass(frozen=True, slots=True)
class GuidancePack:
    route: str
    version: int
    title: str
    verified_at: date
    disclaimer: str
    sources: tuple[Source, ...]
    questions: tuple[Question, ...]
    route_notes: tuple[RouteNote, ...]
    requirements: tuple[Requirement, ...]
    employer_responsibilities: tuple[EmployerResponsibility, ...]
    stages: tuple[Stage, ...]
    documents: tuple[Document, ...]
    costs: tuple[CostItem, ...]
    maintenance_funds: MaintenanceFunds
    timeline_notes: tuple[TimelineNote, ...]
    pack_vs_current_guidance: tuple[PackComparison, ...]

    @property
    def pack_id(self) -> str:
        """Stable identifier recorded with a result, for example ``GUIDANCE:TR-GB:v1``."""
        return f"GUIDANCE:{self.route}:v{self.version}"

    def source(self, source_id: str) -> Source:
        for source in self.sources:
            if source.id == source_id:
                return source
        raise KeyError(source_id)

    def question(self, question_id: str) -> Question:
        for question in self.questions:
            if question.id == question_id:
                return question
        raise KeyError(question_id)

    def stage(self, stage_id: str) -> Stage:
        for stage in self.stages:
            if stage.id == stage_id:
                return stage
        raise KeyError(stage_id)


# ------------------------------------------------------------------------ results


@dataclass(frozen=True, slots=True)
class CostLine:
    """A cost item computed for one set of tailoring answers.

    ``amount`` is the exact computed figure, or ``None`` for a range (see
    ``amount_min`` and ``amount_max``) or a figure the pack does not hold.
    ``in_subtotal`` is true only for exact GBP amounts that are not optional.
    """

    id: str
    label: str
    description: str
    basis: Basis
    payer: Payer
    cannot_be_recouped_from_worker: bool
    currency: str
    amount: Decimal | None
    amount_min: Decimal | None
    amount_max: Decimal | None
    formula: str
    optional: bool
    in_subtotal: bool
    verification: Verification
    source: str
    verified_at: date
    effective_from: date | None
    notes: str


@dataclass(frozen=True, slots=True)
class CostSubtotals:
    """Subtotals in GBP over the lines with ``in_subtotal`` set.

    ``employer_by_policy`` and ``applicant_only`` together make
    ``applicant_side``: costs that fall on the applicant in law, some of which
    employers commonly pay by policy. None of these is ever added to the
    employment-cost estimate.
    """

    employer_mandatory: Decimal
    employer_by_policy: Decimal
    applicant_only: Decimal
    applicant_side: Decimal
    employer_including_policy: Decimal


@dataclass(frozen=True, slots=True)
class FundsRequirement:
    """Money the applicant must show has been held; not a fee and not a cost."""

    applies: bool
    amount: Decimal
    formula: str
    days_held: int


@dataclass(frozen=True, slots=True)
class TimelineStage:
    """A stage placed on the critical-path schedule, in days from the start."""

    id: str
    title: str
    actor: Actor
    also_involves: tuple[Actor, ...]
    days_min: int
    days_max: int
    depends_on: tuple[str, ...]
    parallel_group: str | None
    earliest_start_min: int
    earliest_start_max: int
    earliest_finish_min: int
    earliest_finish_max: int
    slack_days_max: int
    on_critical_path: bool
    on_critical_path_min: bool
    parallel_with: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TimelineResult:
    """Critical-path totals as ranges in days and weeks; never a date.

    ``critical_path`` uses the maximum durations (the planning constraint);
    ``critical_path_min`` uses the minimum durations and can differ.
    """

    stages: tuple[TimelineStage, ...]
    critical_path: tuple[str, ...]
    critical_path_min: tuple[str, ...]
    total_days_min: int
    total_days_max: int
    total_weeks_min: int
    total_weeks_max: int
    phases: tuple[tuple[str, ...], ...]
    parallel_groups: tuple[tuple[str, tuple[str, ...]], ...]
    not_applicable: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class GuidancePanel:
    """The panel handed to a template or an API response.

    Every field holds only plain data: dicts, lists, strings, ints, bools and
    ``None``. Money is a decimal string.
    """

    route: str
    pack_id: str
    title: str
    version: int
    verified_at: str
    disclaimer: str
    warnings: list[dict[str, object]]
    tailoring: dict[str, object]
    route_notes: list[dict[str, object]]
    eligibility: dict[str, object]
    documents: dict[str, object]
    costs: dict[str, object]
    maintenance_funds: dict[str, object]
    timeline: dict[str, object]
    employer_responsibilities: list[dict[str, object]]
    sources: list[dict[str, object]]
    verification_levels: dict[str, str]
    pack_vs_current_guidance: list[dict[str, object]]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)
