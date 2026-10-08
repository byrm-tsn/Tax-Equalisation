"""Test settings: deterministic, independent of the caller's environment."""

from __future__ import annotations

from teq_web.settings.base import *  # noqa: F403

DEBUG = False
SECRET_KEY = "test-only-insecure-key"
ALLOWED_HOSTS = ["testserver", "localhost", "127.0.0.1"]
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
SELFTEST_TOKEN = ""
SECURE_SSL_REDIRECT = False
SECURE_HSTS_SECONDS = 0
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"null": {"class": "logging.NullHandler"}},
    "root": {"handlers": ["null"], "level": "WARNING"},
}
