from rest_framework import serializers

from .models import Incident


class IncidentSerializer(serializers.ModelSerializer):
    mttd_seconds = serializers.FloatField(read_only=True)
    mttr_seconds = serializers.FloatField(read_only=True)
    phase = serializers.CharField(read_only=True)
    device_id = serializers.CharField(source="device.device_id", read_only=True)

    class Meta:
        model = Incident
        fields = [
            "id", "device", "device_id", "fault_type", "remediation_mode",
            "injected_at", "detected_at", "remediation_started_at",
            "remediated_at", "verified_healthy_at", "trial_number", "notes",
            "mttd_seconds", "mttr_seconds", "phase",
        ]
        read_only_fields = ["device", "device_id", "fault_type", "remediation_mode", "injected_at", "trial_number"]


class IncidentUpdateSerializer(serializers.ModelSerializer):
    """Used only by the internal API - the Function is allowed to set the
    timestamp fields and notes, nothing else."""

    class Meta:
        model = Incident
        fields = ["detected_at", "remediation_started_at", "remediated_at", "verified_healthy_at", "notes"]
