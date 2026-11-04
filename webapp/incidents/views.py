import logging

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render

from devices.models import Device

from .models import INJECTABLE_FAULT_TYPES, FaultType, Incident, RemediationMode
from .services import inject_and_create_incident, request_approved_action, resolve_incident_now

logger = logging.getLogger(__name__)


def injection_panel(request):
    """The fault-injection control panel (build plan Section 5, Phase 6.4)."""
    context = {
        "devices": Device.objects.all(),
        "fault_types": INJECTABLE_FAULT_TYPES,
        "remediation_modes": RemediationMode.choices,
        "recent_incidents": Incident.objects.select_related("device")[:10],
    }
    return render(request, "incidents/injection_panel.html", context)


def inject(request):
    if request.method != "POST":
        return redirect("incidents:panel")

    device = get_object_or_404(Device, device_id=request.POST["device_id"])
    fault_type = request.POST["fault_type"]
    remediation_mode = request.POST["remediation_mode"]
    params = {}
    if fault_type == FaultType.NETWORK_LOSS and request.POST.get("duration_s"):
        params["duration_s"] = int(request.POST["duration_s"])

    try:
        incident = inject_and_create_incident(device, fault_type, remediation_mode, params)
    except Exception as exc:  # noqa: BLE001 - surface any IoT Hub/config error to the user
        logger.exception("fault injection failed")
        messages.error(request, f"Could not inject fault: {exc}")
        return redirect("incidents:panel")

    if incident.notes:
        messages.warning(request, incident.notes)

    return redirect("incidents:detail", incident_id=incident.id)


def _can_resolve_manually(incident: Incident) -> bool:
    if incident.is_resolved:
        return False
    return (
        incident.remediation_mode == RemediationMode.MANUAL
        or incident.escalated_at is not None
        or bool(incident.pending_action)
    )


def _detail_context(incident: Incident) -> dict:
    return {
        "incident": incident,
        "actions": incident.actions.all(),
        "can_resolve": _can_resolve_manually(incident),
    }


def detail(request, incident_id: int):
    incident = get_object_or_404(Incident, id=incident_id)
    return render(request, "incidents/detail.html", _detail_context(incident))


def detail_status_partial(request, incident_id: int):
    """Polled via HTMX from the incident detail page while a trial is in flight."""
    incident = get_object_or_404(Incident, id=incident_id)
    return render(request, "incidents/_status.html", _detail_context(incident))


def resolve_manually(request, incident_id: int):
    """Human resolution: allowed in manual mode, or once automation has
    escalated / is waiting for approval. Clears the fault on the device and
    stamps the incident once the device reports healthy - that wait runs as
    a background job so the request returns immediately."""
    if request.method != "POST":
        return redirect("incidents:detail", incident_id=incident_id)

    incident = get_object_or_404(Incident, id=incident_id)
    if not _can_resolve_manually(incident):
        messages.error(request, "This incident can't be resolved manually right now.")
        return redirect("incidents:detail", incident_id=incident_id)

    from dashboard import scheduler

    try:
        scheduler.get_scheduler().add_job(resolve_incident_now, args=[incident])
    except Exception as exc:  # noqa: BLE001
        logger.exception("could not schedule manual resolution")
        messages.error(request, f"Could not start manual resolution: {exc}")
        return redirect("incidents:detail", incident_id=incident_id)

    messages.success(request, "Clearing the fault; the incident resolves once the device reports healthy.")
    return redirect("incidents:detail", incident_id=incident_id)


def approve_action(request, incident_id: int):
    """Approval mode: a human OKs the disruptive step the ladder paused on."""
    if request.method != "POST":
        return redirect("incidents:detail", incident_id=incident_id)

    incident = get_object_or_404(Incident, id=incident_id)
    try:
        request_approved_action(incident)
    except Exception as exc:  # noqa: BLE001
        logger.exception("approval dispatch failed")
        messages.error(request, f"Could not dispatch approval: {exc}")
        return redirect("incidents:detail", incident_id=incident_id)

    messages.success(request, f"Approved {incident.pending_step}; remediation resumes.")
    return redirect("incidents:detail", incident_id=incident_id)


def log(request):
    """Filterable incident log table (build plan Phase 6.4)."""
    incidents = Incident.objects.select_related("device").all()

    fault_type = request.GET.get("fault_type")
    if fault_type:
        incidents = incidents.filter(fault_type=fault_type)

    remediation_mode = request.GET.get("remediation_mode")
    if remediation_mode:
        incidents = incidents.filter(remediation_mode=remediation_mode)

    edge_healing = request.GET.get("edge_healing")
    if edge_healing in ("on", "off"):
        incidents = incidents.filter(edge_healing=edge_healing == "on")

    device_id = request.GET.get("device_id")
    if device_id:
        incidents = incidents.filter(device__device_id=device_id)

    context = {
        "incidents": incidents,
        "devices": Device.objects.all(),
        "fault_types": FaultType.choices,
        "remediation_modes": RemediationMode.choices,
        "selected_fault_type": fault_type or "",
        "selected_remediation_mode": remediation_mode or "",
        "selected_edge_healing": edge_healing or "",
        "selected_device_id": device_id or "",
    }
    return render(request, "incidents/log.html", context)
