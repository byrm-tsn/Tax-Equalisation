"""The JSON API, version 1, on Django Ninja (mounted at ``/api/v1``; docs at ``/api/v1/docs``).

Every endpoint goes through :mod:`teq_web.scenarios.services`, the same service as the
web pages and the command line. Errors are RFC 9457 problem details: 422 for invalid
scenarios and unsupported routes, 400 for malformed JSON or an empty body, 405 for a
method an endpoint does not take, 413 for a body over the size limit, and a 500 that
never carries a traceback.
"""

from __future__ import annotations

import json
import logging
from datetime import date
from typing import Any

from django.core.exceptions import RequestDataTooBig
from django.http import Http404, HttpRequest, HttpResponse
from ninja import NinjaAPI, Query
from ninja.errors import HttpError
from ninja.errors import ValidationError as NinjaValidationError
from pydantic import BaseModel, ConfigDict, ValidationError

from teq_engine import (
    ENGINE_VERSION,
    REFERENCE_RATES_AS_OF,
    CalculationResult,
    EngineError,
    RatesUnavailableError,
    ScenarioInput,
    ScenarioValidationError,
    SolverError,
    UnsupportedRouteError,
    catalogue_as_dicts,
)
from teq_engine.reference import reference_example_data
from teq_guidance import TailoringError
from teq_web.api.problems import json_response, pointer_errors, problem
from teq_web.api.schemas import (
    EstimateOptions,
    EstimateRequest,
    EstimateResponse,
    Problem,
    RoutesResponse,
    WarningsResponse,
)
from teq_web.narration.narrator import TemplateNarrator
from teq_web.scenarios.capability import describe_refusal, supported_route_dicts
from teq_web.scenarios.services import (
    ScenarioLinkError,
    build_immigration,
    estimate,
    read_scenario_link,
    today,
)

__all__ = ["api"]

logger = logging.getLogger(__name__)

api = NinjaAPI(
    title="Tax equalisation cost estimator",
    version="1.0.0",
    description=(
        "Stateless estimates of the employer cost of a tax-equalised assignment (Turkey to "
        "England), with the immigration guidance kept separate. Money is always a decimal "
        'string such as "90000.00"; bare JSON numbers are refused. Errors are RFC 9457 '
        "problem details. An illustration, not tax or immigration advice."
    ),
    urls_namespace="api-v1",
    docs_url="/docs",
    openapi_url="/openapi.json",
)

_PROBLEMS: dict[int | str, Any] = {400: Problem, 405: Problem, 422: Problem, 500: Problem}


# --------------------------------------------------------------------------- errors


def _empty_body(request: HttpRequest) -> HttpResponse | None:
    """400 for a POST with no body (or only whitespace), rather than a list of missing fields."""
    if request.method == "POST" and not request.body.strip():
        return problem(
            request,
            status=400,
            code="empty-body",
            detail="The request body is empty: send the scenario as a JSON object.",
        )
    return None


@api.exception_handler(NinjaValidationError)
def _validation_failed(request: HttpRequest, exc: NinjaValidationError) -> HttpResponse:
    empty = _empty_body(request)
    if empty is not None:
        return empty
    errors = []
    for error in exc.errors:
        loc = tuple(error.get("loc", ()))
        where = loc[0] if loc else "body"
        # Body errors are located as ("body", "payload", ...); query errors as ("query", name).
        strip = 2 if where == "body" and len(loc) > 1 and loc[1] == "payload" else 1
        entry = pointer_errors([error], strip=strip)[0]
        if where != "body":
            entry["location"] = str(where)
        errors.append(entry)
    return problem(
        request,
        status=422,
        code="validation-failed",
        detail="The request does not describe a valid scenario; see errors.",
        errors=errors,
    )


@api.exception_handler(HttpError)
def _http_error(request: HttpRequest, exc: HttpError) -> HttpResponse:
    if exc.status_code == 400 and str(exc).startswith("Cannot parse request body"):
        empty = _empty_body(request)
        if empty is not None:
            return empty
        return problem(
            request,
            status=400,
            code="malformed-json",
            detail="The request body could not be parsed as JSON.",
        )
    return problem(request, status=exc.status_code, code="bad-request", detail=str(exc))


