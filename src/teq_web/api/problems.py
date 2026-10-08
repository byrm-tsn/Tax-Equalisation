"""RFC 9457 problem details (``application/problem+json``) and JSON responses for the API.

Every API response carries the engine version, in the ``X-Engine-Version`` header and,
for JSON objects other than a request body, as ``engine_version`` in the body.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Final

from django.http import HttpRequest, HttpResponse

from teq_engine import ENGINE_VERSION

__all__ = [
    "PROBLEM_CONTENT_TYPE",
    "PROBLEM_TITLES",
    "json_pointer",
    "json_response",
    "pointer_errors",
    "problem",
]

PROBLEM_CONTENT_TYPE: Final = "application/problem+json"

PROBLEM_TITLES: Final[dict[str, str]] = {
    "validation-failed": "The request is not a valid scenario",
    "malformed-json": "The request body is not valid JSON",
    "empty-body": "The request body is empty",
    "request-too-large": "The request body is too large",
    "route-not-supported": "The route is not supported",
    "rates-unavailable": "No rates cover this date",
    "invalid-scenario-link": "The scenario link could not be read",
    "invalid-tailoring": "The immigration tailoring answers are not valid",
    "gross-up-not-converged": "The gross-up could not be solved",
    "calculation-refused": "The scenario cannot be calculated as given",
    "not-found": "Not found",
    "method-not-allowed": "Method not allowed",
    "internal-error": "Internal error",
    "bad-request": "Bad request",
}


def json_response(
    data: Any, *, status: int = 200, content_type: str = "application/json"
) -> HttpResponse:
    """A JSON response with the engine version header. Decimals must already be strings."""
    response = HttpResponse(
        json.dumps(data, ensure_ascii=False, separators=(",", ":")),
        status=status,
        content_type=content_type,
    )
    response["X-Engine-Version"] = ENGINE_VERSION
    return response


def json_pointer(parts: Iterable[object]) -> str:
    """An RFC 6901 JSON pointer, for example ``/hypothetical_tax/override``."""
    escaped = (str(part).replace("~", "~0").replace("/", "~1") for part in parts)
    return "".join("/" + part for part in escaped)


def pointer_errors(errors: Sequence[Mapping[str, Any]], *, strip: int = 0) -> list[dict[str, str]]:
    """Pydantic-style errors as ``[{pointer, message}]``, dropping ``strip`` location parts."""
    return [
        {
            "pointer": json_pointer(tuple(error.get("loc", ()))[strip:]),
            "message": str(error.get("msg", "invalid value")),
        }
        for error in errors
    ]


def problem(
    request: HttpRequest | None,
    *,
    status: int,
    code: str,
    detail: str,
    errors: Sequence[dict[str, str]] = (),
    title: str | None = None,
    **extra: Any,
) -> HttpResponse:
    """An ``application/problem+json`` response.

    ``type`` is a URN naming the problem (``urn:teq:problem:<code>``), ``code`` repeats
    the short name for clients that switch on it, and ``errors`` lists each failing
    field as a JSON pointer into the request body with a message.
    """
    body: dict[str, Any] = {
        "type": f"urn:teq:problem:{code}",
        "title": title or PROBLEM_TITLES.get(code, "Error"),
        "status": status,
        "detail": detail,
        "code": code,
        "engine_version": ENGINE_VERSION,
        "errors": list(errors),
    }
    if request is not None:
        body["instance"] = request.path
    body.update(extra)
    return json_response(body, status=status, content_type=PROBLEM_CONTENT_TYPE)
