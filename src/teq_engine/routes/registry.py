"""The route capability matrix.

v1 supports exactly one route: home Turkey (TR) to host Great Britain (GB), region
England (ENG). Anything else is refused with a typed error that lists the supported
routes; the engine never guesses. Scotland (SCT) is refused with
``REGION_NOT_SUPPORTED``: it has its own income tax rates (NICs are UK-wide), which are
a later addition.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from teq_engine.errors import SupportedRoute, UnsupportedRegionError, UnsupportedRouteError
from teq_engine.types import DEFAULT_REGIONS, Route
from teq_engine.warnings import Code, render

__all__ = [
    "SUPPORTED_ROUTES",
    "RouteSpec",
    "SupportedRoute",
    "UnsupportedRegionError",
    "UnsupportedRouteError",
    "resolve_route",
    "supported_routes",
]

SUPPORTED_ROUTES: Final[tuple[SupportedRoute, ...]] = (
    SupportedRoute(home="TR", host="GB", regions=("ENG",)),
)


@dataclass(frozen=True, slots=True)
class RouteSpec:
    """A supported route and the rate-set jurisdictions it uses."""

    home: str
    host: str
    region: str
    host_income_tax_jurisdiction: str
    host_jurisdiction: str
    home_jurisdiction: str


_SPECS: Final = {
    ("TR", "GB", "ENG"): RouteSpec(
        home="TR",
        host="GB",
        region="ENG",
        host_income_tax_jurisdiction="UK-ENG",
        host_jurisdiction="UK",
        home_jurisdiction="TR",
    ),
}


def supported_routes() -> tuple[SupportedRoute, ...]:
    """The capability matrix."""
    return SUPPORTED_ROUTES


def _describe(routes: tuple[SupportedRoute, ...]) -> str:
    return "; ".join(r.describe() for r in routes)


def resolve_route(route: Route) -> RouteSpec:
    """Return the spec for a supported route, or raise a typed refusal.

    A missing region defaults to the host's only supported region (England for GB).
    Raises :class:`UnsupportedRouteError` (``ROUTE_UNSUPPORTED``) for an unsupported
    country pair and :class:`UnsupportedRegionError` (``REGION_NOT_SUPPORTED``) for an
    unsupported region of a supported pair.
    """
    pair = [r for r in SUPPORTED_ROUTES if r.home == route.home and r.host == route.host]
    if not pair:
        text, _ = render(
            Code.ROUTE_UNSUPPORTED,
            {"home": route.home, "host": route.host, "supported": _describe(SUPPORTED_ROUTES)},
        )
        raise UnsupportedRouteError(
            text,
            home=route.home,
            host=route.host,
            region=route.region,
            supported_routes=SUPPORTED_ROUTES,
        )
    capability = pair[0]
    region = route.region or DEFAULT_REGIONS.get(route.host, "")
    spec = _SPECS.get((route.home, route.host, region))
    if region not in capability.regions or spec is None:
        text, _ = render(
            Code.REGION_NOT_SUPPORTED,
            {
                "region": region or "(none)",
                "host": route.host,
                "supported": _describe(SUPPORTED_ROUTES),
            },
        )
        raise UnsupportedRegionError(
            text,
            home=route.home,
            host=route.host,
            region=route.region,
            supported_routes=SUPPORTED_ROUTES,
        )
    return spec
