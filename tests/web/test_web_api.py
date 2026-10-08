"""The JSON API: estimates, metadata endpoints and RFC 9457 problem details."""

from __future__ import annotations

import json
import re
from typing import Any

import pytest
from django.test import Client

from teq_engine import ENGINE_VERSION
from teq_engine.reference import reference_example_data

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
    client: Client, body: dict[str, Any], reference_html: str, reference_result
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
        assert f"£{int(amount):,}" in reference_html
    assert data["engine_version"] == ENGINE_VERSION
    assert data["inputs_hash"] == reference_result.inputs_hash
    assert data["rates_as_of"] == "2026-10-08"
    assert data["trace"]
    assert 5 <= len(data["narrative"]) <= 8
    assert data["immigration"]["costs"]["subtotals_display"]["employer_mandatory"] == "£3,165"


def test_options_default_to_the_result_with_its_trace(client: Client, body: dict[str, Any]) -> None:
    del body["options"]
    data = _post(client, body).json()
    assert "trace" in data
    assert "narrative" not in data
    assert "immigration" not in data


def test_trace_can_be_left_out(client: Client, body: dict[str, Any]) -> None:
    body["options"] = {"include_trace": False}
    data = _post(client, body).json()
    assert "trace" not in data
    assert data["totals"]["total_employer_cost"] == "369352"


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


def test_get_estimate_with_a_damaged_link(client: Client) -> None:
    data = _assert_problem(
        client.get("/api/v1/estimates", {"s": "nope"}), 400, "invalid-scenario-link"
    )
    assert data["reason"]


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
