"""Rate-set providers.

The engine asks a :class:`RateSetProvider` for the rate set of a jurisdiction and
category in force on a date. :class:`BundledProvider` reads the YAML files packaged
under ``ratesets/data``; a database-backed provider (later stage) implements the same
protocol.

:func:`resolve` adds the carry-forward rule: when no set covers a later year, the latest
set that started on or before that date is used and the caller emits
``RATES_NOT_PUBLISHED_FOR_YEAR``. A date before every set is refused.
"""

from __future__ import annotations

import functools
import itertools
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Protocol

import yaml

from teq_engine.errors import RateSetError, RatesUnavailableError
from teq_engine.ratesets.schemas import RateSet, parse_rate_set

__all__ = [
    "BundledProvider",
    "RateSetProvider",
    "Resolved",
    "default_provider",
    "load_rate_sets_from",
    "resolve",
]


class RateSetProvider(Protocol):
    """Where the engine gets its rules from."""

    def get(self, jurisdiction: str, category: str, as_of: date) -> RateSet | None:
        """The rate set in force on ``as_of``, or ``None``."""
        ...

    def latest(self, jurisdiction: str, category: str) -> RateSet | None:
        """The rate set with the latest effective start, or ``None``."""
        ...


def _load_yaml(text: str, origin: str) -> RateSet:
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise RateSetError(f"{origin}: invalid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise RateSetError(f"{origin}: a rate set must be a mapping")
    return parse_rate_set(raw, origin=origin)


def _iter_yaml(root: Traversable | Path) -> Iterable[tuple[str, str]]:
    for country in sorted(root.iterdir(), key=lambda p: p.name):
        if not country.is_dir():
            continue
        for entry in sorted(country.iterdir(), key=lambda p: p.name):
            if entry.name.endswith((".yaml", ".yml")):
                yield f"{country.name}/{entry.name}", entry.read_text(encoding="utf-8")


def load_rate_sets_from(root: Traversable | Path) -> tuple[RateSet, ...]:
    """Load and validate every ``<country>/*.yaml`` file under ``root``."""
    return tuple(_load_yaml(text, origin) for origin, text in _iter_yaml(root))


class BundledProvider:
    """Serves rate sets from memory; by default, the YAML files packaged with the engine.

    Loading validates every set, refuses duplicate identifiers and refuses overlapping
    effective ranges within one jurisdiction and category.
    """

    def __init__(self, rate_sets: Iterable[RateSet] | None = None) -> None:
        sets = (
            tuple(rate_sets)
            if rate_sets is not None
            else load_rate_sets_from(resources.files("teq_engine.ratesets") / "data")
        )
        if not sets:
            raise RateSetError("no rate sets were loaded")
        by_key: dict[tuple[str, str], list[RateSet]] = defaultdict(list)
        seen_ids: set[tuple[str, str]] = set()
        for rate_set in sets:
            identity = (rate_set.jurisdiction, rate_set.id)
            if identity in seen_ids:
                raise RateSetError(f"duplicate rate set {rate_set.id} for {rate_set.jurisdiction}")
            seen_ids.add(identity)
            by_key[(rate_set.jurisdiction, rate_set.category.value)].append(rate_set)
        for (jurisdiction, category), group in by_key.items():
            group.sort(key=lambda rs: (rs.effective_from, rs.version))
            for earlier, later in itertools.pairwise(group):
                if earlier.effective_to is None or earlier.effective_to >= later.effective_from:
                    raise RateSetError(
                        f"overlapping effective ranges for {jurisdiction} {category}: "
                        f"{earlier.id} and {later.id}"
                    )
        self._by_key: dict[tuple[str, str], tuple[RateSet, ...]] = {
            key: tuple(group) for key, group in by_key.items()
        }

    def get(self, jurisdiction: str, category: str, as_of: date) -> RateSet | None:
        for rate_set in self._by_key.get((jurisdiction, category), ()):
            if rate_set.covers(as_of):
                return rate_set
        return None

    def latest(self, jurisdiction: str, category: str) -> RateSet | None:
        group = self._by_key.get((jurisdiction, category), ())
        return group[-1] if group else None

    def all(self) -> tuple[RateSet, ...]:
        """Every loaded rate set, ordered by jurisdiction, category and start date."""
        return tuple(rs for key in sorted(self._by_key) for rs in self._by_key[key])


@functools.cache
def default_provider() -> BundledProvider:
    """The packaged rate sets, loaded and validated once per process."""
    return BundledProvider()


@dataclass(frozen=True, slots=True)
class Resolved:
    """A rate set chosen for a date, and whether it was carried forward to it."""

    rate_set: RateSet
    carried_forward: bool


def resolve(provider: RateSetProvider, jurisdiction: str, category: str, as_of: date) -> Resolved:
    """Find the rate set for ``as_of``, carrying the latest set forward if needed.

    Raises :class:`~teq_engine.errors.RatesUnavailableError` when nothing covers the date
    and the latest set starts after it (rates are never applied backwards).
    """
    found = provider.get(jurisdiction, category, as_of)
    if found is not None:
        return Resolved(found, carried_forward=False)
    latest = provider.latest(jurisdiction, category)
    if latest is not None and latest.effective_from <= as_of:
        return Resolved(latest, carried_forward=True)
    raise RatesUnavailableError(
        f"no {category} rate set for {jurisdiction} covers {as_of.isoformat()}",
        jurisdiction=jurisdiction,
        category=category,
        as_of=as_of,
    )
