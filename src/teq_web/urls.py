"""URL map. The paths are the README's contract."""

from __future__ import annotations

from django.urls import path

from teq_web.api.endpoints import api
from teq_web.ops import views as ops
from teq_web.web import views as web

urlpatterns = [
    path("", web.scenario_form, name="form"),
    path("example", web.example, name="example"),
    path("estimate", web.estimate_page, name="estimate"),
    path("compare", web.compare, name="compare"),
    path("healthz", ops.healthz, name="healthz"),
    path("readyz", ops.readyz, name="readyz"),
    path("selftest/golden", ops.selftest_golden, name="selftest-golden"),
    path("api/v1/", api.urls),
]

handler404 = "teq_web.web.views.page_not_found"
handler500 = "teq_web.web.views.server_error"
