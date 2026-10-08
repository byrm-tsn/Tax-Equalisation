"""The calculation trace: ordered, numbered steps a specialist can follow.

Each step has a stable id (``t1``, ``t2``, ...), a step name, a plain description,
HMRC or other references, and display values. Result lines point at the step that
produced them through ``trace_ref``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from decimal import ROUND_HALF_UP, Decimal

from teq_engine.money import PENNY, engine_context
from teq_engine.types import TraceStep

__all__ = ["TraceBuilder", "fmt_money", "fmt_number", "fmt_percent", "fmt_rate"]

type TraceValue = str | Decimal | int | bool | None


def fmt_rate(value: Decimal) -> str:
    """Render a rate without trailing zeros or exponent (``0.4700`` -> ``0.47``)."""
    with engine_context():
        return format(value.normalize(), "f")


def fmt_number(value: Decimal) -> str:
    """Render any decimal in fixed-point notation without trailing zeros."""
    return fmt_rate(value)


def fmt_money(value: Decimal) -> str:
    """Render an amount to the penny, half-up (``66000.26``)."""
    with engine_context():
        return str(value.quantize(PENNY, rounding=ROUND_HALF_UP))


def fmt_percent(rate: Decimal) -> str:
    """Render a rate as a percentage (``0.45`` -> ``45%``, ``0.075`` -> ``7.5%``)."""
    with engine_context():
        return fmt_rate(rate * 100) + "%"


def _render(value: TraceValue) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, Decimal):
        return str(value)
    return str(value)


class TraceBuilder:
    """Collects trace steps and numbers them in order."""

    def __init__(self) -> None:
        self._steps: list[TraceStep] = []

    def add(
        self,
        step: str,
        description: str,
        *,
        assignment_year: int | None = None,
        refs: Iterable[str] = (),
        values: Mapping[str, TraceValue] | None = None,
    ) -> str:
        """Append a step and return its id."""
        step_id = f"t{len(self._steps) + 1}"
        self._steps.append(
            TraceStep(
                id=step_id,
                step=step,
                description=description,
                assignment_year=assignment_year,
                refs=tuple(refs),
                values={key: _render(value) for key, value in (values or {}).items()},
            )
        )
        return step_id

    def steps(self) -> tuple[TraceStep, ...]:
        """All steps so far, in order."""
        return tuple(self._steps)

    def __len__(self) -> int:
        return len(self._steps)
