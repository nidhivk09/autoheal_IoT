from django.shortcuts import render

from .models import Device


def _device_context() -> dict:
    devices = list(Device.objects.all())
    for device in devices:
        # last_seen-derived status for display purposes only - the stored
        # `status` field is what alert rules / remediation act on, this is
        # just so the UI doesn't show a stale "online" badge for 30s before
        # the sweep job catches up.
        device.display_status = Device.Status.OFFLINE if device.is_stale else device.status
        device.latest = device.latest_metrics()
    return {"devices": devices}


def device_list(request):
    return render(request, "devices/list.html", _device_context())


def device_list_partial(request):
    """Polled every 10s via HTMX (hx-trigger='every 10s') from the device list page."""
    return render(request, "devices/_device_table.html", _device_context())
