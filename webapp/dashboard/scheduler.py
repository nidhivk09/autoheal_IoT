"""
Background jobs, run via APScheduler (the build plan's default choice over
Celery+Redis for a solo research project).

Kinds of job that live here:
  1. A recurring sweep that marks devices "offline" once their telemetry
     goes stale - the AKS ingestion service only ever sets status='online'
     on receipt of telemetry, so something has to flip it back.
  2. One-off "batch trial" jobs, scheduled on demand from the dashboard's
     "Run batch trial" button, so that button can return immediately instead
     of blocking an HTTP request for the minutes a trial series takes.
  3. One-off jobs scheduled by other apps: firmware rollouts
     (devices/rollout.py) and manual incident resolution
     (incidents/services.py:resolve_incident_now).
"""
import logging
import time

from apscheduler.schedulers.background import BackgroundScheduler

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None

STALENESS_SWEEP_INTERVAL_SECONDS = 10

# Manual-mode batch trials simulate a human response with a fixed delay
# rather than waiting for a real person to click "Resolve manually" N times.
# Approval-mode trials use the same delay before approving a pending step.
# Documented in docs/research_notes.md as a study design choice.
DEFAULT_MANUAL_RESPONSE_DELAY_SECONDS = 45
DEFAULT_TRIAL_TIMEOUT_SECONDS = 300
DEFAULT_TRIAL_GAP_SECONDS = 15
BASELINE_TIMEOUT_SECONDS = 180


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


def _wait_for_healthy_baseline(device) -> bool:
    """Each trial starts from a healthy device with no open incident, so one
    trial's recovery can't bleed into the next one's measurements."""
    from controller.fault_injector import get_health
    from incidents.services import active_incident_for

    deadline = time.monotonic() + BASELINE_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if active_incident_for(device) is None:
            result = get_health(device.device_id)
            if result.ok and result.payload.get("healthy"):
                return True
        time.sleep(5)
    return False


def _append_note(incident, note: str) -> None:
    incident.notes = (incident.notes + " " if incident.notes else "") + note
    incident.save(update_fields=["notes"])


def run_batch_trial(
    device_id: str,
    fault_type: str,
    remediation_mode: str,
    num_trials: int,
    edge_healing: bool | None = None,
    timeout_seconds: int = DEFAULT_TRIAL_TIMEOUT_SECONDS,
    gap_seconds: int = DEFAULT_TRIAL_GAP_SECONDS,
    manual_response_delay_seconds: int = DEFAULT_MANUAL_RESPONSE_DELAY_SECONDS,
) -> None:
    """Runs `num_trials` repeated inject -> wait-for-resolution -> repeat
    cycles. Meant to be scheduled as a one-off APScheduler job (see
    dashboard/views.py:start_batch_trial), not called directly from a
    request/response cycle - it blocks for as long as the whole series takes.
    """
    from controller.fault_injector import clear_fault
    from devices.models import Device
    from devices.services import push_desired_config, wait_for_running_config
    from incidents.models import RemediationMode
    from incidents.services import inject_and_create_incident, request_approved_action, resolve_incident_now

    device = Device.objects.get(device_id=device_id)
    logger.info(
        "starting batch trial: device=%s fault_type=%s mode=%s edge_healing=%s n=%d",
        device_id, fault_type, remediation_mode, edge_healing, num_trials,
    )

    if edge_healing is not None and device.desired_edge_healing != edge_healing:
        device.desired_edge_healing = edge_healing
        device.save(update_fields=["desired_edge_healing"])
        push_desired_config(device)
        if not wait_for_running_config(device):
            logger.error("device %s never applied edge_healing=%s; aborting batch", device_id, edge_healing)
            return

    for trial_index in range(1, num_trials + 1):
        if not _wait_for_healthy_baseline(device):
            logger.error("batch trial %d/%d: %s not healthy, aborting batch", trial_index, num_trials, device_id)
            return

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
            approved = set()
            while time.monotonic() < deadline:
                incident.refresh_from_db()
                if incident.is_resolved:
                    break
                if incident.pending_action and incident.pending_action not in approved:
                    # Scripted human approval, same delay as manual mode.
                    time.sleep(manual_response_delay_seconds)
                    incident.refresh_from_db()
                    if incident.pending_action and not incident.is_resolved:
                        approved.add(incident.pending_action)
                        request_approved_action(incident)
                    continue
                time.sleep(2)
            else:
                _append_note(incident, f"[batch trial timed out after {timeout_seconds}s]")
                logger.warning("batch trial %d/%d: incident %s timed out", trial_index, num_trials, incident.id)
                clear_fault(device.device_id, fault_type)

        if trial_index < num_trials:
            time.sleep(gap_seconds)

    logger.info("batch trial complete: device=%s fault_type=%s mode=%s n=%d",
                device_id, fault_type, remediation_mode, num_trials)
