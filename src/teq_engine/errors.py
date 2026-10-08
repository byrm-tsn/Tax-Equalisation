"""Typed engine errors. Each carries a catalogue code; no partial figures accompany them."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

__all__ = [
    "EngineError",
    "EngineInvariantError",
    "RateSetError",
    "RatesUnavailableError",
    "ScenarioValidationError",
    "SolverError",
    "SupportedRoute",
    "UnsupportedRegionError",
    "UnsupportedRouteError",
]


class EngineError(Exception):
    """Base class for refusals and failures the caller should show to the user."""

    code: str = "ENGINE_ERROR"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True, slots=True)
class SupportedRoute:
    """One row of the route capability matrix."""

    home: str
    host: str
    regions: tuple[str, ...]

    def describe(self) -> str:
        """Human description, for example ``TR to GB (ENG)``."""
        return f"{self.home} to {self.host} ({', '.join(self.regions)})"


class UnsupportedRouteError(EngineError):
    """The route is not in the capability matrix; the calculation is refused."""

    code = "ROUTE_UNSUPPORTED"

    def __init__(
        self,
        message: str,
        *,
        home: str,
        host: str,
        region: str | None,
        supported_routes: tuple[SupportedRoute, ...],
    ) -> None:
        super().__init__(message)
        self.home = home
        self.host = host
        self.region = region
        self.supported_routes = supported_routes

    def supported_as_dicts(self) -> list[dict[str, object]]:
        """The supported routes as plain dictionaries (for problem details)."""
        return [
            {"home": r.home, "host": r.host, "regions": list(r.regions)}
            for r in self.supported_routes
        ]


class UnsupportedRegionError(UnsupportedRouteError):
    """The countries are supported but the host region is not (Scotland in v1)."""

    code = "REGION_NOT_SUPPORTED"


class SolverError(EngineError):
    """The gross-up could not be solved by either method."""

    code = "GROSS_UP_NOT_CONVERGED"

    def __init__(
        self,
        message: str,
        *,
        net_target: str,
        taxable_benefits: str,
        assignment_year: int | None = None,
    ) -> None:
        super().__init__(message)
        self.net_target = net_target
        self.taxable_benefits = taxable_benefits
        self.assignment_year = assignment_year


class RatesUnavailableError(EngineError):
    """No rate set covers a date and none can be carried forward to it."""

    code = "RATES_UNAVAILABLE"

    def __init__(self, message: str, *, jurisdiction: str, category: str, as_of: date) -> None:
        super().__init__(message)
        self.jurisdiction = jurisdiction
        self.category = category
        self.as_of = as_of


class ScenarioValidationError(EngineError):
    """The inputs are valid on their own but cannot be calculated as given.

    Raised by :func:`~teq_engine.calculate` for a rule that depends on the calculation
    rather than on the inputs alone, such as an FX snapshot dated after the rates date
    (``FX_RATE_IN_FUTURE``). ``code`` is the catalogue error code; ``loc`` names the input
    to correct as a pydantic-style location such as ``("fx", "as_of")`` and
    :attr:`pointer` gives it as a JSON pointer (``/fx/as_of``); ``params`` holds the
    values the catalogue text and question were rendered with.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str,
        loc: tuple[str | int, ...],
        params: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.loc = loc
        self.params = dict(params or {})

    @property
    def pointer(self) -> str:
        """``loc`` as a JSON pointer, for example ``/fx/as_of``."""
        return "/" + "/".join(str(part) for part in self.loc)


class RateSetError(ValueError):
    """A rate set failed to load: missing key, bad value, overlap or checksum mismatch."""


class EngineInvariantError(AssertionError):
    """An internal invariant failed (for example a table that does not foot)."""
