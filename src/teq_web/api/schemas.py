"""Request and response models for the API (they also document it in OpenAPI).

The request body is exactly the engine's ``ScenarioInput`` plus ``rates_as_of`` and
``options``: the same strict model, so money must be decimal strings, unknown keys are
refused and every cross-field rule applies.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from teq_engine import CalculationResult, ScenarioInput
from teq_engine.types import IsoDate

__all__ = [
    "EstimateOptions",
    "EstimateRequest",
    "EstimateResponse",
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
        description="The date whose rates apply to year 1 (ISO YYYY-MM-DD). Default: today.",
    )
    options: EstimateOptions = EstimateOptions()

    def scenario_data(self) -> dict[str, Any]:
        """The scenario part of the request as JSON-compatible data."""
        return self.model_dump(mode="json", exclude={"rates_as_of", "options"})


class EstimateResponse(CalculationResult):
    """The calculation result, plus the narrative and immigration panel when requested.

    ``trace`` is omitted when ``options.include_trace`` is false.
    """

    narrative: list[str] | None = None
    immigration: dict[str, Any] | None = None


class ProblemError(BaseModel):
    pointer: str = Field(description="JSON pointer into the request body.")
    message: str


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
    supported_routes: list[dict[str, Any]] | None = None


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
