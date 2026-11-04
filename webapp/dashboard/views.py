import csv
import statistics
from collections import Counter
from itertools import groupby

from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from devices.models import Device
from incidents.models import INJECTABLE_FAULT_TYPES, FaultType, Incident, RemediationMode


def _resolved_trial_incidents():
    return (
        Incident.objects.filter(verified_healthy_at__isnull=False)
        .exclude(fault_type=FaultType.ORGANIC)
        .select_related("device")
    )


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


def _edge_label(edge_healing: bool | None) -> str:
    return {True: "on", False: "off"}.get(edge_healing, "n/a")


def summary_stats() -> list[dict]:
    """One row per (fault_type, remediation_mode, edge_healing) with
    MTTR/MTTD stats and which tier resolved each incident - the table
    described in build plan Section 5, Phase 6.5, extended with the edge
    healing condition."""
    def key(i):
        return (i.fault_type, i.remediation_mode, _edge_label(i.edge_healing))

    incidents = sorted(_resolved_trial_incidents(), key=key)
    rows = []
    for (fault_type, mode, edge), group in groupby(incidents, key=key):
        group = list(group)
        dropped = [i.messages_dropped for i in group if i.messages_dropped is not None]
        rows.append({
            "fault_type": fault_type,
            "remediation_mode": mode,
            "edge_healing": edge,
            "mttr": _stats_for([i.mttr_seconds for i in group if i.mttr_seconds is not None]),
            "mttd": _stats_for([i.mttd_seconds for i in group if i.mttd_seconds is not None]),
            "resolved_by": dict(Counter(i.resolved_by or "unknown" for i in group)),
            "messages_dropped_mean": round(statistics.mean(dropped), 1) if dropped else None,
        })
    return rows


def analytics(request):
    context = {
        "stats": summary_stats(),
        "fault_types": INJECTABLE_FAULT_TYPES,
        "remediation_modes": RemediationMode.choices,
        "devices": Device.objects.all(),
        "organic_count": Incident.objects.filter(fault_type=FaultType.ORGANIC).count(),
        "unresolved_count": Incident.objects.filter(verified_healthy_at__isnull=True).count(),
    }
    return render(request, "dashboard/analytics.html", context)


def chart_data(request):
    """JSON feed for the Chart.js MTTR chart on dashboard/analytics.html:
    fault types on the x axis, one bar series per (mode, edge healing)
    condition, so edge-on vs edge-off sits side by side per fault."""
    rows = summary_stats()
    fault_types = sorted({r["fault_type"] for r in rows})
    conditions = sorted({(r["remediation_mode"], r["edge_healing"]) for r in rows})
    by_key = {(r["fault_type"], r["remediation_mode"], r["edge_healing"]): r for r in rows}
    datasets = []
    for mode, edge in conditions:
        datasets.append({
            "label": f"{mode} · edge {edge}",
            "means": [by_key.get((f, mode, edge), {}).get("mttr", {}).get("mean") for f in fault_types],
            "stdevs": [by_key.get((f, mode, edge), {}).get("mttr", {}).get("stdev") for f in fault_types],
        })
    return JsonResponse({"labels": fault_types, "datasets": datasets, "rows": rows})


def start_batch_trial(request):
    if request.method != "POST":
        return redirect("dashboard:analytics")

    device = get_object_or_404(Device, device_id=request.POST["device_id"])
    edge_choice = request.POST.get("edge_healing", "keep")

    from . import scheduler

    scheduler.get_scheduler().add_job(
        scheduler.run_batch_trial,
        kwargs={
            "device_id": device.device_id,
            "fault_type": request.POST["fault_type"],
            "remediation_mode": request.POST["remediation_mode"],
            "num_trials": int(request.POST.get("num_trials", 10)),
            "edge_healing": {"on": True, "off": False}.get(edge_choice),
        },
    )

    return redirect(reverse("dashboard:analytics") + "?batch_started=1")


def _iso(value) -> str:
    return value.isoformat() if value else ""


def csv_export(request):
    """Raw incident data for pandas/R - build plan Phase 6.5 / Phase 8.3."""
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="incidents.csv"'

    writer = csv.writer(response)
    writer.writerow([
        "id", "device_id", "fault_type", "remediation_mode", "edge_healing", "trial_number",
        "injected_at", "detected_at", "remediation_started_at", "remediated_at",
        "verified_healthy_at", "escalated_at", "detected_by", "detection_signal", "resolved_by",
        "messages_dropped", "mttd_seconds", "mttr_seconds", "cloud_actions", "notes",
    ])
    incidents = Incident.objects.select_related("device").prefetch_related("actions")
    for incident in incidents:
        cloud_actions = ";".join(a.action for a in incident.actions.all() if a.tier == "cloud")
        writer.writerow([
            incident.id, incident.device.device_id, incident.fault_type,
            incident.remediation_mode, _edge_label(incident.edge_healing), incident.trial_number,
            _iso(incident.injected_at), _iso(incident.detected_at),
            _iso(incident.remediation_started_at), _iso(incident.remediated_at),
            _iso(incident.verified_healthy_at), _iso(incident.escalated_at),
            incident.detected_by, incident.detection_signal, incident.resolved_by,
            incident.messages_dropped if incident.messages_dropped is not None else "",
            incident.mttd_seconds if incident.mttd_seconds is not None else "",
            incident.mttr_seconds if incident.mttr_seconds is not None else "",
            cloud_actions, incident.notes,
        ])
    return response
