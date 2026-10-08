"""Middleware for every request: body-size limit, response headers and API method errors.

* :class:`RequestSizeLimitMiddleware` refuses a body over ``DATA_UPLOAD_MAX_MEMORY_SIZE``
  with 413 before anything reads it (problem details under ``/api/``, a plain page
  elsewhere), and maps a ``RequestDataTooBig`` raised later the same way.
* :class:`ResponseHeadersMiddleware` adds ``Cache-Control: private, no-store`` (results
  carry salaries in the page and the URL, so no shared cache may keep them) unless a view
  set its own, and a ``Permissions-Policy`` denying powerful browser features.
* :class:`ApiMethodNotAllowedMiddleware` turns the API's bare 405 into problem details.
"""

from __future__ import annotations

from collections.abc import Callable

from django.conf import settings
from django.core.exceptions import RequestDataTooBig
from django.http import HttpRequest, HttpResponse

from teq_web.api.problems import PROBLEM_CONTENT_TYPE, problem

__all__ = [
    "ApiMethodNotAllowedMiddleware",
    "RequestSizeLimitMiddleware",
    "ResponseHeadersMiddleware",
]

GetResponse = Callable[[HttpRequest], HttpResponse]


def _content_length(request: HttpRequest) -> int:
    try:
        return int(request.META.get("CONTENT_LENGTH") or 0)
    except (TypeError, ValueError):
        return 0


def _too_large(request: HttpRequest) -> HttpResponse:
    from teq_web.web.views import request_too_large  # the views import this package's peers

    return request_too_large(request)


class RequestSizeLimitMiddleware:
    """413 for a declared body over ``DATA_UPLOAD_MAX_MEMORY_SIZE``, before it is read."""

    def __init__(self, get_response: GetResponse) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        limit = settings.DATA_UPLOAD_MAX_MEMORY_SIZE
        if limit is not None and _content_length(request) > limit:
            return _too_large(request)
        return self.get_response(request)

    def process_exception(self, request: HttpRequest, exception: Exception) -> HttpResponse | None:
        """A view that read an oversized body (Django raises ``RequestDataTooBig``)."""
        if isinstance(exception, RequestDataTooBig):
            return _too_large(request)
        return None


class ResponseHeadersMiddleware:
    """``Cache-Control`` and ``Permissions-Policy`` on every response."""

    def __init__(self, get_response: GetResponse) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        response = self.get_response(request)
        if not response.has_header("Cache-Control"):
            response["Cache-Control"] = settings.TEQ_CACHE_CONTROL
        if not response.has_header("Permissions-Policy"):
            response["Permissions-Policy"] = settings.TEQ_PERMISSIONS_POLICY
        return response


class ApiMethodNotAllowedMiddleware:
    """A 405 under ``/api/`` as problem details, keeping the ``Allow`` header."""

    def __init__(self, get_response: GetResponse) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        response = self.get_response(request)
        if (
            response.status_code == 405
            and request.path.startswith("/api/")
            and response.get("Content-Type") != PROBLEM_CONTENT_TYPE
        ):
            allowed = response.get("Allow", "")
            converted = problem(
                request,
                status=405,
                code="method-not-allowed",
                detail=f"{request.method} is not allowed here."
                + (f" Allowed: {allowed}." if allowed else ""),
            )
            if allowed:
                converted["Allow"] = allowed
            return converted
        return response
