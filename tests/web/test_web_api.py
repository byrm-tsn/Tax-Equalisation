"""The JSON API: estimates, metadata endpoints and RFC 9457 problem details."""

from __future__ import annotations

import base64
import json
import re
import zlib
from datetime import date
from typing import Any

import pytest
from django.test import Client

from teq_engine import ENGINE_VERSION, REFERENCE_RATES_AS_OF, reference_example
from teq_engine.reference import reference_example_data
from teq_web.scenarios.services import encode_scenario

PROBLEM = "application/problem+json"


def _post(client: Client, body: Any) -> Any:
    payload = body if isinstance(body, str) else json.dumps(body)
    return client.post("/api/v1/estimates", data=payload, content_type="application/json")


@pytest.fixture
def body(client: Client) -> dict[str, Any]:
    response = client.get("/api/v1/reference-example")
    assert response.status_code == 200
    data: dict[str, Any] = response.json()
    return data


def _assert_problem(response: Any, status: int, code: str) -> dict[str, Any]:
    assert response.status_code == status
    assert response["Content-Type"] == PROBLEM
    data: dict[str, Any] = response.json()
    for key in ("type", "title", "status", "detail", "code", "errors", "engine_version"):
        assert key in data, key
    assert data["status"] == status
    assert data["code"] == code
    assert data["type"] == f"urn:teq:problem:{code}"
    assert "Traceback" not in response.content.decode()
    return data


def test_reference_example_is_a_valid_request_body(body: dict[str, Any]) -> None:
    assert body["rates_as_of"] == "2026-10-08"
    assert body["options"] == {
        "include_trace": True,
        "include_narrative": True,
        "include_immigration": True,
    }
    assert {k: v for k, v in body.items() if k not in ("rates_as_of", "options")} == (
        reference_example_data()
    )


def test_posting_the_reference_example_back_matches_the_html(
    client: Client, body: dict[str, Any], tax_html: str, reference_result
) -> None:
    response = _post(client, body)
    assert response.status_code == 200
    assert response["Content-Type"] == "application/json"
    assert response["X-Engine-Version"] == ENGINE_VERSION
    data = response.json()
    totals = [
        next(line["amount"] for line in year["lines"] if line["code"] == "TOTAL_EMPLOYER_COST")
        for year in data["years"]
    ]
    assert totals == ["188676", "180676"]
    assert data["totals"]["total_employer_cost"] == "369352"
    for amount in (*totals, data["totals"]["total_employer_cost"]):
        assert f"£{int(amount):,}" in tax_html
    assert data["engine_version"] == ENGINE_VERSION
    assert data["inputs_hash"] == reference_result.inputs_hash
    assert data["rates_as_of"] == "2026-10-08"
    assert data["trace"]
    assert 5 <= len(data["narrative"]) <= 8
    assert data["immigration"]["costs"]["subtotals_display"]["employer_mandatory"] == "£3,165"
    assert len(data["immigration_narrative"]) == 4
    assert data["immigration_narrative"][0].startswith("The employer moves first.")


def test_options_default_to_the_result_with_its_trace(client: Client, body: dict[str, Any]) -> None:
    del body["options"]
    data = _post(client, body).json()
    assert "trace" in data
    assert "narrative" not in data
    assert "immigration" not in data
    assert "immigration_narrative" not in data


def test_trace_can_be_left_out(client: Client, body: dict[str, Any]) -> None:
    body["options"] = {"include_trace": False}
    data = _post(client, body).json()
    assert "trace" not in data
    assert data["totals"]["total_employer_cost"] == "369352"


def test_openapi_documents_the_optional_trace_and_the_problem_fields(client: Client) -> None:
    schemas = client.get("/api/v1/openapi.json").json()["components"]["schemas"]
    response = schemas["EstimateResponse"]
    assert "trace" in response["properties"]
    assert "trace" not in response["required"]
    assert "notes" in response["properties"]
    problem = schemas["Problem"]["properties"]
    assert {"engine_code", "reason", "errors", "supported_routes"} <= set(problem)
    assert "location" in schemas["ProblemError"]["properties"]


