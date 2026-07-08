"""
WSGI config for Risala_Backend project.

This module contains the WSGI application used by Django's development server
and any production WSGI deployments. It should expose a module-level variable
named ``application``. Django's ``runserver`` and ``runfcgi`` commands discover
this application via the ``WSGI_APPLICATION`` setting.

Usually you will have the standard Django WSGI application here, but it also
might make sense to replace the whole Django WSGI application with a custom one
that later delegates to the Django one. For example, you could introduce WSGI
middleware here, or combine a Django application with an application of another
framework.

"""

import os
import sys
from pathlib import Path

from django.core.wsgi import get_wsgi_application

# This allows easy placement of apps within the interior
# risala_backend directory.
BASE_DIR = Path(__file__).resolve(strict=True).parent.parent
sys.path.append(str(BASE_DIR / "risala_backend"))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.production")


def _run_startup_tasks():
    """Migrate and collect static before serving requests.

    AletCloud auto-detects the stack and launches gunicorn with its own
    start command, which can skip the Procfile release phase entirely (the
    deploy log prints ">>> Migration skipped"). Without this the database
    schema and the whitenoise manifest may never exist and every request
    500s. Both commands are idempotent; a Postgres advisory lock keeps
    parallel workers from running them concurrently. Set
    DJANGO_STARTUP_TASKS=off to disable once the platform runs migrations
    itself.
    """
    import logging

    from django.conf import settings
    from django.core.management import call_command
    from django.db import connection

    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_try_advisory_lock(721438)")
            acquired = cursor.fetchone()[0]
        if not acquired:  # another worker is already on it
            return
        call_command("migrate", interactive=False)
        manifest = Path(settings.STATIC_ROOT) / "staticfiles.json"
        if not manifest.exists():
            call_command("collectstatic", interactive=False, verbosity=0)
    except Exception:
        logging.getLogger(__name__).exception(
            "Startup migrate/collectstatic failed; continuing boot",
        )


_startup_tasks_off = os.environ.get("DJANGO_STARTUP_TASKS", "").lower() in {"0", "false", "off"}
if os.environ["DJANGO_SETTINGS_MODULE"].endswith("production") and not _startup_tasks_off:
    import django

    django.setup()
    _run_startup_tasks()

# This application object is used by any WSGI server configured to use this
# file. This includes Django's development server, if the WSGI_APPLICATION
# setting points here.
application = get_wsgi_application()
