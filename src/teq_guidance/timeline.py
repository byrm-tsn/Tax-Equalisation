"""Critical path over the applicable timeline stages.

Stages form a dependency graph. Only the stages whose condition holds for the
answers take part. A stage that does not apply passes its own dependencies through
to the stages that depend on it, so a chain ``A -> B -> C`` with ``B`` not applying
becomes ``A -> C`` and never loses the ordering. Inherited dependencies already implied
by another dependency are dropped, so the shown graph stays minimal. The forward and
backward passes run twice, once with every stage's minimum duration and once with its
maximum, giving a range in days and weeks. The result is never a date.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from teq_guidance.model import GuidancePack, Stage, TimelineResult, TimelineStage
from teq_guidance.tailoring import TailoringAnswers, applicable

_DAYS_PER_WEEK = 7


def critical_path(pack: GuidancePack, answers: TailoringAnswers | None = None) -> TimelineResult:
    """Schedule the applicable stages and find the critical path.

    ``critical_path`` lists the stages with no slack under the maximum
    durations; ``critical_path_min`` does the same under the minimum durations.
    Totals are in calendar days; weeks round the minimum down and the maximum up,
    so the week range always contains the day range.
    """
    selected = applicable(pack, answers)
    stages = selected.stages
    ids = {stage.id for stage in stages}
    dependencies = _effective_dependencies(pack.stages, ids)
    order = _topological_order(stages, dependencies)

    min_days = {stage.id: stage.typical_days_min for stage in stages}
    max_days = {stage.id: stage.typical_days_max for stage in stages}
    start_min, finish_min, slack_min = _schedule(order, dependencies, min_days)
    start_max, finish_max, slack_max = _schedule(order, dependencies, max_days)

    total_min = max(finish_min.values(), default=0)
    total_max = max(finish_max.values(), default=0)
    critical_max = tuple(stage_id for stage_id in order if slack_max[stage_id] == 0)
    critical_min = tuple(stage_id for stage_id in order if slack_min[stage_id] == 0)

    related = _related(order, dependencies)
    timeline_stages = tuple(
        TimelineStage(
            id=stage.id,
            title=stage.title,
            actor=stage.actor,
            also_involves=stage.also_involves,
            days_min=stage.typical_days_min,
            days_max=stage.typical_days_max,
            depends_on=dependencies[stage.id],
            parallel_group=stage.parallel_group,
            earliest_start_min=start_min[stage.id],
            earliest_start_max=start_max[stage.id],
            earliest_finish_min=finish_min[stage.id],
            earliest_finish_max=finish_max[stage.id],
            slack_days_max=slack_max[stage.id],
            on_critical_path=stage.id in critical_max,
            on_critical_path_min=stage.id in critical_min,
            parallel_with=tuple(
                other.id
                for other in stages
                if other.id != stage.id and other.id not in related[stage.id]
            ),
        )
        for stage in stages
    )

    groups: dict[str, list[str]] = {}
    for stage in stages:
        if stage.parallel_group:
            groups.setdefault(stage.parallel_group, []).append(stage.id)

    return TimelineResult(
        stages=timeline_stages,
        critical_path=critical_max,
        critical_path_min=critical_min,
        total_days_min=total_min,
        total_days_max=total_max,
        total_weeks_min=total_min // _DAYS_PER_WEEK,
        total_weeks_max=-(-total_max // _DAYS_PER_WEEK),
        phases=_phases(stages, order, dependencies),
        parallel_groups=tuple((name, tuple(members)) for name, members in groups.items()),
        not_applicable=tuple(stage.id for stage in pack.stages if stage.id not in ids),
    )


def _effective_dependencies(
    all_stages: Sequence[Stage], applying: set[str]
) -> dict[str, tuple[str, ...]]:
    """Dependencies among the applying stages, passing through stages that do not apply.

    A dependency on a stage that does not apply is replaced by that stage's own
    effective dependencies (transitively). An inherited dependency that is already an
    ancestor of another dependency of the same stage is dropped as redundant.
    """
    declared = {stage.id: stage.depends_on for stage in all_stages}
    through: dict[str, tuple[str, ...]] = {}

    def resolved(stage_id: str) -> tuple[str, ...]:
        """The applying stages ``stage_id`` waits for, in declaration order."""
        if stage_id not in through:
            through[stage_id] = ()  # the loader has rejected cycles; this guards recursion
            found: list[str] = []
            for dep in declared.get(stage_id, ()):
                for target in (dep,) if dep in applying else resolved(dep):
                    if target not in found:
                        found.append(target)
            through[stage_id] = tuple(found)
        return through[stage_id]

    ancestors: dict[str, frozenset[str]] = {}

    def ancestors_of(stage_id: str) -> frozenset[str]:
        if stage_id not in ancestors:
            ancestors[stage_id] = frozenset()
            result: set[str] = set()
            for dep in resolved(stage_id):
                result.add(dep)
                result |= ancestors_of(dep)
            ancestors[stage_id] = frozenset(result)
        return ancestors[stage_id]

    effective: dict[str, tuple[str, ...]] = {}
    for stage in all_stages:
        if stage.id not in applying:
            continue
        direct = {dep for dep in stage.depends_on if dep in applying}
        deps = resolved(stage.id)
        effective[stage.id] = tuple(
            dep
            for dep in deps
            if dep in direct or not any(dep in ancestors_of(other) for other in deps)
        )
    return effective


def _topological_order(
    stages: Sequence[Stage], dependencies: Mapping[str, tuple[str, ...]]
) -> list[str]:
    """Kahn's algorithm, stable in pack order. The loader has rejected cycles."""
    remaining = [stage.id for stage in stages]
    done: set[str] = set()
    order: list[str] = []
    while remaining:
        ready = [sid for sid in remaining if all(dep in done for dep in dependencies[sid])]
        if not ready:
            raise ValueError("timeline stages contain a dependency cycle: " + ", ".join(remaining))
        for sid in ready:
            order.append(sid)
            done.add(sid)
        remaining = [sid for sid in remaining if sid not in done]
    return order


