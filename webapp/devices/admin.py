from django.contrib import admin

from .models import Device, DeviceMetric


@admin.register(Device)
class DeviceAdmin(admin.ModelAdmin):
    list_display = ("device_id", "display_name", "device_type", "status", "last_seen", "is_physical")
    list_filter = ("status", "device_type", "is_physical")
    search_fields = ("device_id", "display_name")


@admin.register(DeviceMetric)
class DeviceMetricAdmin(admin.ModelAdmin):
    list_display = ("device", "metric", "value", "timestamp")
    list_filter = ("metric",)
    search_fields = ("device__device_id",)
    date_hierarchy = "timestamp"