@api.exception_handler(RequestDataTooBig)
def _too_large(request: HttpRequest, exc: RequestDataTooBig) -> HttpResponse:
    from teq_web.web.views import request_too_large

    return request_too_large(request)


@api.exception_handler(Http404)
def _not_found(request: HttpRequest, exc: Http404) -> HttpResponse:
    return problem(request, status=404, code="not-found", detail="No such resource.")


@api.exception_handler(Exception)
def _internal_error(request: HttpRequest, exc: Exception) -> HttpResponse:
    logger.exception("API error on %s", request.path)
    return problem(
        request,
        status=500,
        code="internal-error",
        detail="An unexpected error occurred. No figures were produced.",
    )


# --------------------------------------------------------------------------- helpers


def _run(
    request: HttpRequest,
    inputs: ScenarioInput,
    rates_as_of: date,
    options: EstimateOptions,
    tailoring: dict[str, Any] | None,
    *,
    tailoring_in_query: bool = False,
    notes: list[dict[str, str]] | None = None,
) -> HttpResponse:
    """Calculate and build the response body, or the problem for a refusal.

    ``tailoring_in_query`` points tailoring errors at the query parameters
    (``/tailoring/<field>``) rather than into the request body.
    """
    try:
        result = estimate(inputs, rates_as_of=rates_as_of)
    except UnsupportedRouteError as exc:
        refusal = describe_refusal(exc)
        pointer = "/route/region" if exc.code == "REGION_NOT_SUPPORTED" else "/route"
        return problem(
            request,
            status=422,
            code="route-not-supported",
            title=refusal.title,
            detail=exc.message,
            errors=[{"pointer": pointer, "message": exc.message}],
            engine_code=exc.code,
            supported_routes=exc.supported_as_dicts(),
            what_adding_it_requires=list(refusal.requirements),
        )
    except RatesUnavailableError as exc:
        return problem(
            request,
            status=422,
            code="rates-unavailable",
            detail=exc.message,
            errors=[{"pointer": "/rates_as_of", "message": exc.message}],
            engine_code=exc.code,
        )
    except ScenarioValidationError as exc:
        return problem(
            request,
            status=422,
            code="validation-failed",
            detail=exc.message,
            errors=[{"pointer": exc.pointer, "message": exc.message}],
            engine_code=exc.code,
        )
    except SolverError as exc:
        logger.error("gross-up not solved: %s", exc.code)
        return problem(request, status=500, code="gross-up-not-converged", detail=exc.message)
    except EngineError as exc:  # a refusal added to the engine later
        return problem(
            request,
            status=422,
            code="calculation-refused",
            detail=exc.message,
            engine_code=exc.code,
        )

    body = _result_body(result, options)
    if options.include_narrative:
        body["narrative"] = TemplateNarrator().narrate(result)
    if options.include_immigration:
        try:
            body["immigration"] = build_immigration(inputs, tailoring, as_of=today())
        except TailoringError as exc:
            return problem(
                request,
                status=422,
                code="invalid-tailoring",
                detail="One or more tailoring answers are not valid; see errors.",
                errors=[
                    _tailoring_error(text, in_query=tailoring_in_query) for text in exc.problems
                ],
            )
    if notes:
        body["notes"] = notes
    return json_response(body)


def _result_body(result: CalculationResult, options: EstimateOptions) -> dict[str, Any]:
    body: dict[str, Any] = json.loads(result.to_json())
    if not options.include_trace:
        body.pop("trace", None)
    return body


def _tailoring_error(text: str, *, in_query: bool = False) -> dict[str, str]:
    """A tailoring problem (``field: message``) as an error entry.

    In a POST body the answers are under ``/options/tailoring``; on the GET endpoint they
    are query parameters, located as ``/tailoring/<field>`` in the query.
    """
    base = "/tailoring" if in_query else "/options/tailoring"
    field, _, message = text.partition(":")
    if message and field.isidentifier():
        entry = {"pointer": f"{base}/{field}", "message": message.strip()}
    else:
        entry = {"pointer": base, "message": text}
    if in_query:
        entry["location"] = "query"
    return entry


