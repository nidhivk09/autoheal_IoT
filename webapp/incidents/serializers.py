from rest_framework import serializers

from .models import Incident, RemediationAction


class IncidentSerializer(serializers.ModelSerializer):
    mttd_seconds = serializers.FloatField(read_only=True)
    mttr_seconds = serializers.FloatField(read_only=True)
    phase = serializers.CharField(read_only=True)
    device_id = serializers.CharField(source="device.device_id", read_only=True)

    class Meta:
        model = Incident
        fields = [
            "id", "device", "device_id", "fault_type", "remediation_mode", "edge_healing",
            "injected_at", "detected_at", "remediation_started_at",
            "remediated_at", "verified_healthy_at", "detected_by", "detection_signal",
            "resolved_by", "escalated_at", "pending_action", "messages_dropped",
            "trial_number", "notes", "mttd_seconds", "mttr_seconds", "phase",
        ]
        read_only_fields = fields


class IncidentUpdateSerializer(serializers.ModelSerializer):
    """Used only by the internal API - the Function is allowed to set the
    lifecycle fields and notes, nothing about the trial itself."""

    class Meta:
        model = Incident
        fields = [
            "detected_at", "remediation_started_at", "remediated_at", "verified_healthy_at",
            "detected_by", "detection_signal", "resolved_by", "escalated_at", "pending_action",
            "notes",
        ]


class RemediationActionSerializer(serializers.ModelSerializer):
    class Meta:
        model = RemediationAction
        fields = ["id", "incident", "tier", "action", "started_at", "finished_at", "success", "detail"]
        read_only_fields = ["id", "incident"]
