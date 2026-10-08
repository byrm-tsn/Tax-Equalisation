"""The one service every entry point uses: HTML views, the JSON API and the command line.

* :func:`estimate` runs the engine on the bundled rate sets for an explicit rates date.
* :func:`encode_scenario` and :func:`decode_scenario` carry a scenario and its rates date
  in a compact, URL-safe string, so a result page is reloadable, shareable and
  reproducible with no database.
* :func:`immigration_panel` builds the guidance panel for a scenario.
* :func:`today` is the only clock in the application (the engine has none).
"""

from __future__ import annotations

import base64
import binascii
import dataclasses
import json
import zlib
from collections.abc import Mapping
from datetime import date
from typing import Final

from django.utils import timezone
from pydantic import ValidationError

from teq_engine import CalculationResult, ScenarioInput, calculate, default_provider
from teq_engine.types import canonical_json
from teq_guidance import TailoringAnswers, TailoringError, build_panel

__all__ = [
    "MAX_ENCODED_LENGTH",
    "ScenarioLinkError",
    "ScenarioTooLargeError",
    "build_immigration",
    "decode_scenario",
    "default_tailoring",
    "encode_scenario",
    "estimate",
    "immigration_panel",
    "tailoring_requested",
    "today",
]

MAX_ENCODED_LENGTH: Final = 8 * 1024
"""Longest accepted encoded scenario, in characters (the encoding is ASCII, so bytes)."""

_MAX_JSON_BYTES: Final = 64 * 1024
"""Decompression limit, so a small token cannot expand into a large payload."""

_FORMAT_VERSION: Final = 1

_TAILORING_FIELDS: Final = frozenset(field.name for field in dataclasses.fields(TailoringAnswers))


class ScenarioLinkError(ValueError):
    """An encoded scenario could not be read. ``message`` is plain English for the user."""

    def __init__(self, message: str, *, reason: str) -> None:
        super().__init__(message)
        self.message = message
        self.reason = reason


class ScenarioTooLargeError(ScenarioLinkError):
    """The encoded scenario is over :data:`MAX_ENCODED_LENGTH`."""


def today() -> date:
    """Today's date in the project time zone (the default rates date)."""
    return timezone.localdate()


def estimate(inputs: ScenarioInput, *, rates_as_of: date) -> CalculationResult:
    """Calculate the employer cost of ``inputs`` under the rates in force on ``rates_as_of``.

    Raises the engine's typed errors (unsupported route or region, rates unavailable,
    solver failure) unchanged; callers decide how to show them.
    """
    return calculate(inputs, default_provider(), rates_as_of=rates_as_of)


# --------------------------------------------------------------------------- URL encoding


def encode_scenario(inputs: ScenarioInput, rates_as_of: date) -> str:
    """Encode validated inputs and their rates date as a URL-safe string.

    The payload is the canonical JSON of the inputs plus the date, compressed with zlib
    and encoded as unpadded URL-safe base64. Raises :class:`ScenarioTooLargeError` above
    :data:`MAX_ENCODED_LENGTH` characters.
    """
    payload = canonical_json(
        {
            "v": _FORMAT_VERSION,
            "rates_as_of": rates_as_of.isoformat(),
            "inputs": inputs.model_dump(mode="json"),
        }
    )
    compressed = zlib.compress(payload.encode("utf-8"), 9)
    token = base64.urlsafe_b64encode(compressed).rstrip(b"=").decode("ascii")
    if len(token) > MAX_ENCODED_LENGTH:
        raise ScenarioTooLargeError(
            f"This scenario is too large to carry in a link ({len(token):,} characters; "
            f"the limit is {MAX_ENCODED_LENGTH:,}). Use fewer or shorter items.",
            reason="too_large",
        )
    return token


