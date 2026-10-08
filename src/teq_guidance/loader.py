"""Load and validate a bundled guidance pack.

The pack is JSON with every amount written as a string. Loading fails, naming
every problem found, if the pack is malformed or internally inconsistent: a
stage depending on an unknown stage, a condition on an unknown question, a cost
without a basis or payer, an item without a known source, a cycle in the
timeline graph, or a bare number where an amount should be a string.
"""

from __future__ import annotations

import functools
import json
from collections.abc import Iterable, Mapping, Sequence
from datetime import date
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from importlib import resources
from typing import NoReturn

from teq_guidance.model import (
    Actor,
    Answer,
    Basis,
    Condition,
    ConditionKind,
    CostItem,
    CostTier,
    Document,
    EmployerResponsibility,
    GuidancePack,
    MaintenanceFunds,
    Operator,
    PackComparison,
    Payer,
    Question,
    QuestionOption,
    QuestionType,
    Requirement,
    RouteNote,
    Source,
    Stage,
    TimelineNote,
    Verification,
)

#: Bundled packs by route code.
_PACK_FILES: Mapping[str, str] = {"TR-GB": "tr_gb_skilled_worker.json"}

#: Currencies a cost item may be stated in.
SUPPORTED_CURRENCIES = frozenset({"GBP", "TRY"})

#: Verification levels allowed on pack items (``computed`` is for the comparison table).
_ITEM_VERIFICATIONS = frozenset(
    {Verification.OFFICIAL_SOURCE, Verification.THIRD_PARTY, Verification.UNVERIFIED}
)

_REQUIREMENT_CATEGORIES = frozenset({"general", "circumstance"})

#: Optional display groups for requirements.
_REQUIREMENT_TOPICS = frozenset({"family"})

_REQUIRED_TOP_LEVEL = (
    "route",
    "version",
    "verified_at",
    "title",
    "disclaimer",
    "sources",
    "tailoring_questions",
    "routes_note",
    "requirements",
    "employer_responsibilities",
    "stages",
    "documents",
    "costs",
    "maintenance_funds",
    "timeline_notes",
    "pack_vs_current_guidance",
)


class GuidancePackError(ValueError):
    """The pack is malformed or inconsistent. ``problems`` lists every issue found."""

    def __init__(self, problems: Sequence[str]) -> None:
        self.problems: tuple[str, ...] = tuple(problems)
        super().__init__("Invalid guidance pack: " + "; ".join(self.problems))


class GuidancePackNotFoundError(LookupError):
    """No bundled pack exists for the requested route."""

    def __init__(self, route: str) -> None:
        self.route = route
        supported = ", ".join(available_routes())
        super().__init__(f"No guidance pack for route {route!r}; supported routes: {supported}")


def available_routes() -> tuple[str, ...]:
    """Route codes that have a bundled guidance pack."""
    return tuple(sorted(_PACK_FILES))


@functools.cache
def load_pack(route: str = "TR-GB") -> GuidancePack:
    """Read, parse and validate the bundled pack for ``route``.

    The result is immutable and cached per route.
    """
    filename = _PACK_FILES.get(route)
    if filename is None:
        raise GuidancePackNotFoundError(route)
    text = resources.files("teq_guidance").joinpath("data", filename).read_text(encoding="utf-8")
    return parse_pack(_decode_json(text), expected_route=route)


def _decode_json(text: str) -> object:
    def reject_float(literal: str) -> NoReturn:
        raise GuidancePackError(
            [f"bare number {literal} found; amounts must be strings and counts integers"]
        )

    try:
        return json.loads(text, parse_float=reject_float)
    except json.JSONDecodeError as exc:
        raise GuidancePackError([f"not valid JSON: {exc}"]) from exc


def parse_pack(data: object, *, expected_route: str | None = None) -> GuidancePack:
    """Build a ``GuidancePack`` from decoded JSON and validate it.

    Raises ``GuidancePackError`` listing every problem found.
    """
    return _Parser(expected_route).parse(data)


