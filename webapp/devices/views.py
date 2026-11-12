import logging
import statistics

from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render

from .models import Device, FirmwareRollout
from .services import push_desired_config

logger = logging.getLogger(__name__)


def _annotate(device: Device) -> Device:
    # last_seen-derived status for display purposes only - the stored
    # `status` field is what the sweep job maintains, this is just so the UI
    # doesn't show a stale "online" badge for 10s before the sweep catches up.
    device.display_status = Device.Status.OFFLINE if device.is_stale else device.status
    device.latest = device.latest_metrics()
    running = Device.running_config(device.latest)
    device.config_in_sync = None if running is None else running == device.desired_config()
    return device


def _device_context() -> dict:
    return {"devices": [_annotate(d) for d in Device.objects.all()]}


def _fleet_summary(devices: list[Device]) -> dict:
    """Headline numbers for the Fleet page's summary row."""
    from incidents.models import Incident

    resolved = list(Incident.objects.filter(verified_healthy_at__isnull=False))
    mttrs = [i.mttr_seconds for i in resolved if i.mttr_seconds is not None]
    total = Incident.objects.count()
    edge = sum(1 for i in resolved if i.resolved_by == "edge")
    return {
        "devices_total": len(devices),
        "devices_online": sum(1 for d in devices if d.display_status == Device.Status.ONLINE),
        "incidents_total": total,
        "incidents_open": total - len(resolved),
        "recovered_pct": round(100 * len(resolved) / total) if total else None,
        "median_mttr": round(statistics.median(mttrs), 1) if mttrs else None,
        "edge_pct": round(100 * edge / len(resolved)) if resolved else None,
    }


def device_list(request):
    from incidents.models import Incident

    context = _device_context()
    context["summary"] = _fleet_summary(context["devices"])
    context["recent_incidents"] = Incident.objects.select_related("device").order_by("-injected_at")[:6]
    return render(request, "devices/list.html", context)


def device_list_partial(request):
    """Polled every 10s via HTMX (hx-trigger='every 10s') from the device list page."""
    return render(request, "devices/_device_table.html", _device_context())


def device_detail(request, device_id: str):
    device = _annotate(get_object_or_404(Device, device_id=device_id))
    context = {
        "device": device,
        "running": Device.running_config(device.latest),
        "incidents": device.incidents.all()[:10],
    }
    return render(request, "devices/detail.html", context)


CHART_METRICS = ("free_heap", "rssi", "edge_heals", "buffered")


def device_metrics(request, device_id: str):
    """Recent telemetry series for the device page's live charts."""
    device = get_object_or_404(Device, device_id=device_id)
    points = int(request.GET.get("points", 120))
    series = {}
    for name in CHART_METRICS:
        rows = list(device.metrics.filter(metric=name).order_by("-timestamp").values_list("timestamp", "value")[:points])
        rows.reverse()
        series[name] = {"t": [t.isoformat() for t, _ in rows], "v": [v for _, v in rows]}
    return JsonResponse(series)


def update_config(request, device_id: str):
    """Saves the desired config and pushes it to the device twin."""
    if request.method != "POST":
        return redirect("devices:detail", device_id=device_id)
    device = get_object_or_404(Device, device_id=device_id)
    try:
        device.desired_telemetry_interval_s = max(1, min(300, int(request.POST["telemetry_interval_s"])))
        device.desired_heap_low_bytes = max(0, min(200000, int(request.POST["heap_low_bytes"])))
    except (KeyError, ValueError):
        messages.error(request, "Interval and heap threshold must be numbers.")
        return redirect("devices:detail", device_id=device_id)
    device.desired_edge_healing = request.POST.get("edge_healing") == "on"
    device.save(update_fields=["desired_telemetry_interval_s", "desired_heap_low_bytes", "desired_edge_healing"])

    try:
        push_desired_config(device)
    except Exception as exc:  # noqa: BLE001
        logger.exception("twin update failed")
        messages.warning(request, f"Saved, but pushing to the device twin failed: {exc}")
    else:
        messages.success(request, "Desired config saved and pushed to the device twin.")
    return redirect("devices:detail", device_id=device_id)


def firmware(request):
    from controller.firmware_store import list_releases

    try:
        releases = list_releases()
        storage_error = ""
    except Exception as exc:  # noqa: BLE001 - page still useful without storage
        releases, storage_error = [], str(exc)
    context = {
        "releases": releases,
        "storage_error": storage_error,
        "devices": Device.objects.all(),
        "rollouts": FirmwareRollout.objects.prefetch_related("devices")[:10],
    }
    return render(request, "devices/firmware.html", context)


def start_rollout(request):
    if request.method != "POST":
        return redirect("devices:firmware")
    from controller.firmware_store import find_release

    from dashboard import scheduler

    from .rollout import run_rollout

    release = find_release(request.POST.get("blob_name", ""))
    devices = list(Device.objects.filter(device_id__in=request.POST.getlist("device_ids")))
    if release is None or not devices:
        messages.error(request, "Pick a release and at least one device.")
        return redirect("devices:firmware")

    canary = request.POST.get("canary_device_id")
    rollout = FirmwareRollout.objects.create(blob_name=release.blob_name, version=release.version)
    rollout.devices.set(devices)
    if canary:
        rollout.append_log(f"canary: {canary}")
    scheduler.get_scheduler().add_job(run_rollout, kwargs={"rollout_id": rollout.id, "canary_device_id": canary})
    messages.success(request, f"Rollout of {release.version} to {len(devices)} device(s) started.")
    return redirect("devices:firmware")
