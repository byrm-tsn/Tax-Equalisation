"""Operations endpoints: liveness, readiness and the golden self-test.

* ``/healthz``: the process is up (no dependencies checked).
* ``/readyz``: the rate sets and the immigration guidance pack load and validate, and
  the database is reachable with no unapplied migrations. Rates or guidance failing is
  503; the database failing is reported as degraded (200), because estimates need no
  database. A failed check reports a stable code such as ``rate_sets_unavailable``; the
  detail goes to the log only.
* ``/selftest/golden``: runs the reference example through the same service as every
  page and checks the reference pack's figures to the pound; 500 on any mismatch. When
  ``SELFTEST_TOKEN`` is set it must be given as ``?token=`` or ``X-Selftest-Token``.
"""

from __future__ import annotations

import hmac
import logging
from decimal import Decimal
from typing import Any, Final

from django.conf import settings
from django.db import DEFAULT_DB_ALIAS, connections
from django.db.migrations.executor import MigrationExecutor
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from teq_engine import ENGINE_VERSION, REFERENCE_RATES_AS_OF, LineCode, default_provider
from teq_engine import reference_example as reference_inputs
from teq_guidance import load_pack
from teq_web.scenarios.services import estimate

__all__ = ["GOLDEN_EXPECTATIONS", "healthz", "readyz", "selftest_golden"]

logger = logging.getLogger(__name__)

# (check name, assignment year or None for the assignment total, line code, expected).
GOLDEN_EXPECTATIONS: Final[tuple[tuple[str, int | None, LineCode, Decimal], ...]] = (
    ("year 1 gross cash", 1, LineCode.GROSS_CASH, Decimal("127762")),
    ("year 1 income tax", 1, LineCode.INCOME_TAX, Decimal("57196")),
    ("year 1 employee NICs", 1, LineCode.EMPLOYEE_NIC, Decimal("4566")),
    ("year 1 employer NICs", 1, LineCode.EMPLOYER_NIC, Decimal("18414")),
    ("year 1 Class 1A", 1, LineCode.CLASS_1A, Decimal("4500")),
    ("year 1 total employer cost", 1, LineCode.TOTAL_EMPLOYER_COST, Decimal("188676")),
    ("year 2 total employer cost", 2, LineCode.TOTAL_EMPLOYER_COST, Decimal("180676")),
    ("two-year total employer cost", None, LineCode.TOTAL_EMPLOYER_COST, Decimal("369352")),
)


def _json(data: dict[str, Any], status: int = 200) -> HttpResponse:
    response = JsonResponse({"engine_version": ENGINE_VERSION, **data}, status=status)
    response["X-Engine-Version"] = ENGINE_VERSION
    return response


@require_GET
@never_cache
def healthz(request: HttpRequest) -> HttpResponse:
    """The process is alive."""
    return _json({"status": "ok"})


def _check_rate_sets() -> dict[str, Any]:
    provider = default_provider()
    rate_sets = provider.all()
    return {"ok": bool(rate_sets), "count": len(rate_sets), "ids": [rs.id for rs in rate_sets]}


def _check_guidance() -> dict[str, Any]:
    pack = load_pack()
    return {
        "ok": True,
        "pack_id": pack.pack_id,
        "version": pack.version,
        "verified_at": pack.verified_at.isoformat(),
    }


def _check_database() -> dict[str, Any]:
    connection = connections[DEFAULT_DB_ALIAS]
    connection.ensure_connection()
    executor = MigrationExecutor(connection)
    pending = executor.migration_plan(executor.loader.graph.leaf_nodes())
    return {"ok": not pending, "vendor": connection.vendor, "pending_migrations": len(pending)}


def _run_check(name: str, check: Any) -> dict[str, Any]:
    """Run one readiness check. A failure is logged with its detail; the response carries
    only a stable code (``<check>_unavailable``), never the exception text."""
    try:
        result: dict[str, Any] = check()
    except Exception:  # readiness must report, not raise
        logger.exception("readiness check %s failed", name)
        return {"ok": False, "code": f"{name}_unavailable"}
    return result


@require_GET
@never_cache
def readyz(request: HttpRequest) -> HttpResponse:
    """Rate sets and guidance load (else 503); the database is reported, not required."""
    checks = {
        "rate_sets": _run_check("rate_sets", _check_rate_sets),
        "guidance_pack": _run_check("guidance_pack", _check_guidance),
        "database": _run_check("database", _check_database),
    }
    required_ok = checks["rate_sets"]["ok"] and checks["guidance_pack"]["ok"]
    if not required_ok:
        status = "unavailable"
    elif not checks["database"]["ok"]:
        status = "degraded"
    else:
        status = "ready"
    return _json({"status": status, "checks": checks}, status=200 if required_ok else 503)


def _token_ok(request: HttpRequest) -> bool:
    expected: str = getattr(settings, "SELFTEST_TOKEN", "")
    if not expected:
        return True
    given = request.GET.get("token") or request.headers.get("X-Selftest-Token") or ""
    return hmac.compare_digest(given.encode(), expected.encode())


@require_GET
@never_cache
def selftest_golden(request: HttpRequest) -> HttpResponse:
    """Run the reference example and compare it with the reference pack, to the pound."""
    if not _token_ok(request):
        return _json({"status": "forbidden", "detail": "A valid self-test token is required."}, 403)
    try:
        result = estimate(reference_inputs(), rates_as_of=REFERENCE_RATES_AS_OF)
    except Exception as exc:
        logger.exception("golden self-test could not run")
        return _json({"status": "error", "detail": type(exc).__name__}, 500)
    checks = []
    for name, year, code, expected in GOLDEN_EXPECTATIONS:
        actual = result.totals.total_employer_cost if year is None else result.year(year).line(code)
        checks.append(
            {
                "check": name,
                "expected": str(expected),
                "actual": str(actual),
                "ok": actual == expected,
            }
        )
    passed = all(check["ok"] for check in checks)
    if not passed:
        logger.error("golden self-test mismatch: %s", [c for c in checks if not c["ok"]])
    return _json(
        {
            "status": "pass" if passed else "fail",
            "rates_as_of": result.rates_as_of.isoformat(),
            "inputs_hash": result.inputs_hash,
            "rate_set_fingerprint": result.rate_set_fingerprint,
            "checks": checks,
        },
        200 if passed else 500,
    )
