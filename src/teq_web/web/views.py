"""HTML views: the input form, the reference example, the results page and error pages.

The form posts, the view validates and calculates, then redirects (303) to a GET results
URL carrying the encoded inputs and rates date (post-redirect-get), so every result is
reloadable, shareable and reproducible without a database.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from django.conf import settings
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse
from django.utils.http import urlencode
from django.views.decorators.http import require_GET, require_http_methods

from teq_engine import (
    REFERENCE_RATES_AS_OF,
    CalculationResult,
    EngineError,
    LineCode,
    RatesUnavailableError,
    ScenarioInput,
    ScenarioValidationError,
    SolverError,
    UnsupportedRouteError,
    reference_example,
)
from teq_engine.types import SocialSecurityMode
from teq_web.api.problems import problem
from teq_web.narration.narrator import TemplateNarrator
from teq_web.scenarios.capability import describe_refusal
from teq_web.scenarios.forms import (
    ItemFormSet,
    ScenarioForm,
    error_summary,
    initial_from_inputs,
    plain_engine_message,
)
from teq_web.scenarios.services import (
    ScenarioLinkError,
    ScenarioTooLargeError,
    decode_scenario,
    encode_scenario,
    estimate,
    immigration_panel,
    today,
)
from teq_web.web.presenters import results_context

__all__ = [
    "compare",
    "estimate_page",
    "example",
    "page_not_found",
    "scenario_form",
    "server_error",
]


def _html(
    request: HttpRequest, template: str, context: dict[str, Any], status: int = 200
) -> HttpResponse:
    response = render(request, template, context, status=status)
    response["Content-Security-Policy"] = settings.TEQ_CONTENT_SECURITY_POLICY
    return response


def _results_url(token: str) -> str:
    return f"{reverse('estimate')}?{urlencode({'s': token})}"


def _problem_page(
    request: HttpRequest, *, status: int, title: str, message: str, token: str | None = None
) -> HttpResponse:
    return _html(
        request,
        "web/problem.html",
        {"title": title, "message": message, "token": token},
        status=status,
    )


# --------------------------------------------------------------------------- the form


@require_http_methods(["GET", "HEAD", "POST"])
def scenario_form(request: HttpRequest) -> HttpResponse:
    """``GET /`` shows the form (pre-filled from ``?s=``); ``POST /`` calculates."""
    link_error = None
    if request.method == "POST":
        form = ScenarioForm(request.POST)
        formset = ItemFormSet(request.POST, prefix="items")
        form_ok = form.is_valid()
        rows_ok = formset.is_valid()
        if form_ok and rows_ok:
            outcome = form.to_inputs(formset)
            if outcome is not None:
                token = _calculate_and_encode(form, *outcome)
                if token is not None:
                    response = HttpResponseRedirect(_results_url(token))
                    response.status_code = 303
                    return response
    else:
        initial: dict[str, Any] = {}
        rows: list[dict[str, Any]] = []
        token = request.GET.get("s")
        if token:
            try:
                initial, rows = initial_from_inputs(*decode_scenario(token))
            except ScenarioLinkError as exc:
                link_error = exc.message
        form = ScenarioForm(initial=initial)
        formset = ItemFormSet(prefix="items", initial=rows)
    errors = error_summary(form, formset) if form.is_bound else []
    context = {
        "form": form,
        "formset": formset,
        "errors": errors,
        "refusal": form.refusal,
        "link_error": link_error,
    }
    return _html(request, "web/form.html", context)


def _calculate_and_encode(
    form: ScenarioForm, inputs: ScenarioInput, rates_as_of: date
) -> str | None:
    """Run the estimate once so refusals show on the form; return the results token."""
    try:
        estimate(inputs, rates_as_of=rates_as_of)
    except UnsupportedRouteError as exc:  # pragma: no cover - the form checks the route first
        form.refusal = describe_refusal(exc)
        form.add_error("home_country", plain_engine_message(exc.message))
        return None
    except RatesUnavailableError as exc:
        form.add_error(
            "rates_as_of",
            f"No tax rates are held for {rates_as_of.isoformat()}: choose a later date. "
            f"({exc.message})",
        )
        return None
    except ScenarioValidationError as exc:
        form.add_engine_error(exc.loc, exc.message)
        return None
    except SolverError as exc:
        form.add_error(None, f"{exc.message} Please report this; no figures are shown.")
        return None
    except EngineError as exc:  # any refusal added to the engine later
        form.add_error(None, plain_engine_message(exc.message))
        return None
    try:
        return encode_scenario(inputs, rates_as_of)
    except ScenarioTooLargeError as exc:
        form.add_error(None, exc.message)
        return None


# --------------------------------------------------------------------------- results


@require_GET
def example(request: HttpRequest) -> HttpResponse:
    """Redirect to the results of the reference example at the pack's rates date."""
    token = encode_scenario(reference_example(), REFERENCE_RATES_AS_OF)
    return HttpResponseRedirect(_results_url(token))


