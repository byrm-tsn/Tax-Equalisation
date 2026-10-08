"""Production: debug off, a real secret key required, secure cookies and HSTS by default."""

from __future__ import annotations

import os

from django.core.exceptions import ImproperlyConfigured

from teq_web.settings.base import *  # noqa: F403
from teq_web.settings.base import SECRET_KEY, env_bool

if not SECRET_KEY or SECRET_KEY.startswith(("dev-only", "test-only")):
    raise ImproperlyConfigured("Set DJANGO_SECRET_KEY to a long random value in production.")

DEBUG = False
CSRF_COOKIE_SECURE = env_bool("DJANGO_SECURE_COOKIES", True)
SECURE_SSL_REDIRECT = env_bool("DJANGO_SECURE_SSL_REDIRECT", True)
SECURE_HSTS_SECONDS = int(os.environ.get("DJANGO_SECURE_HSTS_SECONDS", "31536000"))
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
