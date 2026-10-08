"""Local development: debug on, a development-only secret key unless one is supplied."""

from __future__ import annotations

import os

from teq_web.settings.base import *  # noqa: F403
from teq_web.settings.base import env_bool

DEBUG = env_bool("DJANGO_DEBUG", True)
# Development only. Production (teq_web.settings.prod) refuses to start without a real key.
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY") or "dev-only-insecure-key-do-not-use-in-production"
