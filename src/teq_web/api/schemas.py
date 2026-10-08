"""Request and response models for the API (they also document it in OpenAPI).

The request body is exactly the engine's ``ScenarioInput`` plus ``rates_as_of`` and
``options``: the same strict model, so money must be decimal strings, unknown keys are
refused and every cross-field rule applies.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_core import PydanticCustomError

from teq_engine import CalculationResult, ScenarioInput
from teq_engine.types import IsoDate, TraceStep
from teq_web.scenarios.services import rates_date_problem

__all__ = [
    "EstimateOptions",
    "EstimateRequest",
    "EstimateResponse",
    "Note",
    "Problem",
    "ProblemError",
    "RoutesResponse",
    "WarningsResponse",
]

TailoringValue = str | int | bool | None


class EstimateOptions(BaseModel):
    """What to include in the response besides the result."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    include_trace: bool = Field(default=True, description="Include the calculation trace.")
    include_narrative: bool = Field(
        default=False, description="Include the plain-English narrative."
    )
    include_immigration: bool = Field(
        default=False,
        description="Include the immigration guidance panel (never added to the cost).",
    )
    tailoring: dict[str, TailoringValue] | None = Field(
        default=None,
        description=(
            "Answers to the immigration tailoring questions, by question id (see the "
            "panel's tailoring.questions). Omitted: the reference answers with the visa "
            "length set to the assignment length."
        ),
    )


class EstimateRequest(ScenarioInput):
    """Scenario inputs (exactly as the engine's ``ScenarioInput``), the rates date and options."""

    rates_as_of: IsoDate | None = Field(
        default=None,
        description=(
            "The date whose rates apply to year 1 (ISO YYYY-MM-DD, from 2000-01-01 to "
            "2100-12-31). Default: today."
        ),
    )
    options: EstimateOptions = EstimateOptions()

    @field_validator("rates_as_of")
    @classmethod
    def _rates_date_in_range(cls, value: date | None) -> date | None:
        problem = None if value is None else rates_date_problem(value)
        if problem is not None:
            raise PydanticCustomError("date_out_of_range", problem)
        return value

    def scenario_data(self) -> dict[str, Any]:
        """The scenario part of the request as JSON-compatible data."""
        return self.model_dump(mode="json", exclude={"rates_as_of", "options"})


class Note(BaseModel):
    """A note about how the response was produced (not a calculation warning)."""

    severity: Literal["info"] = "info"
    code: str = Field(description="For example LINK_ENGINE_VERSION_CHANGED.")
    text: str


class EstimateResponse(CalculationResult):
    """The calculation result, plus the narrative and immigration panel when requested.

    ``trace`` is omitted when ``include_trace`` is false; ``notes`` is present only when
    there is something to note (for example a link made under another engine version).
    """

    trace: tuple[TraceStep, ...] = Field(
        default=(),
        description="The calculation trace; omitted when include_trace is false.",
    )
    narrative: list[str] | None = None
    immigration: dict[str, Any] | None = None
    notes: list[Note] | None = None


class ProblemError(BaseModel):
    pointer: str = Field(
        description=(
            "JSON pointer into the request body, or for a query parameter the parameter "
            "name (tailoring answers as /tailoring/<question id>)."
        )
    )
    message: str
    location: Literal["body", "query"] | None = Field(
        default=None, description="Set to query when the error is in a query parameter."
    )


class Problem(BaseModel):
    """RFC 9457 problem details."""

    type: str
    title: str
    status: int
    detail: str
    code: str
    engine_version: str
    instance: str | None = None
    errors: list[ProblemError] = []
    engine_code: str | None = Field(
        default=None, description="The engine's own error code, when the engine refused."
    )
    reason: str | None = Field(
        default=None, description="Why a scenario link could not be read (for example too_large)."
    )
    supported_routes: list[dict[str, Any]] | None = None
    what_adding_it_requires: list[str] | None = None


class RouteEntry(BaseModel):
    home: str
    host: str
    regions: list[str]
    description: str


class RoutesResponse(BaseModel):
    engine_version: str
    routes: list[RouteEntry]


class CatalogueEntry(BaseModel):
    code: str
    kind: str
    title: str
    template: str
    question: str


class WarningsResponse(BaseModel):
    engine_version: str
    codes: list[CatalogueEntry]