# --------------------------------------------------------------------------- parsing


class _Parser:
    def __init__(self, expected_route: str | None) -> None:
        self.expected_route = expected_route
        self.problems: list[str] = []

    # ------------------------------------------------------------------ helpers

    def problem(self, message: str) -> None:
        self.problems.append(message)

    def mapping(self, raw: object, where: str) -> Mapping[str, object]:
        if isinstance(raw, Mapping):
            return raw
        self.problem(f"{where}: expected an object")
        return {}

    def items(self, raw: object, where: str) -> list[Mapping[str, object]]:
        if not isinstance(raw, list):
            self.problem(f"{where}: expected a list")
            return []
        return [self.mapping(item, f"{where}[{index}]") for index, item in enumerate(raw)]

    def text(
        self, obj: Mapping[str, object], key: str, where: str, *, required: bool = True
    ) -> str:
        value = obj.get(key)
        if isinstance(value, str) and value.strip():
            return value
        if value is None and not required:
            return ""
        self.problem(f"{where}.{key}: expected a non-empty string")
        return ""

    def optional_text(self, obj: Mapping[str, object], key: str, where: str) -> str | None:
        value = obj.get(key)
        if value is None:
            return None
        if isinstance(value, str) and value.strip():
            return value
        self.problem(f"{where}.{key}: expected a non-empty string or null")
        return None

    def integer(self, obj: Mapping[str, object], key: str, where: str) -> int:
        value = obj.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
        self.problem(f"{where}.{key}: expected an integer")
        return 0

    def optional_integer(self, obj: Mapping[str, object], key: str, where: str) -> int | None:
        if obj.get(key) is None:
            return None
        return self.integer(obj, key, where)

    def boolean(self, obj: Mapping[str, object], key: str, where: str, *, default: bool) -> bool:
        value = obj.get(key, default)
        if isinstance(value, bool):
            return value
        self.problem(f"{where}.{key}: expected true or false")
        return default

    def money(self, obj: Mapping[str, object], key: str, where: str) -> Decimal | None:
        """A non-negative decimal amount written as a string, or ``None`` if absent."""
        value = obj.get(key)
        if value is None:
            return None
        if not isinstance(value, str):
            self.problem(f"{where}.{key}: amounts must be decimal strings, got {value!r}")
            return None
        try:
            amount = Decimal(value)
        except InvalidOperation:
            self.problem(f"{where}.{key}: {value!r} is not a decimal amount")
            return None
        if not amount.is_finite() or amount < 0:
            self.problem(f"{where}.{key}: {value!r} must be a finite amount of zero or more")
            return None
        return amount

    def iso_date(
        self, obj: Mapping[str, object], key: str, where: str, *, required: bool = True
    ) -> date | None:
        value = obj.get(key)
        if value is None and not required:
            return None
        if isinstance(value, str):
            try:
                return date.fromisoformat(value)
            except ValueError:
                pass
        self.problem(f"{where}.{key}: expected an ISO date (YYYY-MM-DD)")
        return None

    def enum[E: StrEnum](
        self, kind: type[E], obj: Mapping[str, object], key: str, where: str, default: E
    ) -> E:
        value = obj.get(key)
        if isinstance(value, str):
            try:
                return kind(value)
            except ValueError:
                pass
        allowed = ", ".join(member.value for member in kind)
        self.problem(f"{where}.{key}: {value!r} is not one of {allowed}")
        return default

    def verification(self, obj: Mapping[str, object], where: str) -> Verification:
        level = self.enum(Verification, obj, "verification", where, Verification.UNVERIFIED)
        if level not in _ITEM_VERIFICATIONS:
            self.problem(
                f"{where}.verification: {level.value!r} is only allowed in the comparison table"
            )
        return level

    def source_ids(self, obj: Mapping[str, object], where: str) -> tuple[str, ...]:
        value = obj.get("sources")
        if not isinstance(value, list) or not value:
            self.problem(f"{where}.sources: expected at least one source id")
            return ()
        ids = tuple(item for item in value if isinstance(item, str))
        if len(ids) != len(value):
            self.problem(f"{where}.sources: every source id must be a string")
        return ids

    def condition(self, raw: object, where: str) -> Condition | None:
        if raw is None:
            return None
        return self._condition(raw, where)

    def _condition(self, raw: object, where: str) -> Condition:
        if isinstance(raw, list):
            if not raw:
                self.problem(f"{where}: an empty condition list is not allowed")
            children = tuple(self._condition(item, f"{where}[{i}]") for i, item in enumerate(raw))
            return Condition(kind=ConditionKind.ALL, children=children)
        obj = self.mapping(raw, where)
        for group_key, kind in (("all", ConditionKind.ALL), ("any", ConditionKind.ANY)):
            if group_key in obj:
                if len(obj) != 1:
                    self.problem(f"{where}: {group_key!r} must be the only key in a group")
                members = obj[group_key]
                if not isinstance(members, list) or not members:
                    self.problem(f"{where}.{group_key}: expected a non-empty list")
                    members = []
                children = tuple(
                    self._condition(item, f"{where}.{group_key}[{i}]")
                    for i, item in enumerate(members)
                )
                return Condition(kind=kind, children=children)
        question = self.text(obj, "question", where)
        operators = [op for op in Operator if op.value in obj]
        unknown = sorted(set(obj) - {"question"} - {op.value for op in Operator})
        if unknown:
            self.problem(f"{where}: unknown condition keys {unknown}")
        if len(operators) != 1:
            allowed = ", ".join(op.value for op in Operator)
            self.problem(f"{where}: expected exactly one operator ({allowed})")
            return Condition(kind=ConditionKind.TEST, question=question)
        operator = operators[0]
        raw_value = obj[operator.value]
        value: Answer | tuple[str | int | bool, ...]
        if operator is Operator.IN:
            if not isinstance(raw_value, list) or not raw_value:
                self.problem(f"{where}.in: expected a non-empty list")
                value = ()
            else:
                value = tuple(v for v in raw_value if isinstance(v, (str, int, bool)))
                if len(value) != len(raw_value):
                    self.problem(f"{where}.in: values must be strings, integers or booleans")
        elif isinstance(raw_value, (str, int, bool)):
            value = raw_value
        else:
            self.problem(f"{where}.{operator.value}: expected a string, integer or boolean")
            value = None
        return Condition(kind=ConditionKind.TEST, question=question, operator=operator, value=value)

    # ------------------------------------------------------------------ sections

    def parse(self, data: object) -> GuidancePack:
        top = self.mapping(data, "pack")
        for key in _REQUIRED_TOP_LEVEL:
            if key not in top:
                self.problem(f"pack: missing top-level key {key!r}")

        route = self.text(top, "route", "pack")
        if self.expected_route is not None and route and route != self.expected_route:
            self.problem(
                f"pack.route: {route!r} does not match the requested route {self.expected_route!r}"
            )
        version = self.integer(top, "version", "pack")
        verified_at = self.iso_date(top, "verified_at", "pack") or date.min

        pack = GuidancePack(
            route=route,
            version=version,
            title=self.text(top, "title", "pack"),
            verified_at=verified_at,
            disclaimer=self.text(top, "disclaimer", "pack"),
            sources=self._sources(top.get("sources")),
            questions=self._questions(top.get("tailoring_questions")),
            route_notes=self._route_notes(top.get("routes_note")),
            requirements=self._requirements(top.get("requirements")),
            employer_responsibilities=self._responsibilities(top.get("employer_responsibilities")),
            stages=self._stages(top.get("stages")),
            documents=self._documents(top.get("documents")),
            costs=self._costs(top.get("costs")),
            maintenance_funds=self._maintenance(top.get("maintenance_funds")),
            timeline_notes=self._timeline_notes(top.get("timeline_notes")),
            pack_vs_current_guidance=self._comparison(top.get("pack_vs_current_guidance")),
        )
        _Validator(pack, self.problems).run()
        if self.problems:
            raise GuidancePackError(self.problems)
        return pack

    def _sources(self, raw: object) -> tuple[Source, ...]:
        sources = []
        for i, obj in enumerate(self.items(raw, "sources")):
            where = f"sources[{i}]"
            url = self.text(obj, "url", where)
            if url and not url.startswith("https://"):
                self.problem(f"{where}.url: expected an https URL")
            sources.append(
                Source(
                    id=self.text(obj, "id", where), title=self.text(obj, "title", where), url=url
                )
            )
        return tuple(sources)

    def _questions(self, raw: object) -> tuple[Question, ...]:
        questions = []
        for i, obj in enumerate(self.items(raw, "tailoring_questions")):
            where = f"tailoring_questions[{i}]"
            options = tuple(
                QuestionOption(
                    value=self.text(opt, "value", f"{where}.options[{j}]"),
                    label=self.text(opt, "label", f"{where}.options[{j}]"),
                )
                for j, opt in enumerate(self.items(obj.get("options", []), f"{where}.options"))
            )
            assumed = obj.get("assumed_if_unanswered")
            if "assumed_if_unanswered" not in obj:
                self.problem(f"{where}: missing assumed_if_unanswered (use null for none)")
            if assumed is not None and not isinstance(assumed, (bool, int, str)):
                self.problem(
                    f"{where}.assumed_if_unanswered: expected a boolean, integer, string or null"
                )
                assumed = None
            questions.append(
                Question(
                    id=self.text(obj, "id", where),
                    short_label=self.text(obj, "short_label", where),
                    text=self.text(obj, "text", where),
                    type=self.enum(QuestionType, obj, "type", where, QuestionType.TEXT),
                    assumed_if_unanswered=assumed,
                    why_it_matters=self.text(obj, "why_it_matters", where),
                    options=options,
                    minimum=self.optional_integer(obj, "minimum", where),
                    maximum=self.optional_integer(obj, "maximum", where),
                )
            )
        return tuple(questions)

    def _route_notes(self, raw: object) -> tuple[RouteNote, ...]:
        notes = []
        for i, obj in enumerate(self.items(raw, "routes_note")):
            where = f"routes_note[{i}]"
            notes.append(
                RouteNote(
                    id=self.text(obj, "id", where),
                    title=self.text(obj, "title", where),
                    text=self.text(obj, "text", where),
                    verification=self.verification(obj, where),
                    sources=self.source_ids(obj, where),
                    condition=self.condition(obj.get("condition"), f"{where}.condition"),
                )
            )
        return tuple(notes)

    def _requirements(self, raw: object) -> tuple[Requirement, ...]:
        requirements = []
        for i, obj in enumerate(self.items(raw, "requirements")):
            where = f"requirements[{i}]"
            category = self.text(obj, "category", where)
            if category and category not in _REQUIREMENT_CATEGORIES:
                self.problem(f"{where}.category: expected 'general' or 'circumstance'")
            condition = self.condition(obj.get("condition"), f"{where}.condition")
            if category == "general" and condition is not None:
                self.problem(f"{where}: a general requirement cannot have a condition")
            why = self.text(obj, "why_it_applies", where, required=condition is not None)
            topic = self.text(obj, "topic", where, required=False)
            if topic and topic not in _REQUIREMENT_TOPICS:
                allowed = ", ".join(sorted(_REQUIREMENT_TOPICS))
                self.problem(f"{where}.topic: {topic!r} is not one of {allowed}")
            requirements.append(
                Requirement(
                    id=self.text(obj, "id", where),
                    category=category,
                    title=self.text(obj, "title", where),
                    text=self.text(obj, "text", where),
                    verification=self.verification(obj, where),
                    sources=self.source_ids(obj, where),
                    condition=condition,
                    why_it_applies=why,
                    topic=topic,
                )
            )
        return tuple(requirements)

    def _responsibilities(self, raw: object) -> tuple[EmployerResponsibility, ...]:
        return tuple(
            EmployerResponsibility(
                id=self.text(obj, "id", f"employer_responsibilities[{i}]"),
                title=self.text(obj, "title", f"employer_responsibilities[{i}]"),
                text=self.text(obj, "text", f"employer_responsibilities[{i}]"),
                verification=self.verification(obj, f"employer_responsibilities[{i}]"),
                sources=self.source_ids(obj, f"employer_responsibilities[{i}]"),
            )
            for i, obj in enumerate(self.items(raw, "employer_responsibilities"))
        )

    def _actor_list(self, obj: Mapping[str, object], where: str) -> tuple[Actor, ...]:
        raw = obj.get("also_involves", [])
        if not isinstance(raw, list):
            self.problem(f"{where}.also_involves: expected a list")
            return ()
        actors = []
        for value in raw:
            try:
                actors.append(Actor(value))
            except ValueError:
                self.problem(f"{where}.also_involves: {value!r} is not an actor")
        return tuple(actors)

    def _stages(self, raw: object) -> tuple[Stage, ...]:
        stages = []
        for i, obj in enumerate(self.items(raw, "stages")):
            where = f"stages[{i}]"
            depends = obj.get("depends_on", [])
            if not isinstance(depends, list) or not all(isinstance(d, str) for d in depends):
                self.problem(f"{where}.depends_on: expected a list of stage ids")
                depends = []
            stages.append(
                Stage(
                    id=self.text(obj, "id", where),
                    title=self.text(obj, "title", where),
                    description=self.text(obj, "description", where),
                    actor=self.enum(Actor, obj, "actor", where, Actor.EMPLOYER),
                    depends_on=tuple(str(d) for d in depends),
                    typical_days_min=self.integer(obj, "typical_days_min", where),
                    typical_days_max=self.integer(obj, "typical_days_max", where),
                    verification=self.verification(obj, where),
                    sources=self.source_ids(obj, where),
                    also_involves=self._actor_list(obj, where),
                    condition=self.condition(obj.get("condition"), f"{where}.condition"),
                    parallel_group=self.optional_text(obj, "parallel_group", where),
                    alternative_note=self.text(obj, "alternative_note", where, required=False),
                )
            )
        return tuple(stages)

    def _documents(self, raw: object) -> tuple[Document, ...]:
        return tuple(
            Document(
                id=self.text(obj, "id", f"documents[{i}]"),
                title=self.text(obj, "title", f"documents[{i}]"),
                text=self.text(obj, "text", f"documents[{i}]"),
                why_it_applies=self.text(obj, "why_it_applies", f"documents[{i}]"),
                verification=self.verification(obj, f"documents[{i}]"),
                sources=self.source_ids(obj, f"documents[{i}]"),
                condition=self.condition(obj.get("condition"), f"documents[{i}].condition"),
            )
            for i, obj in enumerate(self.items(raw, "documents"))
        )

    def _tiers(self, obj: Mapping[str, object], where: str) -> tuple[CostTier, ...]:
        if "tiers" not in obj:
            return ()
        tiers = []
        for j, tier in enumerate(self.items(obj["tiers"], f"{where}.tiers")):
            tier_where = f"{where}.tiers[{j}]"
            condition = self.condition(tier.get("condition"), f"{tier_where}.condition")
            if condition is None:
                self.problem(f"{tier_where}: a tier needs a condition")
                condition = Condition(kind=ConditionKind.ALL)
            amount = self.money(tier, "amount", tier_where)
            note = self.text(tier, "note", tier_where, required=False)
            if amount is None and not note:
                self.problem(f"{tier_where}: a tier without an amount needs a note saying why")
            tiers.append(
                CostTier(
                    condition=condition,
                    amount=amount,
                    label=self.text(tier, "label", tier_where),
                    amount_per_period=self.money(tier, "amount_per_period", tier_where),
                    note=note,
                )
            )
        if not tiers:
            self.problem(f"{where}.tiers: expected at least one tier")
        return tuple(tiers)

    def _costs(self, raw: object) -> tuple[CostItem, ...]:
        costs = []
        for i, obj in enumerate(self.items(raw, "costs")):
            where = f"costs[{i}]"
            for key in ("basis", "payer"):
                if key not in obj:
                    self.problem(f"{where}: missing {key}")
            currency = self.text(obj, "currency", where)
            if currency and currency not in SUPPORTED_CURRENCIES:
                self.problem(f"{where}.currency: {currency!r} is not supported")
            costs.append(
                CostItem(
                    id=self.text(obj, "id", where),
                    label=self.text(obj, "label", where),
                    description=self.text(obj, "description", where),
                    basis=self.enum(Basis, obj, "basis", where, Basis.FIXED),
                    currency=currency,
                    payer=self.enum(Payer, obj, "payer", where, Payer.EMPLOYER),
                    cannot_be_recouped_from_worker=self._required_bool(
                        obj, "cannot_be_recouped_from_worker", where
                    ),
                    verification=self.verification(obj, where),
                    source=self.text(obj, "source", where),
                    verified_at=self.iso_date(obj, "verified_at", where) or date.min,
                    amount=self.money(obj, "amount", where),
                    amount_per_period=self.money(obj, "amount_per_period", where),
                    amount_min=self.money(obj, "amount_min", where),
                    amount_max=self.money(obj, "amount_max", where),
                    tiers=self._tiers(obj, where),
                    condition=self.condition(obj.get("condition"), f"{where}.condition"),
                    optional=self.boolean(obj, "optional", where, default=False),
                    effective_from=self.iso_date(obj, "effective_from", where, required=False),
                    notes=self.text(obj, "notes", where, required=False),
                )
            )
        return tuple(costs)

    def _required_bool(self, obj: Mapping[str, object], key: str, where: str) -> bool:
        if key not in obj:
            self.problem(f"{where}: missing {key}")
            return True
        return self.boolean(obj, key, where, default=True)

    def _maintenance(self, raw: object) -> MaintenanceFunds:
        where = "maintenance_funds"
        obj = self.mapping(raw, where)

        def amount(key: str) -> Decimal:
            value = self.money(obj, key, where)
            if value is None:
                self.problem(f"{where}.{key}: expected an amount")
                return Decimal(0)
            return value

        return MaintenanceFunds(
            main_applicant=amount("main_applicant"),
            partner=amount("partner"),
            first_child=amount("first_child"),
            each_further_child=amount("each_further_child"),
            days_held=self.integer(obj, "days_held", where),
            condition=self.condition(obj.get("condition"), f"{where}.condition"),
            text=self.text(obj, "text", where),
            verification=self.verification(obj, where),
            sources=self.source_ids(obj, where),
        )

    def _timeline_notes(self, raw: object) -> tuple[TimelineNote, ...]:
        return tuple(
            TimelineNote(
                id=self.text(obj, "id", f"timeline_notes[{i}]"),
                text=self.text(obj, "text", f"timeline_notes[{i}]"),
                verification=self.verification(obj, f"timeline_notes[{i}]"),
                sources=self.source_ids(obj, f"timeline_notes[{i}]"),
            )
            for i, obj in enumerate(self.items(raw, "timeline_notes"))
        )

    def _comparison(self, raw: object) -> tuple[PackComparison, ...]:
        rows = []
        for i, obj in enumerate(self.items(raw, "pack_vs_current_guidance")):
            where = f"pack_vs_current_guidance[{i}]"
            rows.append(
                PackComparison(
                    topic=self.text(obj, "topic", where),
                    reference_pack=self.text(obj, "reference_pack", where),
                    current_position=self.text(obj, "current_position", where),
                    effect_on_tool=self.text(obj, "effect_on_tool", where),
                    verification=self.enum(
                        Verification, obj, "verification", where, Verification.UNVERIFIED
                    ),
                )
            )
        return tuple(rows)


