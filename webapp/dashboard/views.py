import csv
import statistics
from itertools import groupby

from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from devices.models import Device
from incidents.models import FaultType, Incident, RemediationMode


def _resolved_incidents():
    return Incident.objects.filter(verified_healthy_at__isnull=False).select_related("device")


def _stats_for(values: list[float]) -> dict:
    if not values:
        return {"n": 0, "mean": None, "median": None, "stdev": None, "min": None, "max": None}
    return {
        "n": len(values),
        "mean": round(statistics.mean(values), 2),
        "median": round(statistics.median(values), 2),
        "stdev": round(statistics.stdev(values), 2) if len(values) > 1 else 0.0,
        "min": round(min(values), 2),
        "max": round(max(values), 2),
    }


def summary_stats() -> list[dict]:
    """One row per (fault_type, remediation_mode) with MTTR/MTTD stats -
    the table described in build plan Section 5, Phase 6.5."""
    incidents = list(_resolved_incidents())
    incidents.sort(key=lambda i: (i.fault_type, i.remediation_mode))

    rows = []
    for (fault_type, mode), group in groupby(incidents, key=lambda i: (i.fault_type, i.remediation_mode)):
        group = list(group)
        mttr_values = [i.mttr_seconds for i in group if i.mttr_seconds is not None]
        mttd_values = [i.mttd_seconds for i in group if i.mttd_seconds is not None]
        rows.append({
            "fault_type": fault_type,
            "remediation_mode": mode,
            "mttr": _stats_for(mttr_values),
            "mttd": _stats_for(mttd_values),
        })
    return rows


def analytics(request):
    context = {
        "stats": summary_stats(),
        "fault_types": FaultType.choices,
        "remediation_modes": RemediationMode.choices,
        "devices": Device.objects.all(),
    }
    return render(request, "dashboard/analytics.html", context)


def chart_data(request):
    """JSON feed for the Chart.js MTTR comparison chart, polled/fetched from
    dashboard/analytics.html - one bar per (fault_type, mode) with mean and
    stdev so the frontend can draw error bars."""
    rows = summary_stats()
    labels = [f"{r['fault_type']} / {r['remediation_mode']}" for r in rows]
    means = [r["mttr"]["mean"] for r in rows]
    stdevs = [r["mttr"]["stdev"] for r in rows]
    return JsonResponse({"labels": labels, "means": means, "stdevs": stdevs, "rows": rows})


def start_batch_trial(request):
    if request.method != "POST":
        return redirect("dashboard:analytics")

    device = get_object_or_404(Device, device_id=request.POST["device_id"])
    fault_type = request.POST["fault_type"]
    remediation_mode = request.POST["remediation_mode"]
    num_trials = int(request.POST.get("num_trials", 10))

    from . import scheduler

    scheduler.get_scheduler().add_job(
        scheduler.run_batch_trial,
        kwargs={
            "device_id": device.device_id,
            "fault_type": fault_type,
            "remediation_mode": remediation_mode,
            "num_trials": num_trials,
        },
    )

    return redirect(reverse("dashboard:analytics") + "?batch_started=1")


def csv_export(request):
    """Raw incident data for pandas/R - build plan Phase 6.5 / Phase 8.3."""
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="incidents.csv"'

    writer = csv.writer(response)
    writer.writerow([
        "id", "device_id", "fault_type", "remediation_mode", "trial_number",
        "injected_at", "detected_at", "remediation_started_at", "remediated_at",
        "verified_healthy_at", "mttd_seconds", "mttr_seconds", "notes",
    ])
    for incident in Incident.objects.select_related("device").all():
        writer.writerow([
            incident.id, incident.device.device_id, incident.fault_type,
            incident.remediation_mode, incident.trial_number,
            incident.injected_at.isoformat() if incident.injected_at else "",
            incident.detected_at.isoformat() if incident.detected_at else "",
            incident.remediation_started_at.isoformat() if incident.remediation_started_at else "",
            incident.remediated_at.isoformat() if incident.remediated_at else "",
            incident.verified_healthy_at.isoformat() if incident.verified_healthy_at else "",
            incident.mttd_seconds if incident.mttd_seconds is not None else "",
            incident.mttr_seconds if incident.mttr_seconds is not None else "",
            incident.notes,
        ])
    return response
