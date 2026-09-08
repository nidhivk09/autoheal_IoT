from django.contrib import admin

from .models import Incident


@admin.register(Incident)
class IncidentAdmin(admin.ModelAdmin):
    list_display = (
        "id", "device", "fault_type", "remediation_mode", "trial_number",
        "injected_at", "mttd_seconds", "mttr_seconds", "phase",
    )
    list_filter = ("fault_type", "remediation_mode", "device")
    date_hierarchy = "injected_at"
    readonly_fields = ("mttd_seconds", "mttr_seconds", "phase")
