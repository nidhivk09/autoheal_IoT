from django.contrib import admin, messages

from .models import Device, DeviceMetric, FirmwareRollout
from .services import push_desired_config

DESIRED_FIELDS = ("desired_telemetry_interval_s", "desired_heap_low_bytes", "desired_edge_healing")


@admin.register(Device)
class DeviceAdmin(admin.ModelAdmin):
    list_display = (
        "device_id", "display_name", "device_type", "status", "firmware_version",
        "desired_edge_healing", "last_seen",
    )
    list_filter = ("status", "device_type", "desired_edge_healing")
    search_fields = ("device_id", "display_name")

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        if not change or any(f in form.changed_data for f in DESIRED_FIELDS):
            try:
                push_desired_config(obj)
            except Exception as exc:  # noqa: BLE001 - saved locally either way
                self.message_user(request, f"Saved, but pushing config to the twin failed: {exc}", messages.WARNING)


@admin.register(DeviceMetric)
class DeviceMetricAdmin(admin.ModelAdmin):
    list_display = ("device", "metric", "value", "timestamp")
    list_filter = ("metric",)
    search_fields = ("device__device_id",)
    date_hierarchy = "timestamp"


@admin.register(FirmwareRollout)
class FirmwareRolloutAdmin(admin.ModelAdmin):
    list_display = ("id", "version", "status", "created_at", "finished_at")
    list_filter = ("status",)
    readonly_fields = ("log",)
