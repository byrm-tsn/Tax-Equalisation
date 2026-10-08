"""Defaults a new scenario starts from: the indicative exchange rate.

The reference pack asks the tool to work out the hypothetical tax from the Turkish rules,
which needs a lira-per-pound rate. A new form therefore starts with an indicative
mid-September 2026 rate, shown with a note to confirm it; the "calculate it from the
Turkish rules" link on the results page uses it when the scenario carries no rate of its
own. The rate is never fetched and never applied silently: a result pins it with its
date and source (``FX_RATE_USER_SUPPLIED``), and the engine flags it as stale once the
rates date is more than 30 days after it.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Final

from teq_engine.types import FxSnapshot

__all__ = [
    "INDICATIVE_FX_DATE",
    "INDICATIVE_FX_HELP",
    "INDICATIVE_FX_RATE",
    "INDICATIVE_FX_SOURCE",
    "indicative_fx_initial",
    "indicative_fx_snapshot",
]

INDICATIVE_FX_RATE: Final = Decimal("65.7")
"""Turkish lira per pound, mid-September 2026 (indicative)."""

INDICATIVE_FX_DATE: Final = date(2026, 9, 15)
"""The date the indicative rate is taken at."""

INDICATIVE_FX_SOURCE: Final = "indicative"
"""The source recorded with the rate, so a result says the rate was not confirmed."""

INDICATIVE_FX_HELP: Final = (
    "Pre-filled with an indicative mid-September 2026 rate (65.7 lira to the pound on "
    "15 September 2026): confirm it, or enter the rate you use, before relying on the "
    "result. Needed for a salary in lira, a calculated hypothetical tax or the Turkish "
    "scheme."
)


def indicative_fx_snapshot() -> FxSnapshot:
    """The indicative rate as the engine's exchange-rate snapshot."""
    return FxSnapshot(
        rate=INDICATIVE_FX_RATE, as_of=INDICATIVE_FX_DATE, source=INDICATIVE_FX_SOURCE
    )


def indicative_fx_initial() -> dict[str, Any]:
    """The indicative rate as initial values for the form's three exchange-rate fields."""
    return {
        "fx_rate": format(INDICATIVE_FX_RATE, "f"),
        "fx_date": INDICATIVE_FX_DATE,
        "fx_source": INDICATIVE_FX_SOURCE,
    }