def test_tailoring_answers_reach_the_panel(client: Client, body: dict[str, Any]) -> None:
    body["options"] = {"include_immigration": True, "tailoring": {"sponsor_licence_held": False}}
    data = _post(client, body).json()
    labels = [line["label"] for line in data["immigration"]["costs"]["lines"]]
    assert any("licence" in label.lower() for label in labels)


def test_invalid_tailoring_is_a_422(client: Client, body: dict[str, Any]) -> None:
    body["options"] = {"include_immigration": True, "tailoring": {"visa_length_years": 99}}
    data = _assert_problem(_post(client, body), 422, "invalid-tailoring")
    assert data["errors"][0]["pointer"] == "/options/tailoring/visa_length_years"


def test_hypothetical_tax_above_salary_is_a_422_with_a_pointer(
    client: Client, body: dict[str, Any]
) -> None:
    body["hypothetical_tax"]["override"] = "95000.00"
    data = _assert_problem(_post(client, body), 422, "validation-failed")
    assert {
        "pointer": "/hypothetical_tax/override",
        "message": ("the hypothetical tax must be below the annual salary in GBP"),
    } in data["errors"]


@pytest.mark.parametrize(
    ("change", "pointer"),
    [
        (lambda b: b["salary"].update(amount=90000.5), "/salary/amount"),
        (lambda b: b["salary"].update(amount=90000), "/salary/amount"),
        (lambda b: b.update(surprise=True), "/surprise"),
        (lambda b: b["items"][1].update(years=[3]), "/items/1/years"),
        (lambda b: b.update(rates_as_of="8 October"), "/rates_as_of"),
        (lambda b: b["options"].update(include_trace="yes"), "/options/include_trace"),
    ],
)
def test_validation_errors_point_at_the_field(
    client: Client, body: dict[str, Any], change: Any, pointer: str
) -> None:
    change(body)
    data = _assert_problem(_post(client, body), 422, "validation-failed")
    assert pointer in [error["pointer"] for error in data["errors"]]


def test_bare_numbers_are_refused_for_money(client: Client, body: dict[str, Any]) -> None:
    text = json.dumps(body).replace('"90000.00"', "90000.00")
    data = _assert_problem(_post(client, text), 422, "validation-failed")
    assert any("decimal strings" in error["message"] for error in data["errors"])


def test_malformed_json_is_a_400(client: Client) -> None:
    _assert_problem(_post(client, '{"route": '), 400, "malformed-json")


@pytest.mark.parametrize("payload", ["", "  \n"])
def test_an_empty_body_is_a_400(client: Client, payload: str) -> None:
    data = _assert_problem(_post(client, payload), 400, "empty-body")
    assert data["detail"].startswith("The request body is empty")


def test_a_body_over_the_limit_is_a_413(client: Client, body: dict[str, Any]) -> None:
    body["padding"] = "x" * 300_000
    data = _assert_problem(_post(client, body), 413, "request-too-large")
    assert "256 KB" in data["detail"]


def test_the_api_maps_request_data_too_big_without_the_middleware(
    client: Client, body: dict[str, Any], settings: Any
) -> None:
    # Without the size middleware Django itself raises RequestDataTooBig as the body is read.
    settings.MIDDLEWARE = [
        name for name in settings.MIDDLEWARE if not name.endswith("RequestSizeLimitMiddleware")
    ]
    body["padding"] = "x" * 300_000
    _assert_problem(_post(client, body), 413, "request-too-large")


@pytest.mark.parametrize("method", ["put", "patch", "delete"])
def test_a_wrong_method_is_a_405_problem(client: Client, method: str) -> None:
    response = getattr(client, method)("/api/v1/estimates", "{}", content_type="application/json")
    data = _assert_problem(response, 405, "method-not-allowed")
    assert {m.strip() for m in response["Allow"].split(",")} == {"GET", "POST"}
    assert data["detail"].startswith(f"{method.upper()} is not allowed here. Allowed: ")


def test_a_json_array_is_not_a_scenario(client: Client) -> None:
    _assert_problem(_post(client, "[]"), 422, "validation-failed")


