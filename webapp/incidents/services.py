"""
Shared incident-lifecycle operations, used by the interactive injection
panel (incidents/views.py), the batch trial runner (dashboard/scheduler.py)
and the internal API (incidents/internal_api.py), so there is exactly one
place that talks to the fault injection controller and stamps Incident
timestamps.
"""
import logging
import time
from datetime import datetime, timedelta, timezone as dt_timezone

import requests
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from devices.models import Device

from .models import EXPECTED_SIGNALS, FaultType, Incident, RemediationAction, RemediationMode, Tier

logger = logging.getLogger(__name__)

MANUAL_VERIFY_TIMEOUT_SECONDS = 180
# Device clocks are NTP-synced but not perfectly: allow device-reported
# times to land this far before injected_at before treating them as stale.
CLOCK_SKEW_TOLERANCE = timedelta(seconds=3)


def _epoch_to_dt(value) -> datetime | None:
    if not value:
        return None
    return datetime.fromtimestamp(float(value), tz=dt_timezone.utc)


def inject_and_create_incident(device: Device, fault_type: str, remediation_mode: str,
                               params: dict | None = None) -> Incident:
    from controller.fault_injector import inject_fault

    params = dict(params or {})
    if fault_type == FaultType.BAD_FIRMWARE and "url" not in params:
        from controller.firmware_store import latest_release, sas_url

        release = latest_release("bad")
        if release is None:
            raise RuntimeError("no bad firmware build in blob storage - run the build-firmware workflow first")
        params.update(url=sas_url(release.blob_name), version=release.version)

    prior_trials = Incident.objects.filter(
        device=device, fault_type=fault_type, remediation_mode=remediation_mode,
        edge_healing=device.desired_edge_healing,
    ).count()

    success, output = inject_fault(device.device_id, fault_type, params)

    return Incident.objects.create(
        device=device,
        fault_type=fault_type,
        remediation_mode=remediation_mode,
        edge_healing=device.desired_edge_healing,
        injected_at=timezone.now(),
        trial_number=prior_trials + 1,
        notes="" if success else f"injection command reported failure: {output}",
    )


def active_incident_for(device: Device) -> Incident | None:
    return (
        Incident.objects.filter(device=device, verified_healthy_at__isnull=True)
        .order_by("-injected_at")
        .first()
    )


def resolve_incident_now(incident: Incident) -> None:
    """Human resolution: reverts the fault on the device, waits until the
    device reports healthy, then stamps remediated_at / verified_healthy_at.
    Used for:
      - a human clicking "Resolve manually" in the UI (run as a background
        job, since the wait can take minutes)
      - the batch trial runner's scripted stand-in for human response time
        in manual-mode trials (see dashboard/scheduler.py)
    Blocks for up to MANUAL_VERIFY_TIMEOUT_SECONDS.
    """
    from controller.fault_injector import clear_fault, get_health

    if incident.fault_type != FaultType.ORGANIC:
        ok, output = clear_fault(incident.device.device_id, incident.fault_type)
        if not ok:
            logger.info("manual clear of incident %s didn't reach the device: %s", incident.id, output)
    remediated_at = timezone.now()
    RemediationAction.objects.create(
        incident=incident, tier=Tier.HUMAN, action="manual_resolve", started_at=remediated_at,
        finished_at=remediated_at,
    )

    deadline = time.monotonic() + MANUAL_VERIFY_TIMEOUT_SECONDS
    healthy = False
    while time.monotonic() < deadline:
        incident.refresh_from_db()
        if incident.is_resolved:
            return  # another tier (or the device itself) got there first
        result = get_health(incident.device.device_id)
        if result.ok and result.payload.get("healthy"):
            healthy = True
            break
        time.sleep(3)

    with transaction.atomic():
        incident = Incident.objects.select_for_update().get(id=incident.id)
        if incident.is_resolved:
            return
        now = timezone.now()
        incident.detected_at = incident.detected_at or remediated_at
        incident.detected_by = incident.detected_by or Tier.HUMAN
        incident.remediation_started_at = incident.remediation_started_at or remediated_at
        incident.remediated_at = remediated_at
        incident.pending_action = ""
        if healthy:
            incident.verified_healthy_at = now
            incident.resolved_by = Tier.HUMAN
        else:
            incident.notes = (incident.notes + " " if incident.notes else "") + (
                f"[manual resolve: device not healthy after {MANUAL_VERIFY_TIMEOUT_SECONDS}s]"
            )
        incident.save()