def decode_scenario(token: str) -> tuple[ScenarioInput, date]:
    """Decode a string from :func:`encode_scenario` back to validated inputs and a date.

    Raises :class:`ScenarioLinkError` (with a plain-English ``message``) when the string
    is too long, damaged, from an unknown format version or holds invalid inputs.
    """
    token = token.strip()
    if not token:
        raise ScenarioLinkError("The link holds no scenario.", reason="empty")
    if len(token) > MAX_ENCODED_LENGTH:
        raise ScenarioTooLargeError(
            f"This link is too long to hold a scenario (the limit is "
            f"{MAX_ENCODED_LENGTH:,} characters).",
            reason="too_large",
        )
    damaged = "This link is damaged or incomplete: the scenario in it could not be read."
    try:
        raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
    except (binascii.Error, ValueError) as exc:
        raise ScenarioLinkError(damaged, reason="not_base64") from exc
    try:
        inflater = zlib.decompressobj()
        data = inflater.decompress(raw, _MAX_JSON_BYTES)
        if inflater.unconsumed_tail:
            raise ScenarioTooLargeError(
                "This link expands to more data than a scenario can hold.", reason="too_large"
            )
        data += inflater.flush()
        if not inflater.eof:
            raise ScenarioLinkError(damaged, reason="truncated")
    except zlib.error as exc:
        raise ScenarioLinkError(damaged, reason="not_zlib") from exc
    try:
        payload = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ScenarioLinkError(damaged, reason="not_json") from exc
    if not isinstance(payload, dict) or payload.get("v") != _FORMAT_VERSION:
        raise ScenarioLinkError(
            "This link was made by a different version of the tool and cannot be read.",
            reason="version",
        )
    try:
        rates_as_of = date.fromisoformat(str(payload.get("rates_as_of")))
    except ValueError as exc:
        raise ScenarioLinkError(
            "The rates date in this link is not a valid date.", reason="bad_date"
        ) from exc
    try:
        inputs = ScenarioInput.model_validate(payload.get("inputs"))
    except ValidationError as exc:
        first = exc.errors()[0]
        where = "/".join(str(part) for part in first["loc"]) or "the scenario"
        raise ScenarioLinkError(
            f"The scenario in this link is not valid ({where}: {first['msg']}).",
            reason="invalid_inputs",
        ) from exc
    return inputs, rates_as_of


# --------------------------------------------------------------------------- immigration


def default_tailoring(inputs: ScenarioInput) -> TailoringAnswers:
    """The reference example's tailoring answers, with the visa length set to the
    assignment length (a single grant lasts up to five years; the pack explains this)."""
    changes: dict[str, object] = {"visa_length_years": inputs.assignment.length_years}
    if "visa_length_months" in _TAILORING_FIELDS:  # a refinement; whole years here
        changes["visa_length_months"] = None
    return dataclasses.replace(TailoringAnswers.reference_example(), **changes)  # type: ignore[arg-type]


def tailoring_requested(params: Mapping[str, object]) -> bool:
    """Whether ``params`` (a query string) carries any tailoring answer."""
    return any(key in _TAILORING_FIELDS for key in params)


def build_immigration(
    inputs: ScenarioInput,
    params: Mapping[str, object] | None,
    *,
    as_of: date,
) -> dict[str, object]:
    """Build the immigration panel for a scenario; strict about the tailoring answers.

    Without any tailoring answer in ``params`` the defaults of :func:`default_tailoring`
    are used. Raises :class:`~teq_guidance.TailoringError` for an invalid answer.
    Immigration costs are never added to the employment cost.
    """
    if params is not None and tailoring_requested(params):
        answers = TailoringAnswers.from_mapping(params)
    else:
        answers = default_tailoring(inputs)
    return build_panel(answers, as_of=as_of)


def immigration_panel(
    inputs: ScenarioInput,
    params: Mapping[str, object] | None,
    *,
    as_of: date,
) -> tuple[dict[str, object], tuple[str, ...]]:
    """Like :func:`build_immigration`, but invalid answers fall back to the defaults and
    are returned as problems, so a page can always show the panel."""
    try:
        return build_immigration(inputs, params, as_of=as_of), ()
    except TailoringError as exc:
        return build_immigration(inputs, None, as_of=as_of), exc.problems
