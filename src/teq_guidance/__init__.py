"""Structured UK immigration guidance (Skilled Worker, Turkey to England).

Guidance is data, not code: a bundled, versioned JSON pack holds the
requirements, documents, costs (as formulas with a payer), timeline stages (as a
dependency graph), tailoring questions, sources and verification levels. The
functions here are pure: no clock, no network, no database, standard library
only. Immigration costs are never added to the employment-cost estimate.

Typical use::

    from teq_guidance import TailoringAnswers, build_panel

    panel = build_panel(TailoringAnswers.reference_example())
"""

from teq_guidance.costs import compute_costs, cost_subtotals, maintenance_funds
from teq_guidance.loader import (
    GuidancePackError,
    GuidancePackNotFoundError,
    available_routes,
    load_pack,
    parse_pack,
)
from teq_guidance.model import CostLine, CostSubtotals, GuidancePack, TimelineResult
from teq_guidance.panel import build_panel
from teq_guidance.tailoring import TailoringAnswers, TailoringError, applicable
from teq_guidance.timeline import critical_path

__all__ = [
    "CostLine",
    "CostSubtotals",
    "GuidancePack",
    "GuidancePackError",
    "GuidancePackNotFoundError",
    "TailoringAnswers",
    "TailoringError",
    "TimelineResult",
    "applicable",
    "available_routes",
    "build_panel",
    "compute_costs",
    "cost_subtotals",
    "critical_path",
    "load_pack",
    "maintenance_funds",
    "parse_pack",
]