def apply_recovery_event(device: Device, event: dict) -> Incident | None:
    """Applies a firmware "recovered" event (forwarded by the ingestion
    service) to the device's active incident. The first tier to verify
    recovery wins; later claims are ignored.

    healer="edge" means the on-device healer fixed it. healer="none" means
    the device noticed it had recovered without help (baseline firmware
    reconnecting after an outage) and is credited as Tier.SELF.
    """
    signal = event.get("signal", "")
    healer = event.get("healer", "none")
    tier = Tier.EDGE if healer == "edge" else Tier.SELF
    detected = _epoch_to_dt(event.get("detected_at"))
    remediated = _epoch_to_dt(event.get("remediated_at"))
    verified = _epoch_to_dt(event.get("verified_at")) or timezone.now()

    with transaction.atomic():
        incident = (
            Incident.objects.select_for_update()
            .filter(device=device, verified_healthy_at__isnull=True)
            .order_by("-injected_at")
            .first()
        )

        if incident is None:
            if tier != Tier.EDGE:
                return None
            # A real fault the edge fixed on its own - record it so organic
            # heals show up in the dataset too.
            start = detected or verified
            incident = Incident.objects.create(
                device=device, fault_type=FaultType.ORGANIC,
                remediation_mode=RemediationMode.AUTOMATED, edge_healing=True,
                injected_at=start, detected_at=start, detected_by=Tier.EDGE,
                detection_signal=signal, remediation_started_at=remediated or start,
                remediated_at=remediated or verified, verified_healthy_at=verified,
                resolved_by=Tier.EDGE,
            )
            _record_event_action(incident, tier, event, remediated or verified, verified)
            return incident

        if verified < incident.injected_at - CLOCK_SKEW_TOLERANCE:
            return None  # recovery from something that happened before this trial

        _record_event_action(incident, tier, event, remediated or verified, verified)
        expected = EXPECTED_SIGNALS.get(incident.fault_type)
        if expected is not None and signal not in expected:
            return incident  # unrelated heal during the trial; logged, not credited

        def clamp(value: datetime | None) -> datetime | None:
            return max(value, incident.injected_at) if value else None

        detected, remediated, verified = clamp(detected), clamp(remediated), clamp(verified)
        if tier == Tier.EDGE and detected and (incident.detected_at is None or detected < incident.detected_at):
            incident.detected_at = detected
            incident.detected_by = Tier.EDGE
            incident.detection_signal = signal
        if tier == Tier.EDGE:
            incident.remediation_started_at = min(filter(None, [incident.remediation_started_at, remediated]),
                                                  default=None)
        incident.remediated_at = remediated or verified
        incident.verified_healthy_at = verified
        incident.resolved_by = tier
        incident.pending_action = ""
        if event.get("dropped_during") is not None:
            incident.messages_dropped = int(event["dropped_during"])
        incident.save()
        return incident


def _record_event_action(incident: Incident, tier: str, event: dict, started: datetime,
                         finished: datetime) -> None:
    RemediationAction.objects.create(
        incident=incident, tier=tier, action=event.get("action", "unknown"),
        started_at=started, finished_at=finished, success=True,
        detail=f"signal={event.get('signal')} fw={event.get('fw')} dropped={event.get('dropped_during')}",
    )


def request_approved_action(incident: Incident) -> None:
    """Approval mode: tells the remediation Function to run the pending
    disruptive step and continue the ladder from there."""
    if not incident.pending_action:
        raise ValueError("incident has no pending action")
    if not settings.REMEDIATION_DISPATCH_URL:
        raise RuntimeError("REMEDIATION_DISPATCH_URL is not set (see .env.example)")
    resp = requests.post(
        settings.REMEDIATION_DISPATCH_URL,
        json={"kind": "approval", "incident_id": incident.id, "action": incident.pending_action},
        timeout=15,
    )
    resp.raise_for_status()
    RemediationAction.objects.create(
        incident=incident, tier=Tier.HUMAN, action=f"approve:{incident.pending_step}",
        started_at=timezone.now(), finished_at=timezone.now(), success=True,
    )
