"""The reference pack's worked example: Turkey to England for two years.

Salary 90,000 GBP; hypothetical tax 30,000 (override, includes social security); a net
cost-of-living allowance of 6,000 a year; housing of 30,000 a year provided as a
taxable benefit; relocation of 8,000 in year 1 (exempt within the cap). With rates as
of 8 October 2026 the engine reproduces the pack: year 1 total employer cost 188,676,
year 2 180,676, two-year total 369,352.
"""

from __future__ import annotations

from datetime import date
from typing import Final

from teq_engine.types import ScenarioInput

__all__ = ["REFERENCE_RATES_AS_OF", "reference_example", "reference_example_data"]

REFERENCE_RATES_AS_OF: Final = date(2026, 10, 8)


def reference_example_data() -> dict[str, object]:
    """The reference inputs as plain JSON-compatible data (as the API would receive)."""
    return {
        "route": {"home": "TR", "host": "GB", "region": "ENG"},
        "assignment": {"length_years": 2, "period_mode": "ILLUSTRATIVE_WHOLE_YEAR"},
        "salary": {"amount": "90000.00", "currency": "GBP", "frequency": "ANNUAL"},
        "hypothetical_tax": {
            "method": "OVERRIDE",
            "override": "30000.00",
            "includes_social_security": True,
            "base": "SALARY_ONLY",
        },
        "items": [
            {
                "id": "cola",
                "kind": "COLA",
                "label": "Cost-of-living allowance",
                "amount": "6000.00",
                "currency": "GBP",
                "frequency": "ANNUAL",
                "years": "ALL",
            },
            {
                "id": "housing",
                "kind": "HOUSING",
                "label": "Housing (rent paid by the employer)",
                "amount": "30000.00",
                "currency": "GBP",
                "frequency": "ANNUAL",
                "years": "ALL",
            },
            {
                "id": "relocation",
                "kind": "RELOCATION",
                "label": "Relocation",
                "amount": "8000.00",
                "currency": "GBP",
                "frequency": "ONE_OFF",
                "years": [1],
            },
        ],
        "assumptions": {"uk_resident": True, "social_security": "UK_NIC", "owr_claimed": False},
    }


def reference_example() -> ScenarioInput:
    """The reference example as validated scenario inputs."""
    return ScenarioInput.model_validate(reference_example_data())