@pytest.mark.parametrize(
    ("route", "engine_code", "pointer"),
    [
        ({"home": "TR", "host": "GB", "region": "SCT"}, "REGION_NOT_SUPPORTED", "/route/region"),
        ({"home": "TR", "host": "DE", "region": None}, "ROUTE_UNSUPPORTED", "/route"),
    ],
)
def test_unsupported_route_is_a_422_listing_the_supported_routes(
    client: Client, body: dict[str, Any], route: dict[str, Any], engine_code: str, pointer: str
) -> None:
    body["route"] = route
    data = _assert_problem(_post(client, body), 422, "route-not-supported")
    assert data["engine_code"] == engine_code
    assert data["supported_routes"] == [{"home": "TR", "host": "GB", "regions": ["ENG"]}]
    assert data["errors"][0]["pointer"] == pointer
    assert data["what_adding_it_requires"]


@pytest.mark.parametrize("rates_as_of", ["0001-01-01", "9999-12-31", "1999-12-31", "2101-01-01"])
def test_rates_date_out_of_range_is_a_422_at_the_field(
    client: Client, body: dict[str, Any], rates_as_of: str
) -> None:
    body["rates_as_of"] = rates_as_of
    data = _assert_problem(_post(client, body), 422, "validation-failed")
    assert data["errors"] == [
        {
            "pointer": "/rates_as_of",
            "message": "The rates date must be between 1 January 2000 and 31 December 2100.",
        }
    ]


@pytest.mark.parametrize("rates_as_of", [date(1, 1, 1), date(9999, 12, 31)])
def test_a_link_with_an_extreme_rates_date_is_a_400(client: Client, rates_as_of: date) -> None:
    token = encode_scenario(reference_example(), rates_as_of)
    data = _assert_problem(
        client.get("/api/v1/estimates", {"s": token}), 400, "invalid-scenario-link"
    )
    assert data["reason"] == "date_out_of_range"
    assert "between 1 January 2000 and 31 December 2100" in data["detail"]


def test_rates_before_any_rate_set_is_a_422(client: Client, body: dict[str, Any]) -> None:
    body["rates_as_of"] = "2020-01-01"
    data = _assert_problem(_post(client, body), 422, "rates-unavailable")
    assert data["errors"][0]["pointer"] == "/rates_as_of"


def test_rates_date_defaults_to_today(client: Client, body: dict[str, Any]) -> None:
    del body["rates_as_of"]
    data = _post(client, body).json()
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", data["rates_as_of"])


def test_get_estimate_for_a_link_matches_the_post(
    client: Client, body: dict[str, Any], reference_token: str
) -> None:
    from_link = client.get(
        "/api/v1/estimates", {"s": reference_token, "include_narrative": "true"}
    ).json()
    posted = _post(client, body).json()
    assert from_link["inputs_hash"] == posted["inputs_hash"]
    assert from_link["totals"] == posted["totals"]
    assert from_link["narrative"] == posted["narrative"]
    assert "immigration" not in from_link
    assert "immigration_narrative" not in from_link


def test_invalid_tailoring_in_the_query_points_at_the_parameter(
    client: Client, reference_token: str
) -> None:
    response = client.get(
        "/api/v1/estimates",
        {"s": reference_token, "include_immigration": "true", "visa_length_years": "99"},
    )
    data = _assert_problem(response, 422, "invalid-tailoring")
    assert data["errors"] == [
        {
            "pointer": "/tailoring/visa_length_years",
            "message": "99 is not a valid answer (expected a whole number from 1 to 10)",
            "location": "query",
        }
    ]


def _crafted_link(payload: dict[str, Any]) -> str:
    raw = zlib.compress(json.dumps(payload).encode("utf-8"), 9)
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def test_a_link_from_another_engine_version_gets_an_info_note(client: Client) -> None:
    token = _crafted_link(
        {
            "v": 1,
            "rates_as_of": REFERENCE_RATES_AS_OF.isoformat(),
            "engine_version": "0.0.1",
            "inputs": reference_example_data(),
        }
    )
    response = client.get("/api/v1/estimates", {"s": token})
    assert response.status_code == 200
    data = response.json()
    assert data["engine_version"] == ENGINE_VERSION
    assert data["totals"]["total_employer_cost"] == "369352"
    assert data["notes"] == [
        {
            "severity": "info",
            "code": "LINK_ENGINE_VERSION_CHANGED",
            "text": (
                f"Recalculated under engine {ENGINE_VERSION}; the link was created under "
                "engine 0.0.1."
            ),
        }
    ]


