"""ASGI entry point. Defaults to production settings; set DJANGO_SETTINGS_MODULE to override."""

from __future__ import annotations

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "teq_web.settings.prod")

application = get_asgi_application()
