"""
Shared incident-lifecycle operations, used by both the interactive injection
panel (incidents/views.py) and the batch trial runner (dashboard/scheduler.py)
so there is exactly one place that talks to the fault injection controller
and stamps Incident timestamps for "injected" and "manually resolved" events.
"""
from django.utils import timezone

from devices.models import Device

from .models import Incident, RemediationMode


def inject_and_create_incident(device: Device, fault_type: str, remediation_mode: str) -> Incident:
    from controller.fault_injector import inject_fault

    prior_trials = Incident.objects.filter(
        device=device, fault_type=fault_type, remediation_mode=remediation_mode
    ).count()

    success, output = inject_fault(fault_type)

    return Incident.objects.create(
        device=device,
        fault_type=fault_type,
        remediation_mode=remediation_mode,
        injected_at=timezone.now(),
        trial_number=prior_trials + 1,
        notes="" if success else f"injection command reported failure: {output}",
    )


def resolve_incident_now(incident: Incident) -> None:
    """Reverts the fault on the device and stamps remediated_at /
    verified_healthy_at at the moment this is called. Used for:
      - a human clicking "Resolve manually" in the UI
      - the batch trial runner's scripted stand-in for human response time
        in manual-mode trials (see dashboard/scheduler.py)
    """
    from controller.fault_injector import clear_fault

    clear_fault(incident.fault_type)

    now = timezone.now()
    if incident.detected_at is None:
        incident.detected_at = now
    incident.remediation_started_at = incident.remediation_started_at or now
    incident.remediated_at = now
    incident.verified_healthy_at = now
    incident.save()
