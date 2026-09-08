"""
Background jobs, run via APScheduler (the build plan's default choice over
Celery+Redis for a solo research project).

Two kinds of job live here:
  1. A recurring sweep that marks devices "offline" once their telemetry
     goes stale - the AKS ingestion service only ever sets status='online'
     on receipt of telemetry, so something has to flip it back.
  2. One-off "batch trial" jobs, scheduled on demand from the dashboard's
     "Run batch trial" button, so that button can return immediately instead
     of blocking an HTTP request for the minutes a trial series takes.
"""
import logging
import time

from apscheduler.schedulers.background import BackgroundScheduler
from django.utils import timezone

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None

STALENESS_SWEEP_INTERVAL_SECONDS = 10

# Manual-mode batch trials simulate a human response with a fixed delay
# rather than waiting for a real person to click "Resolve manually" N times.
# Documented in docs/research_notes.md as a study design choice.
DEFAULT_MANUAL_RESPONSE_DELAY_SECONDS = 45
DEFAULT_TRIAL_TIMEOUT_SECONDS = 180
DEFAULT_TRIAL_GAP_SECONDS = 15


def start() -> BackgroundScheduler:
    global _scheduler
    if _scheduler is not None:
        return _scheduler

    _scheduler = BackgroundScheduler(daemon=True)
    _scheduler.add_job(
        sweep_stale_devices,
        "interval",
        seconds=STALENESS_SWEEP_INTERVAL_SECONDS,
        id="sweep_stale_devices",
        replace_existing=True,
    )
    _scheduler.start()
    logger.info("background scheduler started")
    return _scheduler


def get_scheduler() -> BackgroundScheduler:
    if _scheduler is None:
        raise RuntimeError("scheduler has not been started - is SCHEDULER_AUTOSTART set?")
    return _scheduler


def sweep_stale_devices() -> None:
    from devices.models import Device

    for device in Device.objects.exclude(status=Device.Status.OFFLINE):
        if device.is_stale:
            device.status = Device.Status.OFFLINE
            device.save(update_fields=["status"])
            logger.info("marked %s offline (stale telemetry)", device.device_id)


def run_batch_trial(
    device_id: str,
    fault_type: str,
    remediation_mode: str,
    num_trials: int,
    timeout_seconds: int = DEFAULT_TRIAL_TIMEOUT_SECONDS,
    gap_seconds: int = DEFAULT_TRIAL_GAP_SECONDS,
    manual_response_delay_seconds: int = DEFAULT_MANUAL_RESPONSE_DELAY_SECONDS,
) -> None:
    """Runs `num_trials` repeated inject -> wait-for-resolution -> repeat
    cycles. Meant to be scheduled as a one-off APScheduler job (see
    dashboard/views.py:start_batch_trial), not called directly from a
    request/response cycle - it blocks for as long as the whole series takes.
    """
    from devices.models import Device
    from incidents.models import Incident, RemediationMode
    from incidents.services import inject_and_create_incident, resolve_incident_now

    device = Device.objects.get(device_id=device_id)
    logger.info(
        "starting batch trial: device=%s fault_type=%s mode=%s n=%d",
        device_id, fault_type, remediation_mode, num_trials,
    )

    for trial_index in range(1, num_trials + 1):
        incident = inject_and_create_incident(device, fault_type, remediation_mode)
        logger.info("batch trial %d/%d: incident %s injected", trial_index, num_trials, incident.id)

        if remediation_mode == RemediationMode.MANUAL:
            # Scripted stand-in for human response time - see module docstring.
            time.sleep(manual_response_delay_seconds)
            incident.refresh_from_db()
            if not incident.is_resolved:
                resolve_incident_now(incident)
        else:
            deadline = time.monotonic() + timeout_seconds
            while time.monotonic() < deadline:
                incident.refresh_from_db()
                if incident.is_resolved:
                    break
                time.sleep(2)
            else:
                incident.notes = (incident.notes + " " if incident.notes else "") + (
                    f"[batch trial timed out after {timeout_seconds}s waiting for automated remediation]"
                )
                incident.save(update_fields=["notes"])
                logger.warning("batch trial %d/%d: incident %s timed out", trial_index, num_trials, incident.id)

        if trial_index < num_trials:
            time.sleep(gap_seconds)

    logger.info("batch trial complete: device=%s fault_type=%s mode=%s n=%d", device_id, fault_type, remediation_mode, num_trials)
