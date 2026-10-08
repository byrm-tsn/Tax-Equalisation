"""The one service every entry point uses: HTML views, the JSON API and the command line.

* :func:`estimate` runs the engine on the bundled rate sets for an explicit rates date.
* :func:`encode_scenario` and :func:`read_scenario_link` carry a scenario, its rates date
  and the engine version that made the link in a compact, URL-safe string, so a result
  page is reloadable, shareable and reproducible with no database.
  :func:`decode_scenario` is the short form returning only the inputs and the date.
* :func:`rates_date_problem` bounds every rates date the tool accepts.
* :func:`immigration_panel` builds the guidance panel for a scenario.
* :func:`today` is the only clock in the application (the engine has none).
"""

from __future__ import annotations

import base64
import binascii
import dataclasses
import json
import re
import zlib
from collections.abc import Mapping
from datetime import date
from typing import Final

from django.utils import timezone
from pydantic import ValidationError

from teq_engine import ENGINE_VERSION, CalculationResult, ScenarioInput, calculate, default_provider
from teq_engine.types import canonical_json
from teq_guidance import TailoringAnswers, TailoringError, build_panel
from teq_web.formatting import uk_date

__all__ = [
    "MAX_ENCODED_LENGTH",
    "RATES_DATE_MAX",
    "RATES_DATE_MIN",
    "ScenarioLink",
    "ScenarioLinkError",
    "ScenarioTooLargeError",
    "build_immigration",
    "decode_scenario",
    "default_tailoring",
    "encode_scenario",
    "estimate",
    "immigration_panel",
    "rates_date_problem",
    "read_scenario_link",
    "tailoring_requested",
    "today",
]

MAX_ENCODED_LENGTH: Final = 8 * 1024
"""Longest accepted encoded scenario, in characters (the encoding is ASCII, so bytes)."""

_MAX_JSON_BYTES: Final = 64 * 1024
"""Decompression limit, so a small token cannot expand into a large payload."""

_FORMAT_VERSION: Final = 1

RATES_DATE_MIN: Final = date(2000, 1, 1)
"""Earliest rates date accepted anywhere (form, link, API, command line)."""

RATES_DATE_MAX: Final = date(2100, 12, 31)
"""Latest rates date accepted anywhere; later dates would push the tax years out of range."""

_TOKEN_ALPHABET: Final = re.compile(r"[A-Za-z0-9_-]+={0,2}")
"""URL-safe base64 (links are unpadded; padding is tolerated): anything else in a link is
damage or tampering."""

_ENGINE_VERSION_FORMAT: Final = re.compile(r"[0-9A-Za-z][0-9A-Za-z.+_-]{0,39}")

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


def rates_date_problem(value: date) -> str | None:
    """Why ``value`` cannot be a rates date, in plain English; ``None`` when it can."""
    if RATES_DATE_MIN <= value <= RATES_DATE_MAX:
        return None
    return (
        f"The rates date must be between {uk_date(RATES_DATE_MIN)} and {uk_date(RATES_DATE_MAX)}."
    )


def estimate(inputs: ScenarioInput, *, rates_as_of: date) -> CalculationResult:
    """Calculate the employer cost of ``inputs`` under the rates in force on ``rates_as_of``.

    Raises the engine's typed errors (unsupported route or region, rates unavailable,
    solver failure) unchanged; callers decide how to show them.
    """
    return calculate(inputs, default_provider(), rates_as_of=rates_as_of)


# --------------------------------------------------------------------------- URL encoding


def encode_scenario(
    inputs: ScenarioInput, rates_as_of: date, *, engine_version: str | None = ENGINE_VERSION
) -> str:
    """Encode validated inputs, their rates date and the engine version as a URL-safe string.

    The payload is the canonical JSON of the inputs, the date and the version of the
    engine that made the link (``None`` leaves it out, as links made before it was
    recorded did), compressed with zlib and encoded as unpadded URL-safe base64. The
    encoding is deterministic. Raises :class:`ScenarioTooLargeError` above
    :data:`MAX_ENCODED_LENGTH` characters.
    """
    data: dict[str, object] = {
        "v": _FORMAT_VERSION,
        "rates_as_of": rates_as_of.isoformat(),
        "inputs": inputs.model_dump(mode="json"),
    }
    if engine_version is not None:
        data["engine_version"] = engine_version
    compressed = zlib.compress(canonical_json(data).encode("utf-8"), 9)
    token = base64.urlsafe_b64encode(compressed).rstrip(b"=").decode("ascii")
    if len(token) > MAX_ENCODED_LENGTH:
        raise ScenarioTooLargeError(
            f"This scenario is too large to carry in a link ({len(token):,} characters; "
            f"the limit is {MAX_ENCODED_LENGTH:,}). Use fewer or shorter items.",
            reason="too_large",
        )
    return token


