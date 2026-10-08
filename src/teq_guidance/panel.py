"""Build the immigration guidance panel as plain data for a template or an API.

``build_panel`` returns a dict holding only dicts, lists, strings, ints, bools
and ``None``; money is a decimal string such as ``"2640.00"`` with a display
form such as ``"£2,640"`` alongside. The panel never decides eligibility and its
costs are never added to the employment-cost estimate.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from teq_guidance.costs import (
    MAX_SINGLE_GRANT_MONTHS,
    compute_costs,
    cost_subtotals,
    grant_cap_note,
    maintenance_funds,
    priced_visa_months,
)
from teq_guidance.formatting import (
    decimal_string,
    format_money,
    format_range,
    in_decimal_context,
)
from teq_guidance.loader import load_pack
from teq_guidance.model import (
    Actor,
    Basis,
    CostLine,
    GuidancePack,
    GuidancePanel,
    Payer,
    TimelineResult,
    Verification,
)
from teq_guidance.tailoring import (
    Applicable,
    TailoringAnswers,
    applicable,
    describe_condition,
    evaluate,
    format_answer,
)
from teq_guidance.timeline import critical_path

#: Content older than this, relative to ``as_of``, shows IMMIGRATION_CONTENT_STALE.
DEFAULT_MAX_CONTENT_AGE_DAYS = 180

VERIFICATION_LABELS: dict[Verification, str] = {
    Verification.SEARCH_CONFIRMED: "Search-confirmed",
    Verification.THIRD_PARTY_REPORTED: "Third-party reported",
    Verification.FROM_KNOWLEDGE: "From knowledge, not re-checked",
    Verification.COMPUTED: "Computed",
}

VERIFICATION_DESCRIPTIONS: dict[Verification, str] = {
    Verification.SEARCH_CONFIRMED: (
        "Confirmed from search results quoting the official page; the page itself could "
        "not be opened from the build environment. Check it on the page."
    ),
    Verification.THIRD_PARTY_REPORTED: (
        "Reported by third parties (for example adviser or news summaries); check the "
        "official page before relying on it."
    ),
    Verification.FROM_KNOWLEDGE: (
        "From the author's knowledge of the rules; not re-checked against the source for this pack."
    ),
    Verification.COMPUTED: "Arithmetic on figures used by this tool.",
}

PAYER_LABELS: dict[Payer, str] = {
    Payer.EMPLOYER: "Employer",
    Payer.APPLICANT: "Applicant",
    Payer.EITHER_BY_POLICY: "Applicant in law; employer by policy",
}

ACTOR_LABELS: dict[Actor, str] = {
    Actor.EMPLOYER: "Employer",
    Actor.EMPLOYEE: "Employee",
    Actor.HOME_OFFICE: "Home Office",
    Actor.THIRD_PARTY: "Third party",
}

BASIS_LABELS: dict[Basis, str] = {
    Basis.FIXED: "Fixed",
    Basis.PER_VISA_YEAR: "Per year of the visa",
    Basis.FIRST_YEAR_THEN_PER_6_MONTHS: "First 12 months, then per 6 months",
    Basis.PER_ADULT_DEPENDANT: "Per partner",
    Basis.PER_CHILD_DEPENDANT: "Per child",
    Basis.PER_DEPENDANT_VISA_YEAR_ADULT: "Per partner per year",
    Basis.PER_DEPENDANT_VISA_YEAR_CHILD: "Per child per year",
}

SUBTOTAL_LABELS: dict[str, str] = {
    "employer_mandatory": "Employer, required (cannot be passed to the worker)",
    "employer_by_policy": "Applicant in law, often paid by the employer by policy",
    "applicant_only": "Applicant only",
    "applicant_side": "Applicant side in total (if the employer pays none of it)",
    "employer_including_policy": "Employer, if it also pays the applicant's fees by policy",
}

STATUS_LABELS: dict[str, str] = {
    "APPLIES": "Applies on these answers",
    "DOES_NOT_APPLY": "Does not apply on these answers",
    "MAY_APPLY": "May apply; not decided by this tool",
}

ELIGIBILITY_NOTE = (
    "This tool does not decide eligibility. The occupation code, its going rate and the "
    "rule that only guaranteed basic pay counts decide whether the job and salary qualify."
)

COSTS_NOTE = (
    "Immigration costs are shown separately and are never added to the employment-cost "
    "estimate. Subtotals add exact sterling amounts only; ranges, lira amounts and optional "
    "services are listed but not added."
)

TIMELINE_METHOD_NOTE = (
    "Typical ranges in calendar days, combined along the critical path of the stages that "
    "apply. A range, never a date: holidays, appointment availability and requests for "
    "more information can extend it."
)

UNANSWERED_HEADING = "What we would need to tailor this further"

type Plain = dict[str, object]


@in_decimal_context
def build_panel(
    answers: TailoringAnswers | None = None,
    *,
    pack: GuidancePack | None = None,
    as_of: date | None = None,
    max_content_age_days: int = DEFAULT_MAX_CONTENT_AGE_DAYS,
) -> dict[str, object]:
    """Everything the immigration panel shows, as plain data.

    ``answers`` defaults to all questions unanswered (the pack's assumed values
    are used and listed). ``as_of`` is the date the panel is shown; when given,
    content verified more than ``max_content_age_days`` earlier carries the
    ``IMMIGRATION_CONTENT_STALE`` warning. A visa length longer than a single grant
    (60 months) is priced as one 60-month grant and carries the
    ``VISA_LENGTH_EXCEEDS_SINGLE_GRANT`` warning. The package has no clock of its own.
    All decimal arithmetic runs in a fixed local context, whatever the host's.
    """
    pack = pack if pack is not None else load_pack()
    answers = answers if answers is not None else TailoringAnswers()
    selected = applicable(pack, answers)
    lines = compute_costs(pack, answers)
    timeline = critical_path(pack, answers)

    panel = GuidancePanel(
        route=pack.route,
        pack_id=pack.pack_id,
        title=pack.title,
        version=pack.version,
        verified_at=pack.verified_at.isoformat(),
        disclaimer=pack.disclaimer,
        warnings=[
            *_warnings(pack, as_of, max_content_age_days),
            *_grant_warnings(selected.answers.months()),
        ],
        tailoring=_tailoring(pack, selected),
        route_notes=[
            {
                "id": note.id,
                "title": note.title,
                "text": note.text,
                **_verified(note.verification),
                "sources": _sources(pack, note.sources),
            }
            for note in selected.route_notes
        ],
        eligibility=_eligibility(pack, selected),
        documents=_documents(pack, selected),
        costs=_costs(pack, lines, selected.answers.months()),
        maintenance_funds=_maintenance(pack, answers),
        timeline=_timeline(pack, timeline),
        employer_responsibilities=[
            {
                "id": item.id,
                "title": item.title,
                "text": item.text,
                **_verified(item.verification),
                "sources": _sources(pack, item.sources),
            }
            for item in pack.employer_responsibilities
        ],
        sources=[{"id": s.id, "title": s.title, "url": s.url} for s in pack.sources],
        verification_levels={
            level.value: f"{VERIFICATION_LABELS[level]}: {VERIFICATION_DESCRIPTIONS[level]}"
            for level in Verification
        },
        pack_vs_current_guidance=[
            {
                "topic": row.topic,
                "reference_pack": row.reference_pack,
                "current_position": row.current_position,
                "effect_on_tool": row.effect_on_tool,
                **_verified(row.verification),
            }
            for row in pack.pack_vs_current_guidance
        ],
    )
    return panel.to_dict()


# --------------------------------------------------------------------------- parts


def _verified(level: Verification) -> Plain:
    return {"verification": level.value, "verification_label": VERIFICATION_LABELS[level]}


def _sources(pack: GuidancePack, ids: Sequence[str]) -> list[Plain]:
    found = (pack.source(source_id) for source_id in ids)
    return [{"id": s.id, "title": s.title, "url": s.url} for s in found]


def _warnings(pack: GuidancePack, as_of: date | None, max_age: int) -> list[Plain]:
    if as_of is None:
        return []
    age = (as_of - pack.verified_at).days
    if age <= max_age:
        return []
    return [
        {
            "code": "IMMIGRATION_CONTENT_STALE",
            "severity": "warning",
            "text": (
                f"This guidance was last verified on {pack.verified_at.isoformat()}, "
                f"{age} days before {as_of.isoformat()}. Re-verify fees and timings before "
                "relying on them."
            ),
        }
    ]


def _grant_warnings(requested_months: int) -> list[Plain]:
    """A warning when the requested visa length is longer than a single grant."""
    note = grant_cap_note(requested_months)
    if not note:
        return []
    return [
        {
            "code": "VISA_LENGTH_EXCEEDS_SINGLE_GRANT",
            "severity": "warning",
            "text": (
                f"The requested visa length of {requested_months} months is longer than a "
                f"single Skilled Worker grant (at most {MAX_SINGLE_GRANT_MONTHS} months). "
                f"{note} The further application has its own fees, health surcharge and "
                "Immigration Skills Charge."
            ),
        }
    ]


def _tailoring(pack: GuidancePack, selected: Applicable) -> Plain:
    resolved = selected.answers
    questions: list[Plain] = []
    unanswered: list[Plain] = []
    for question in pack.questions:
        value = resolved[question.id]
        assumed = question.id in resolved.assumed
        questions.append(
            {
                "id": question.id,
                "short_label": question.short_label,
                "text": question.text,
                "type": question.type.value,
                "options": [{"value": o.value, "label": o.label} for o in question.options],
                "minimum": question.minimum,
                "maximum": question.maximum,
                "value": value,
                "value_label": format_answer(question, value),
                "answered": not assumed,
                "assumed": assumed,
                "why_it_matters": question.why_it_matters,
            }
        )
        if assumed:
            unanswered.append(
                {
                    "id": question.id,
                    "short_label": question.short_label,
                    "text": question.text,
                    "assumed_value": value,
                    "assumed_value_label": format_answer(question, value),
                    "why_it_matters": question.why_it_matters,
                }
            )
    return {
        "questions": questions,
        "visa_length_months": resolved.months(),
        "unanswered": unanswered,
        "unanswered_heading": UNANSWERED_HEADING,
        "answered_count": len(questions) - len(unanswered),
        "question_count": len(questions),
    }


def _eligibility(pack: GuidancePack, selected: Applicable) -> Plain:
    values = selected.answers.values
    general: list[Plain] = []
    circumstance: list[Plain] = []
    for item in pack.requirements:
        entry: Plain = {
            "id": item.id,
            "title": item.title,
            "text": item.text,
            **_verified(item.verification),
            "sources": _sources(pack, item.sources),
        }
        if item.category == "general":
            general.append(entry)
            continue
        if item.condition is None:
            status = "MAY_APPLY"
        elif evaluate(item.condition, values):
            status = "APPLIES"
        else:
            status = "DOES_NOT_APPLY"
        entry.update(
            {
                "status": status,
                "status_label": STATUS_LABELS[status],
                "why": item.why_it_applies if status == "APPLIES" else "",
                "condition_text": (
                    describe_condition(pack, item.condition) if item.condition else ""
                ),
            }
        )
        circumstance.append(entry)
    return {"note": ELIGIBILITY_NOTE, "general": general, "circumstance_dependent": circumstance}


def _documents(pack: GuidancePack, selected: Applicable) -> Plain:
    applying = {document.id for document in selected.documents}
    general: list[Plain] = []
    conditional: list[Plain] = []
    not_needed: list[Plain] = []
    for document in pack.documents:
        entry: Plain = {
            "id": document.id,
            "title": document.title,
            "text": document.text,
            "why_it_applies": document.why_it_applies,
            "condition_text": (
                describe_condition(pack, document.condition) if document.condition else ""
            ),
            **_verified(document.verification),
            "sources": _sources(pack, document.sources),
        }
        if document.condition is None:
            general.append(entry)
        elif document.id in applying:
            conditional.append(entry)
        else:
            not_needed.append(
                {
                    "id": document.id,
                    "title": document.title,
                    "condition_text": entry["condition_text"],
                }
            )
    return {"general": general, "conditional": conditional, "not_needed": not_needed}


def _cost_entry(pack: GuidancePack, line: CostLine) -> Plain:
    if line.amount is not None:
        display = format_money(line.amount, line.currency)
    elif line.amount_min is not None and line.amount_max is not None:
        display = format_range(line.amount_min, line.amount_max, line.currency)
    else:
        display = "Not held"
    source = pack.source(line.source)
    return {
        "id": line.id,
        "label": line.label,
        "description": line.description,
        "basis": line.basis.value,
        "basis_label": BASIS_LABELS[line.basis],
        "formula": line.formula,
        "currency": line.currency,
        "amount": decimal_string(line.amount) if line.amount is not None else None,
        "amount_min": decimal_string(line.amount_min) if line.amount_min is not None else None,
        "amount_max": decimal_string(line.amount_max) if line.amount_max is not None else None,
        "display": display,
        "payer": line.payer.value,
        "payer_label": PAYER_LABELS[line.payer],
        "cannot_be_recouped_from_worker": line.cannot_be_recouped_from_worker,
        "optional": line.optional,
        "in_subtotal": line.in_subtotal,
        **_verified(line.verification),
        "source": {"id": source.id, "title": source.title, "url": source.url},
        "verified_at": line.verified_at.isoformat(),
        "effective_from": line.effective_from.isoformat() if line.effective_from else None,
        "notes": line.notes,
    }


def _costs(pack: GuidancePack, lines: list[CostLine], requested_months: int) -> Plain:
    subtotals = cost_subtotals(lines)
    amounts = {
        "employer_mandatory": subtotals.employer_mandatory,
        "employer_by_policy": subtotals.employer_by_policy,
        "applicant_only": subtotals.applicant_only,
        "applicant_side": subtotals.applicant_side,
        "employer_including_policy": subtotals.employer_including_policy,
    }
    return {
        "currency": "GBP",
        "visa_months_requested": requested_months,
        "visa_months_priced": priced_visa_months(requested_months),
        "lines": [_cost_entry(pack, line) for line in lines if not line.optional],
        "optional": [_cost_entry(pack, line) for line in lines if line.optional],
        "subtotals": {key: decimal_string(value) for key, value in amounts.items()},
        "subtotals_display": {key: format_money(value) for key, value in amounts.items()},
        "subtotal_labels": dict(SUBTOTAL_LABELS),
        "listed_separately": [
            line.id for line in lines if not line.optional and not line.in_subtotal
        ],
        "separate_from_employment_cost": True,
        "note": COSTS_NOTE,
    }


def _maintenance(pack: GuidancePack, answers: TailoringAnswers) -> Plain:
    funds = maintenance_funds(pack, answers)
    spec = pack.maintenance_funds
    return {
        "applies": funds.applies,
        "amount": decimal_string(funds.amount),
        "display": format_money(funds.amount),
        "formula": funds.formula,
        "days_held": funds.days_held,
        "text": spec.text,
        "condition_text": describe_condition(pack, spec.condition) if spec.condition else "",
        **_verified(spec.verification),
        "sources": _sources(pack, spec.sources),
    }


def _days(low: int, high: int) -> str:
    return f"{low} days" if low == high else f"{low} to {high} days"


def _timeline(pack: GuidancePack, result: TimelineResult) -> Plain:
    stages: list[Plain] = []
    for placed in result.stages:
        stage = pack.stage(placed.id)
        stages.append(
            {
                "id": placed.id,
                "title": placed.title,
                "description": stage.description,
                "actor": placed.actor.value,
                "actor_label": ACTOR_LABELS[placed.actor],
                "also_involves": [actor.value for actor in placed.also_involves],
                "also_involves_labels": [ACTOR_LABELS[actor] for actor in placed.also_involves],
                "days_min": placed.days_min,
                "days_max": placed.days_max,
                "range_text": _days(placed.days_min, placed.days_max),
                "depends_on": list(placed.depends_on),
                "parallel_group": placed.parallel_group,
                "parallel_with": list(placed.parallel_with),
                "earliest_start_min": placed.earliest_start_min,
                "earliest_start_max": placed.earliest_start_max,
                "earliest_finish_min": placed.earliest_finish_min,
                "earliest_finish_max": placed.earliest_finish_max,
                "slack_days_max": placed.slack_days_max,
                "on_critical_path": placed.on_critical_path,
                "on_critical_path_min": placed.on_critical_path_min,
                "alternative_note": stage.alternative_note,
                **_verified(stage.verification),
                "sources": _sources(pack, stage.sources),
            }
        )
    weeks = (
        f"about {result.total_weeks_min} to {result.total_weeks_max} weeks"
        if result.total_weeks_min != result.total_weeks_max
        else f"about {result.total_weeks_min} weeks"
    )
    return {
        "stages": stages,
        "critical_path": list(result.critical_path),
        "critical_path_min": list(result.critical_path_min),
        "total_days_min": result.total_days_min,
        "total_days_max": result.total_days_max,
        "total_weeks_min": result.total_weeks_min,
        "total_weeks_max": result.total_weeks_max,
        "total_text": f"{weeks} ({_days(result.total_days_min, result.total_days_max)})",
        "phases": [
            {"phase": number, "stages": list(members)}
            for number, members in enumerate(result.phases, start=1)
        ],
        "parallel_groups": [
            {"group": name, "stages": list(members)} for name, members in result.parallel_groups
        ],
        "not_applicable": [
            {
                "id": stage_id,
                "title": pack.stage(stage_id).title,
                "condition_text": describe_condition(pack, pack.stage(stage_id).condition),
            }
            for stage_id in result.not_applicable
        ],
        "notes": [
            {
                "id": note.id,
                "text": note.text,
                **_verified(note.verification),
                "sources": _sources(pack, note.sources),
            }
            for note in pack.timeline_notes
        ],
        "method_note": TIMELINE_METHOD_NOTE,
    }
