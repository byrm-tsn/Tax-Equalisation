"""What the tool supports, and what adding an unsupported route or region would take.

The engine refuses an unsupported route with a typed error; this module turns that
refusal into the plain-English explanation the web page and the API problem show.
"""

from __future__ import annotations

from dataclasses import dataclass

from teq_engine import UnsupportedRegionError, UnsupportedRouteError, supported_routes

__all__ = [
    "Refusal",
    "country_name",
    "describe_refusal",
    "region_name",
    "supported_route_dicts",
]

_COUNTRY_NAMES = {
    "TR": "Turkey",
    "GB": "the United Kingdom",
    "DE": "Germany",
    "IN": "India",
    "US": "the United States",
}
_REGION_NAMES = {"ENG": "England", "SCT": "Scotland", "WLS": "Wales", "NIR": "Northern Ireland"}


def country_name(code: str) -> str:
    """A country's name for prose, falling back to its code."""
    return _COUNTRY_NAMES.get(code, code)


def region_name(code: str | None) -> str:
    """A region's name for prose, falling back to its code."""
    if not code:
        return "(no region)"
    return _REGION_NAMES.get(code, code)


def supported_route_dicts() -> list[dict[str, object]]:
    """The capability matrix as plain data, with a readable description per route."""
    return [
        {
            "home": route.home,
            "host": route.host,
            "regions": list(route.regions),
            "description": (
                f"{country_name(route.home)} to {country_name(route.host)} "
                f"({', '.join(region_name(r) for r in route.regions)})"
            ),
        }
        for route in supported_routes()
    ]


@dataclass(frozen=True, slots=True)
class Refusal:
    """A refused route or region, explained."""

    code: str
    title: str
    message: str
    requested: str
    requirements: tuple[str, ...]
    supported: tuple[dict[str, object], ...]


def describe_refusal(error: UnsupportedRouteError) -> Refusal:
    """Explain a refusal: what was asked, what is supported, and what adding it needs."""
    supported = tuple(supported_route_dicts())
    requirements: tuple[str, ...]
    if isinstance(error, UnsupportedRegionError):
        requested = (
            f"{country_name(error.home)} to {country_name(error.host)}, "
            f"region {region_name(error.region)}"
        )
        requirements = (
            f"An income tax rate set for {region_name(error.region)} (its own bands and "
            "rates for each tax year), with sources and an effective date, reviewed by a "
            "second person.",
            "National Insurance is UK-wide, so the existing National Insurance and "
            "benefit rate sets would be reused unchanged.",
            "A row for the region in the route capability matrix, mapping it to that "
            "income tax jurisdiction.",
            "Golden test cases for the region, recomputed by hand, so every figure is "
            "checked to the pound before release.",
        )
        title = "This region is not supported yet"
    else:
        requested = f"{country_name(error.home)} to {country_name(error.host)}"
        requirements = (
            f"Rate sets for the host country ({error.host}): income tax, social security "
            "and the treatment of benefits, each with sources and effective dates.",
            f"Rules for the home country ({error.home}) to work out the hypothetical tax, "
            "or a supplied figure, and its social security for the home-scheme option.",
            "Whether a social security agreement exists between the two countries, and "
            "on what conditions.",
            "An immigration guidance pack for the route: steps, documents, costs with who "
            "pays, and timings, each with a verified date and sources.",
            "A row in the route capability matrix and golden test cases checked by a "
            "specialist before release.",
        )
        title = "This route is not supported yet"
    return Refusal(
        code=error.code,
        title=title,
        message=error.message,
        requested=requested,
        requirements=requirements,
        supported=supported,
    )
