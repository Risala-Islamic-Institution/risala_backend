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
    """Migrate and collect static shortly after boot.

    AletCloud auto-detects the stack and launches gunicorn with its own
    start command, which can skip the Procfile release phase entirely (the
    deploy log prints ">>> Migration skipped"). Without this the database
    schema and the collected static files may never exist. Both commands
    are idempotent; a Postgres advisory lock keeps parallel workers from
    running them concurrently. Runs in a daemon thread: doing this work
    during wsgi import blocks every worker for the duration of
    collectstatic, the platform health check gets no answer, and the app
    is killed one second after boot. Set DJANGO_STARTUP_TASKS=off to
    disable once the platform runs migrations itself.
    """
    import logging

    from django.conf import settings
    from django.core.management import call_command
    from django.db import connection, connections

    log = logging.getLogger(__name__)
    try:
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_try_advisory_lock(721438)")
                acquired = cursor.fetchone()[0]
            if not acquired:  # another worker is already on it
                return
        except Exception:
            log.exception("Startup advisory lock failed; skipping tasks")
            return
        try:
            call_command("migrate", interactive=False)
            log.info("Startup migrate finished")
        except Exception:
            log.exception("Startup migrate failed")
        try:
            if not Path(settings.STATIC_ROOT).exists():
                call_command("collectstatic", interactive=False, verbosity=0)
                log.info("Startup collectstatic finished")
        except Exception:
            log.exception("Startup collectstatic failed")
    finally:
        # The lock's connection belongs to this thread; close it so the
        # lock is released and the connection isn't left dangling.
        connections.close_all()


_startup_tasks_off = os.environ.get("DJANGO_STARTUP_TASKS", "").lower() in {
    "0",
    "false",
    "off",
}
_is_production = os.environ["DJANGO_SETTINGS_MODULE"].endswith("production")
if _is_production and not _startup_tasks_off:
    import threading

    import django

    django.setup()
    threading.Thread(
        target=_run_startup_tasks,
        name="startup-tasks",
        daemon=True,
    ).start()

# This application object is used by any WSGI server configured to use this
# file. This includes Django's development server, if the WSGI_APPLICATION
# setting points here.
application = get_wsgi_application()