@dataclasses.dataclass(frozen=True, slots=True)
class ScenarioLink:
    """A decoded scenario link: the inputs, the rates date and the engine that made it."""

    inputs: ScenarioInput
    rates_as_of: date
    engine_version: str | None
    """The engine version recorded in the link; ``None`` for links made before it was."""
    source: str
    """The link as received (already checked to hold only URL-safe base64 characters)."""

    @property
    def token(self) -> str:
        """The canonical encoding of this link, to echo back in pages and URLs.

        It equals :attr:`source` for any link this tool made; a hand-made link is
        re-encoded, so nothing from the request is reflected as given. Falls back to
        :attr:`source` only if the canonical form would exceed the size limit.
        """
        try:
            return encode_scenario(
                self.inputs, self.rates_as_of, engine_version=self.engine_version
            )
        except ScenarioTooLargeError:
            return self.source

    @property
    def engine_notice(self) -> str | None:
        """A note when the link was made under another engine version, else ``None``."""
        if self.engine_version is None or self.engine_version == ENGINE_VERSION:
            return None
        return (
            f"Recalculated under engine {ENGINE_VERSION}; the link was created under "
            f"engine {self.engine_version}."
        )


def read_scenario_link(token: str) -> ScenarioLink:
    """Decode a string from :func:`encode_scenario` back to a :class:`ScenarioLink`.

    Raises :class:`ScenarioLinkError` (with a plain-English ``message``) when the string
    is too long, holds characters outside URL-safe base64, is damaged or has data after
    the compressed stream, is from an unknown format version, carries a rates date
    outside :data:`RATES_DATE_MIN` to :data:`RATES_DATE_MAX`, or holds invalid inputs.
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
    if not _TOKEN_ALPHABET.fullmatch(token):
        raise ScenarioLinkError(damaged, reason="not_base64")
    body = token.rstrip("=")
    try:
        raw = base64.b64decode(body + "=" * (-len(body) % 4), altchars=b"-_", validate=True)
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
        if inflater.unused_data:
            raise ScenarioLinkError(damaged, reason="trailing_data")
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
    engine_version = payload.get("engine_version")
    if engine_version is not None and not (
        isinstance(engine_version, str) and _ENGINE_VERSION_FORMAT.fullmatch(engine_version)
    ):
        raise ScenarioLinkError(damaged, reason="bad_engine_version")
    try:
        rates_as_of = date.fromisoformat(str(payload.get("rates_as_of")))
    except ValueError as exc:
        raise ScenarioLinkError(
            "The rates date in this link is not a valid date.", reason="bad_date"
        ) from exc
    problem = rates_date_problem(rates_as_of)
    if problem is not None:
        raise ScenarioLinkError(
            f"This link cannot be used: {problem[0].lower()}{problem[1:]}",
            reason="date_out_of_range",
        )
    try:
        inputs = ScenarioInput.model_validate(payload.get("inputs"))
    except ValidationError as exc:
        first = exc.errors()[0]
        where = "/".join(str(part) for part in first["loc"]) or "the scenario"
        raise ScenarioLinkError(
            f"The scenario in this link is not valid ({where}: {first['msg']}).",
            reason="invalid_inputs",
        ) from exc
    return ScenarioLink(
        inputs=inputs, rates_as_of=rates_as_of, engine_version=engine_version, source=token
    )


def decode_scenario(token: str) -> tuple[ScenarioInput, date]:
    """The inputs and rates date of a link (see :func:`read_scenario_link`)."""
    link = read_scenario_link(token)
    return link.inputs, link.rates_as_of


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