# ------------------------------------------------------------------------ validation


class _Validator:
    """Cross-reference checks run once every section has been parsed."""

    def __init__(self, pack: GuidancePack, problems: list[str]) -> None:
        self.pack = pack
        self.problems = problems
        self.source_ids = {source.id for source in pack.sources}
        self.questions = {question.id: question for question in pack.questions}

    def run(self) -> None:
        pack = self.pack
        self.unique("sources", (s.id for s in pack.sources))
        self.unique("tailoring_questions", (q.id for q in pack.questions))
        self.unique("routes_note", (n.id for n in pack.route_notes))
        self.unique("requirements", (r.id for r in pack.requirements))
        self.unique("employer_responsibilities", (r.id for r in pack.employer_responsibilities))
        self.unique("stages", (s.id for s in pack.stages))
        self.unique("documents", (d.id for d in pack.documents))
        self.unique("costs", (c.id for c in pack.costs))
        self.unique("timeline_notes", (n.id for n in pack.timeline_notes))

        for question in pack.questions:
            self.check_question(question)

        cited: list[tuple[str, tuple[str, ...], Condition | None]] = []
        cited += [(f"routes_note {n.id!r}", n.sources, n.condition) for n in pack.route_notes]
        cited += [(f"requirement {r.id!r}", r.sources, r.condition) for r in pack.requirements]
        cited += [
            (f"responsibility {r.id!r}", r.sources, None) for r in pack.employer_responsibilities
        ]
        cited += [(f"stage {s.id!r}", s.sources, s.condition) for s in pack.stages]
        cited += [(f"document {d.id!r}", d.sources, d.condition) for d in pack.documents]
        cited += [(f"timeline note {n.id!r}", n.sources, None) for n in pack.timeline_notes]
        cited.append(
            ("maintenance_funds", pack.maintenance_funds.sources, pack.maintenance_funds.condition)
        )
        for where, sources, condition in cited:
            self.check_sources(where, sources)
            self.check_condition(where, condition)

        for cost in pack.costs:
            self.check_cost(cost)
        self.check_stage_graph()

    def problem(self, message: str) -> None:
        self.problems.append(message)

    def unique(self, section: str, ids: Iterable[str]) -> None:
        seen: set[str] = set()
        for item_id in ids:
            if item_id in seen:
                self.problem(f"{section}: duplicate id {item_id!r}")
            seen.add(item_id)

    def check_sources(self, where: str, sources: Sequence[str]) -> None:
        if not sources:
            self.problem(f"{where}: needs at least one source")
        for source_id in sources:
            if source_id not in self.source_ids:
                self.problem(f"{where}: unknown source {source_id!r}")

    def check_question(self, question: Question) -> None:
        where = f"question {question.id!r}"
        if question.type is QuestionType.CHOICE and not question.options:
            self.problem(f"{where}: a choice question needs options")
        if question.type is not QuestionType.CHOICE and question.options:
            self.problem(f"{where}: only choice questions have options")
        if (
            question.minimum is not None
            and question.maximum is not None
            and question.minimum > question.maximum
        ):
            self.problem(f"{where}: minimum is above maximum")
        assumed = question.assumed_if_unanswered
        if assumed is None and question.type is not QuestionType.TEXT:
            self.problem(f"{where}: only text questions may have no assumed value")
        if assumed is not None and not answer_fits(question, assumed):
            self.problem(f"{where}: assumed value {assumed!r} does not fit the question")

    def check_condition(self, where: str, condition: Condition | None) -> None:
        if condition is None:
            return
        if condition.kind is not ConditionKind.TEST:
            for child in condition.children:
                self.check_condition(where, child)
            return
        question = self.questions.get(condition.question)
        if question is None:
            self.problem(f"{where}: condition refers to unknown question {condition.question!r}")
            return
        operator = condition.operator
        if operator is None:
            return
        if operator in (Operator.GTE, Operator.GT, Operator.LTE):
            if question.type is not QuestionType.INT:
                self.problem(
                    f"{where}: {operator.value} needs an integer question, not {question.id!r}"
                )
            elif not _is_int(condition.value):
                self.problem(f"{where}: {operator.value} needs an integer value")
            return
        values = condition.value if isinstance(condition.value, tuple) else (condition.value,)
        for value in values:
            if not answer_fits(question, value):
                self.problem(f"{where}: value {value!r} does not fit question {question.id!r}")

    def check_cost(self, cost: CostItem) -> None:
        where = f"cost {cost.id!r}"
        if cost.source not in self.source_ids:
            self.problem(f"{where}: unknown source {cost.source!r}")
        self.check_condition(where, cost.condition)
        for tier in cost.tiers:
            self.check_condition(f"{where} tier {tier.label!r}", tier.condition)
        has_range = cost.amount_min is not None or cost.amount_max is not None
        ways = sum((cost.amount is not None, has_range, bool(cost.tiers)))
        if ways != 1:
            self.problem(
                f"{where}: give exactly one of amount, amount_min and amount_max, or tiers"
            )
        if has_range:
            if cost.amount_min is None or cost.amount_max is None:
                self.problem(f"{where}: a range needs both amount_min and amount_max")
            elif cost.amount_min > cost.amount_max:
                self.problem(f"{where}: amount_min is above amount_max")
        if cost.basis is Basis.FIRST_YEAR_THEN_PER_6_MONTHS:
            if has_range:
                self.problem(f"{where}: {cost.basis.value} does not support a range")
            if cost.tiers:
                per_period = [t.amount_per_period for t in cost.tiers if t.amount is not None]
            else:
                per_period = [cost.amount_per_period]
            if any(value is None for value in per_period):
                self.problem(f"{where}: {cost.basis.value} needs amount_per_period")

    def check_stage_graph(self) -> None:
        ids = {stage.id for stage in self.pack.stages}
        for stage in self.pack.stages:
            where = f"stage {stage.id!r}"
            for dependency in stage.depends_on:
                if dependency == stage.id:
                    self.problem(f"{where}: depends on itself")
                elif dependency not in ids:
                    self.problem(f"{where}: depends on unknown stage {dependency!r}")
            if stage.typical_days_min < 0 or stage.typical_days_min > stage.typical_days_max:
                self.problem(f"{where}: needs 0 <= typical_days_min <= typical_days_max")
        cycle = _find_cycle(
            {s.id: tuple(d for d in s.depends_on if d in ids) for s in self.pack.stages}
        )
        if cycle:
            self.problem("stages: dependency cycle " + " -> ".join(cycle))


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def answer_fits(question: Question, value: object) -> bool:
    """Whether ``value`` is a valid answer to ``question``."""
    match question.type:
        case QuestionType.BOOL:
            return isinstance(value, bool)
        case QuestionType.INT:
            if not isinstance(value, int) or isinstance(value, bool):
                return False
            above_minimum = question.minimum is None or value >= question.minimum
            below_maximum = question.maximum is None or value <= question.maximum
            return above_minimum and below_maximum
        case QuestionType.CHOICE:
            return isinstance(value, str) and value in {option.value for option in question.options}
        case QuestionType.TEXT:
            return isinstance(value, str)


def _find_cycle(graph: Mapping[str, tuple[str, ...]]) -> list[str]:
    """Return one dependency cycle as a list of ids, or an empty list."""
    state: dict[str, int] = {}  # 1 visiting, 2 done
    path: list[str] = []

    def visit(node: str) -> list[str]:
        state[node] = 1
        path.append(node)
        for nxt in graph.get(node, ()):
            if state.get(nxt) == 1:
                return [*path[path.index(nxt) :], nxt]
            if nxt not in state:
                found = visit(nxt)
                if found:
                    return found
        path.pop()
        state[node] = 2
        return []

    for node in graph:
        if node not in state:
            found = visit(node)
            if found:
                return found
    return []
