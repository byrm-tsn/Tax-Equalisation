"""Shared settings. Production-safe defaults; everything that varies comes from the environment.

No sessions, no authentication, no models: the slice is stateless. Results are carried in
the URL, so the database is configured for completeness (and for ``/readyz``) only.
"""

from __future__ import annotations

import os
from pathlib import Path


def env_bool(name: str, default: bool = False) -> bool:
    """Read a yes-or-no environment variable (``1``, ``true``, ``yes``, ``on``)."""
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_list(name: str, default: list[str]) -> list[str]:
    """Read a comma-separated environment variable."""
    value = os.environ.get(name)
    if value is None:
        return default
    return [part.strip() for part in value.split(",") if part.strip()]


PACKAGE_DIR = Path(__file__).resolve().parent.parent  # src/teq_web
REPO_DIR = PACKAGE_DIR.parent.parent

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "")
DEBUG = env_bool("DJANGO_DEBUG", False)
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", ["localhost", "127.0.0.1", "[::1]"])
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS", [])

INSTALLED_APPS = [
    "django.contrib.staticfiles",
    # django-ninja's own templates and static files, so the API docs work offline.
    "ninja",
    "teq_web.web",
    "teq_web.scenarios",
    "teq_web.narration",
    "teq_web.api",
    "teq_web.ops",
]

MIDDLEWARE = [
    # Outermost, so every response gets the headers, including Django's own error pages.
    "teq_web.middleware.ResponseHeadersMiddleware",
    "django.middleware.security.SecurityMiddleware",
    # Before CSRF and the views, so an oversized body is refused before anything reads it.
    "teq_web.middleware.RequestSizeLimitMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "teq_web.middleware.ApiMethodNotAllowedMiddleware",
]

ROOT_URLCONF = "teq_web.urls"
WSGI_APPLICATION = "teq_web.wsgi.application"
ASGI_APPLICATION = "teq_web.asgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
            ],
        },
    }
]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": os.environ.get("TEQ_SQLITE_PATH", str(REPO_DIR / "db.sqlite3")),
    }
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LANGUAGE_CODE = "en-gb"
TIME_ZONE = "Europe/London"
USE_I18N = False
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = os.environ.get("DJANGO_STATIC_ROOT", str(REPO_DIR / "staticfiles"))

# ---- security headers (no sessions or auth: only the CSRF cookie is ever set)
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"
CSRF_COOKIE_HTTPONLY = True
CSRF_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SECURE = env_bool("DJANGO_SECURE_COOKIES", False)
SECURE_SSL_REDIRECT = env_bool("DJANGO_SECURE_SSL_REDIRECT", False)
SECURE_HSTS_SECONDS = int(os.environ.get("DJANGO_SECURE_HSTS_SECONDS", "0"))
SECURE_HSTS_INCLUDE_SUBDOMAINS = SECURE_HSTS_SECONDS > 0
if env_bool("DJANGO_BEHIND_TLS_PROXY", False):
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# Content-Security-Policy for the HTML pages (applied by the web views; the pages use no
# JavaScript and no inline styles).
TEQ_CONTENT_SECURITY_POLICY = (
    "default-src 'self'; script-src 'none'; style-src 'self'; img-src 'self' data:; "
    "form-action 'self'; frame-ancestors 'none'; base-uri 'none'; object-src 'none'"
)

# Every response that sets no Cache-Control of its own: results carry salaries in the
# page and in the URL, so neither a shared cache nor the browser may keep them.
TEQ_CACHE_CONTROL = "private, no-store"
TEQ_PERMISSIONS_POLICY = "camera=(), microphone=(), geolocation=(), payment=()"

# A scenario is a few kilobytes; refuse request bodies over 256 KB (413).
DATA_UPLOAD_MAX_MEMORY_SIZE = 262144

# ---- operations
# When set, /selftest/golden requires it as ?token= or the X-Selftest-Token header.
SELFTEST_TOKEN = os.environ.get("SELFTEST_TOKEN", "")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"plain": {"format": "%(asctime)s %(levelname)s %(name)s %(message)s"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "plain"}},
    "root": {"handlers": ["console"], "level": os.environ.get("DJANGO_LOG_LEVEL", "INFO")},
}