def _schedule(
    order: Sequence[str],
    dependencies: Mapping[str, tuple[str, ...]],
    durations: Mapping[str, int],
) -> tuple[dict[str, int], dict[str, int], dict[str, int]]:
    """Forward and backward passes: earliest start, earliest finish and slack."""
    start: dict[str, int] = {}
    finish: dict[str, int] = {}
    for sid in order:
        start[sid] = max((finish[dep] for dep in dependencies[sid]), default=0)
        finish[sid] = start[sid] + durations[sid]
    end = max(finish.values(), default=0)

    successors: dict[str, list[str]] = {sid: [] for sid in order}
    for sid in order:
        for dep in dependencies[sid]:
            successors[dep].append(sid)
    latest_start: dict[str, int] = {}
    for sid in reversed(order):
        latest_finish = min((latest_start[nxt] for nxt in successors[sid]), default=end)
        latest_start[sid] = latest_finish - durations[sid]
    slack = {sid: latest_start[sid] - start[sid] for sid in order}
    return start, finish, slack


def _related(
    order: Sequence[str], dependencies: Mapping[str, tuple[str, ...]]
) -> dict[str, set[str]]:
    """For each stage, the stages it depends on or that depend on it, transitively."""
    ancestors: dict[str, set[str]] = {}
    for sid in order:
        found: set[str] = set()
        for dep in dependencies[sid]:
            found.add(dep)
            found |= ancestors[dep]
        ancestors[sid] = found
    related = {sid: set(ancestors[sid]) for sid in order}
    for sid in order:
        for ancestor in ancestors[sid]:
            related[ancestor].add(sid)
    return related


def _phases(
    stages: Sequence[Stage],
    order: Sequence[str],
    dependencies: Mapping[str, tuple[str, ...]],
) -> tuple[tuple[str, ...], ...]:
    """Group stages by dependency depth: the stages in one phase can run side by
    side once the stages they depend on are done."""
    depth: dict[str, int] = {}
    for sid in order:
        depth[sid] = 1 + max((depth[dep] for dep in dependencies[sid]), default=-1)
    phases: dict[int, list[str]] = {}
    for stage in stages:
        phases.setdefault(depth[stage.id], []).append(stage.id)
    return tuple(tuple(phases[level]) for level in sorted(phases))
