import logging

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render

from devices.models import Device

from .models import FaultType, Incident, RemediationMode
from .services import inject_and_create_incident, resolve_incident_now

logger = logging.getLogger(__name__)


def injection_panel(request):
    """The fault-injection control panel (build plan Section 5, Phase 6.4)."""
    context = {
        "devices": Device.objects.all(),
        "fault_types": FaultType.choices,
        "remediation_modes": RemediationMode.choices,
        "recent_incidents": Incident.objects.all()[:10],
    }
    return render(request, "incidents/injection_panel.html", context)


def inject(request):
    if request.method != "POST":
        return redirect("incidents:panel")

    device = get_object_or_404(Device, device_id=request.POST["device_id"])
    fault_type = request.POST["fault_type"]
    remediation_mode = request.POST["remediation_mode"]

    try:
        incident = inject_and_create_incident(device, fault_type, remediation_mode)
    except Exception as exc:  # noqa: BLE001 - surface any SSH/config error to the user
        logger.exception("fault injection failed")
        messages.error(request, f"Could not inject fault: {exc}")
        return redirect("incidents:panel")

    if incident.notes:
        messages.warning(request, incident.notes)

    return redirect("incidents:detail", incident_id=incident.id)


def detail(request, incident_id: int):
    incident = get_object_or_404(Incident, id=incident_id)
    return render(request, "incidents/detail.html", {"incident": incident})


def detail_status_partial(request, incident_id: int):
    """Polled via HTMX from the incident detail page while a trial is in flight."""
    incident = get_object_or_404(Incident, id=incident_id)
    return render(request, "incidents/_status.html", {"incident": incident})


def resolve_manually(request, incident_id: int):
    """Manual-mode resolution: a human clicks this after observing/fixing the
    fault themselves. Reverts the fault on the device and stamps
    remediated_at / verified_healthy_at at click time, per the build plan."""
    if request.method != "POST":
        return redirect("incidents:detail", incident_id=incident_id)

    incident = get_object_or_404(Incident, id=incident_id)
    if incident.remediation_mode != RemediationMode.MANUAL:
        messages.error(request, "This incident is not in manual mode.")
        return redirect("incidents:detail", incident_id=incident_id)

    try:
        resolve_incident_now(incident)
    except Exception as exc:  # noqa: BLE001
        logger.exception("manual fault clear failed")
        messages.error(request, f"Could not clear fault on device: {exc}")
        return redirect("incidents:detail", incident_id=incident_id)

    messages.success(request, "Incident marked resolved.")
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

    context = {
        "incidents": incidents,
        "fault_types": FaultType.choices,
        "remediation_modes": RemediationMode.choices,
        "selected_fault_type": fault_type or "",
        "selected_remediation_mode": remediation_mode or "",
    }
    return render(request, "incidents/log.html", context)