@require_GET
def estimate_page(request: HttpRequest) -> HttpResponse:
    """``GET /estimate?s=...``: decode, calculate and show the results."""
    token = request.GET.get("s", "")
    if not token:
        return HttpResponseRedirect(reverse("form"))
    try:
        inputs, rates_as_of = decode_scenario(token)
    except ScenarioLinkError as exc:
        return _problem_page(
            request, status=400, title="This link cannot be read", message=exc.message
        )
    try:
        result = estimate(inputs, rates_as_of=rates_as_of)
    except UnsupportedRouteError as exc:
        return _html(
            request,
            "web/unsupported.html",
            {"refusal": describe_refusal(exc), "token": token},
        )
    except RatesUnavailableError as exc:
        return _problem_page(
            request,
            status=422,
            title="No rates for this date",
            message=f"{exc.message}. Edit the inputs and choose a later rates date.",
            token=token,
        )
    except SolverError as exc:
        return _problem_page(
            request,
            status=500,
            title="The gross-up could not be solved",
            message=f"{exc.message} This is a fault in the tool; please report it.",
            token=token,
        )
    except EngineError as exc:  # ScenarioValidationError and any later refusal
        return _problem_page(
            request,
            status=422,
            title="This scenario cannot be calculated as given",
            message=f"{exc.message} Edit the inputs to correct it.",
            token=token,
        )
    panel, problems = immigration_panel(inputs, request.GET, as_of=today())
    context = results_context(
        result,
        token=token,
        panel=panel,
        tailoring_problems=problems,
        narrative=TemplateNarrator().narrate(result),
    )
    context["api_url"] = (
        reverse("api-v1:estimates")
        + "?"
        + urlencode({"s": token, "include_narrative": "true", "include_immigration": "true"})
    )
    context["edit_url"] = f"{reverse('form')}?{urlencode({'s': token})}"
    context["compare_url"] = f"{reverse('compare')}?{urlencode({'s': token})}"
    return _html(request, "web/results.html", context)


@require_GET
def compare(request: HttpRequest) -> HttpResponse:
    """The same scenario under UK National Insurance and under the Turkish scheme."""
    token = request.GET.get("s", "")
    if not token:
        return HttpResponseRedirect(reverse("form"))
    try:
        inputs, rates_as_of = decode_scenario(token)
    except ScenarioLinkError as exc:
        return _problem_page(
            request, status=400, title="This link cannot be read", message=exc.message
        )
    data = inputs.model_dump(mode="json")
    columns: list[dict[str, Any]] = []
    for mode, title in (
        (SocialSecurityMode.UK_NIC, "UK National Insurance applies"),
        (SocialSecurityMode.HOME_SCHEME_AGREEMENT, "Stays in the Turkish scheme"),
    ):
        column: dict[str, Any] = {"title": title, "mode": mode.value}
        columns.append(column)
        if mode is SocialSecurityMode.HOME_SCHEME_AGREEMENT and inputs.fx is None:
            column["problem"] = (
                "Staying in the Turkish scheme needs an exchange rate (lira per pound) and "
                "its date, to estimate the Turkish employer contributions. Edit the inputs "
                "to add one."
            )
            continue
        assumptions = {**data["assumptions"], "social_security": mode.value}
        variant = ScenarioInput.model_validate({**data, "assumptions": assumptions})
        try:
            column["result"] = estimate(variant, rates_as_of=rates_as_of)
        except UnsupportedRouteError as exc:
            return _html(
                request, "web/unsupported.html", {"refusal": describe_refusal(exc), "token": token}
            )
        except EngineError as exc:
            column["problem"] = exc.message
        else:
            column["url"] = _results_url(encode_scenario(variant, rates_as_of))
    context = {
        "token": token,
        "columns": columns,
        "rows": _compare_rows([column.get("result") for column in columns]),
        "edit_url": f"{reverse('form')}?{urlencode({'s': token})}",
        "results_url": _results_url(token),
    }
    return _html(request, "web/compare.html", context)


_LINE_ORDER = list(LineCode)
_RATIO_CODES = (LineCode.MULTIPLE_OF_SALARY, LineCode.MARGINAL_COST_PER_NET_POUND)


def _compare_rows(results: list[CalculationResult | None]) -> list[dict[str, Any]]:
    """Each year's lines side by side, in the engine's line order, then the totals."""
    available = [result for result in results if result is not None]
    if not available:
        return []
    rows: list[dict[str, Any]] = []
    for index, year in enumerate(available[0].years):
        labels = {
            line.code: line.label for result in available for line in result.years[index].lines
        }
        for code in sorted(labels, key=_LINE_ORDER.index):
            values = [
                result.years[index].line(code)
                if result is not None and result.years[index].has_line(code)
                else None
                for result in results
            ]
            rows.append(
                {
                    "year": year.assignment_year,
                    "label": labels[code],
                    "values": values,
                    "ratio": code in _RATIO_CODES,
                    "strong": code is LineCode.TOTAL_EMPLOYER_COST,
                }
            )
    rows.append(
        {
            "year": None,
            "label": "Total employer cost over the assignment",
            "values": [r.totals.total_employer_cost if r is not None else None for r in results],
            "ratio": False,
            "strong": True,
        }
    )
    return rows


# --------------------------------------------------------------------------- error pages


def page_not_found(request: HttpRequest, exception: Exception | None = None) -> HttpResponse:
    """404: problem details under ``/api/``, an HTML page elsewhere."""
    if request.path.startswith("/api/"):
        return problem(request, status=404, code="not-found", detail="No such resource.")
    return _problem_page(
        request,
        status=404,
        title="Page not found",
        message="There is no page at this address. Start from the input form.",
    )


def server_error(request: HttpRequest) -> HttpResponse:
    """500 without a traceback: problem details under ``/api/``, plain HTML elsewhere."""
    if request.path.startswith("/api/"):
        return problem(
            request,
            status=500,
            code="internal-error",
            detail="An unexpected error occurred. No figures were produced.",
        )
    response = HttpResponse(
        '<!doctype html><html lang="en-GB"><head><meta charset="utf-8"><title>Error'
        "</title></head><body><h1>Something went wrong</h1><p>No figures were produced. "
        'Please try again, or <a href="/">start from the input form</a>.</p></body></html>',
        status=500,
    )
    response["Content-Security-Policy"] = settings.TEQ_CONTENT_SECURITY_POLICY
    return response
