from django.contrib import admin

from .models import Incident, RemediationAction


class RemediationActionInline(admin.TabularInline):
    model = RemediationAction
    extra = 0
    readonly_fields = ("tier", "action", "started_at", "finished_at", "success", "detail")


@admin.register(Incident)
class IncidentAdmin(admin.ModelAdmin):
    list_display = (
        "id", "device", "fault_type", "remediation_mode", "edge_healing", "trial_number",
        "injected_at", "mttd_seconds", "mttr_seconds", "resolved_by", "phase",
    )
    list_filter = ("fault_type", "remediation_mode", "edge_healing", "resolved_by", "device")
    date_hierarchy = "injected_at"
    readonly_fields = ("mttd_seconds", "mttr_seconds", "phase")
    inlines = [RemediationActionInline]