# --------------------------------------------------------------------------- endpoints


@api.post(
    "/estimates",
    response={200: EstimateResponse, **_PROBLEMS, 413: Problem},
    summary="Calculate an estimate",
    tags=["estimates"],
    url_name="estimates",
)
def create_estimate(request: HttpRequest, payload: EstimateRequest) -> HttpResponse:
    """Calculate the employer cost of a scenario. Deterministic and stateless.

    The body is the scenario exactly as `GET /reference-example` returns it: you can POST
    that body back unchanged.
    """
    try:
        inputs = ScenarioInput.model_validate(payload.scenario_data())
    except ValidationError as exc:  # pragma: no cover - the request model ran the same rules
        return problem(
            request,
            status=422,
            code="validation-failed",
            detail="The request does not describe a valid scenario; see errors.",
            errors=pointer_errors(exc.errors()),
        )
    rates_as_of = payload.rates_as_of or today()
    tailoring = payload.options.tailoring
    return _run(request, inputs, rates_as_of, payload.options, tailoring)


class LinkQuery(BaseModel):
    """Query parameters for reading an encoded scenario link."""

    model_config = ConfigDict(extra="ignore")

    s: str
    include_trace: bool = True
    include_narrative: bool = False
    include_immigration: bool = False


@api.get(
    "/estimates",
    response={200: EstimateResponse, **_PROBLEMS},
    summary="Calculate the estimate for an encoded scenario link",
    tags=["estimates"],
    url_name="estimates",
)
def estimate_from_link(request: HttpRequest, query: Query[LinkQuery]) -> HttpResponse:
    """The API representation of a results page: `s` is the encoded scenario from
    `/estimate?s=...`. Tailoring answers may be added as further query parameters. When
    the link was made under another engine version the result is recalculated under this
    one and `notes` says so."""
    try:
        link = read_scenario_link(query.s)
    except ScenarioLinkError as exc:
        return problem(
            request,
            status=400,
            code="invalid-scenario-link",
            detail=exc.message,
            errors=[{"pointer": "/s", "location": "query", "message": exc.message}],
            reason=exc.reason,
        )
    options = EstimateOptions(
        include_trace=query.include_trace,
        include_narrative=query.include_narrative,
        include_immigration=query.include_immigration,
    )
    notes = (
        [{"severity": "info", "code": "LINK_ENGINE_VERSION_CHANGED", "text": link.engine_notice}]
        if link.engine_notice
        else None
    )
    return _run(
        request,
        link.inputs,
        link.rates_as_of,
        options,
        dict(request.GET.items()),
        tailoring_in_query=True,
        notes=notes,
    )


@api.get("/routes", response=RoutesResponse, summary="Supported routes", tags=["metadata"])
def routes(request: HttpRequest) -> HttpResponse:
    """The capability matrix: the only routes the engine will calculate."""
    return json_response({"engine_version": ENGINE_VERSION, "routes": supported_route_dicts()})


@api.get(
    "/reference-example",
    response=EstimateRequest,
    summary="The reference scenario, as a request body",
    tags=["metadata"],
)
def reference_example(request: HttpRequest) -> HttpResponse:
    """A valid `POST /estimates` body for the reference pack's example (salary £90,000,
    hypothetical tax £30,000, allowance, housing and relocation; Turkey to England for
    two years), with the rates date the pack's figures were reproduced at."""
    body = {
        **reference_example_data(),
        "rates_as_of": REFERENCE_RATES_AS_OF.isoformat(),
        "options": {"include_trace": True, "include_narrative": True, "include_immigration": True},
    }
    return json_response(body)


@api.get(
    "/warnings",
    response=WarningsResponse,
    summary="Warning and assumption codes",
    tags=["metadata"],
)
def warnings(request: HttpRequest) -> HttpResponse:
    """Every warning, assumption and error code the engine can emit, with its text
    template and the question it asks the user."""
    return json_response({"engine_version": ENGINE_VERSION, "codes": catalogue_as_dicts()})