def test_a_link_from_this_engine_version_has_no_notes(client: Client, reference_token: str) -> None:
    data = client.get("/api/v1/estimates", {"s": reference_token}).json()
    assert "notes" not in data


def test_get_estimate_with_a_damaged_link(client: Client) -> None:
    data = _assert_problem(
        client.get("/api/v1/estimates", {"s": "nope"}), 400, "invalid-scenario-link"
    )
    assert data["reason"]
    assert data["errors"][0]["pointer"] == "/s"
    assert data["errors"][0]["location"] == "query"


def test_routes(client: Client) -> None:
    response = client.get("/api/v1/routes")
    data = response.json()
    assert response.status_code == 200
    assert data["engine_version"] == ENGINE_VERSION
    assert data["routes"] == [
        {
            "home": "TR",
            "host": "GB",
            "regions": ["ENG"],
            "description": "Turkey to the United Kingdom (England)",
        }
    ]


def test_warnings_catalogue(client: Client) -> None:
    data = client.get("/api/v1/warnings").json()
    codes = {entry["code"] for entry in data["codes"]}
    assert {
        "ROUTE_UNSUPPORTED",
        "SOCIAL_SECURITY_AGREEMENT_MAY_APPLY",
        "HYPO_TAX_OVERRIDE",
    } <= codes
    assert data["engine_version"] == ENGINE_VERSION


def test_openapi_and_docs(client: Client) -> None:
    schema = client.get("/api/v1/openapi.json")
    assert schema.status_code == 200
    paths = schema.json()["paths"]
    assert {
        "/api/v1/estimates",
        "/api/v1/routes",
        "/api/v1/reference-example",
        "/api/v1/warnings",
    } <= set(paths)
    assert client.get("/api/v1/docs").status_code == 200


def test_unknown_api_path_is_a_problem(client: Client) -> None:
    _assert_problem(client.get("/api/v1/nothing-here"), 404, "not-found")


def test_unexpected_errors_never_leak_a_traceback(
    client: Client, body: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    import teq_web.api.endpoints as endpoints

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("secret internal detail")

    monkeypatch.setattr(endpoints, "estimate", boom)
    response = _post(client, body)
    data = _assert_problem(response, 500, "internal-error")
    assert "secret internal detail" not in response.content.decode()
    assert data["detail"].startswith("An unexpected error occurred")


def test_exchange_rate_after_the_rates_date_is_a_422(client: Client, body: dict[str, Any]) -> None:
    body["fx"] = {"rate": "55.25", "as_of": "2026-11-01"}
    data = _assert_problem(_post(client, body), 422, "validation-failed")
    assert data["engine_code"] == "FX_RATE_IN_FUTURE"
    assert data["errors"][0]["pointer"] == "/fx/as_of"


def test_the_immigration_narrative_follows_the_tailoring(
    client: Client, reference_token: str
) -> None:
    data = client.get(
        "/api/v1/estimates",
        {"s": reference_token, "include_immigration": "true", "sponsor_licence_held": "false"},
    ).json()
    assert "narrative" not in data  # the tax narrative is its own option
    assert "It does not yet hold a sponsor licence" in data["immigration_narrative"][0]
    assert "the sponsor licence fee of £1,682" in data["immigration_narrative"][2]


def test_openapi_documents_the_immigration_narrative(client: Client) -> None:
    schemas = client.get("/api/v1/openapi.json").json()["components"]["schemas"]
    properties = schemas["EstimateResponse"]["properties"]
    assert "immigration_narrative" in properties
    assert "immigration_narrative" not in schemas["EstimateResponse"].get("required", [])
