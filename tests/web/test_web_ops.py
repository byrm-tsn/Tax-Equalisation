"""Operations endpoints: liveness, readiness and the golden self-test."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from django.test import Client

from teq_engine import ENGINE_VERSION, LineCode


def test_healthz(client: Client) -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"engine_version": ENGINE_VERSION, "status": "ok"}
    assert "no-cache" in response["Cache-Control"]


@pytest.mark.django_db
def test_readyz_when_everything_loads(client: Client) -> None:
    response = client.get("/readyz")
    data = response.json()
    assert response.status_code == 200
    assert data["status"] == "ready"
    assert data["checks"]["rate_sets"]["ok"] is True
    assert "UK_INCOME_TAX:2026-27:v1" in data["checks"]["rate_sets"]["ids"]
    assert data["checks"]["guidance_pack"]["ok"] is True
    assert data["checks"]["database"]["ok"] is True


@pytest.mark.django_db
def test_readyz_is_503_when_the_rate_sets_fail(
    client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    import teq_web.ops.views as ops

    def broken() -> Any:
        raise ValueError("rate set failed validation")

    monkeypatch.setattr(ops, "default_provider", broken)
    response = client.get("/readyz")
    data = response.json()
    assert response.status_code == 503
    assert data["status"] == "unavailable"
    # A stable code only: the exception text goes to the log, never into the response.
    assert data["checks"]["rate_sets"] == {"ok": False, "code": "rate_sets_unavailable"}
    assert "rate set failed validation" not in response.content.decode()
    assert "ValueError" not in response.content.decode()


def test_readyz_reports_a_missing_database_as_degraded(
    client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    import teq_web.ops.views as ops

    def no_database() -> Any:
        raise ConnectionError("database unavailable")

    monkeypatch.setattr(ops, "_check_database", no_database)
    response = client.get("/readyz")
    assert response.status_code == 200
    assert response.json()["status"] == "degraded"
    assert response.json()["checks"]["database"] == {"ok": False, "code": "database_unavailable"}
    assert "database unavailable" not in response.content.decode()


def test_selftest_passes_with_the_reference_figures(client: Client) -> None:
    response = client.get("/selftest/golden")
    data = response.json()
    assert response.status_code == 200
    assert data["status"] == "pass"
    actual = {check["check"]: check["actual"] for check in data["checks"]}
    assert actual["year 1 gross cash"] == "127762"
    assert actual["year 1 total employer cost"] == "188676"
    assert actual["year 2 total employer cost"] == "180676"
    assert actual["two-year total employer cost"] == "369352"


def test_selftest_reports_a_mismatch_as_500(
    client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    import teq_web.ops.views as ops

    wrong = (("year 1 total employer cost", 1, LineCode.TOTAL_EMPLOYER_COST, Decimal("188677")),)
    monkeypatch.setattr(ops, "GOLDEN_EXPECTATIONS", wrong)
    response = client.get("/selftest/golden")
    data = response.json()
    assert response.status_code == 500
    assert data["status"] == "fail"
    assert data["checks"] == [
        {
            "check": "year 1 total employer cost",
            "expected": "188677",
            "actual": "188676",
            "ok": False,
        }
    ]


def test_selftest_token_when_configured(client: Client, settings: Any) -> None:
    settings.SELFTEST_TOKEN = "s3cret"
    assert client.get("/selftest/golden").status_code == 403
    assert client.get("/selftest/golden", {"token": "wrong"}).status_code == 403
    assert client.get("/selftest/golden", {"token": "s3cret"}).status_code == 200
    assert client.get("/selftest/golden", headers={"X-Selftest-Token": "s3cret"}).status_code == 200


def test_ops_endpoints_refuse_post(client: Client) -> None:
    assert client.post("/healthz").status_code == 405
