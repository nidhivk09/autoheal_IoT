import os

from django.apps import AppConfig
from django.conf import settings


class DashboardConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "dashboard"

    def ready(self):
        # Guard against starting the scheduler twice under the dev server's
        # autoreloader (which imports every app twice - once in the reloader
        # parent process, once in the real child) and against import-time
        # side effects during `manage.py migrate` / other management commands.
        if not settings.SCHEDULER_AUTOSTART:
            return
        if os.environ.get("RUN_MAIN") != "true" and settings.DEBUG:
            return

        from . import scheduler

        scheduler.start()
