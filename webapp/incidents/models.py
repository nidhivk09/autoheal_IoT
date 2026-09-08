from django.db import models

from devices.models import Device


class FaultType(models.TextChoices):
    NETWORK_LOSS = "network_loss", "Network loss"
    PROCESS_CRASH = "process_crash", "Process crash"
    RESOURCE_EXHAUSTION = "resource_exhaustion", "Resource exhaustion"
    IOT_HUB_THROTTLE = "iot_hub_throttle", "IoT Hub throttle"


class RemediationMode(models.TextChoices):
    MANUAL = "manual", "Manual"
    AUTOMATED = "automated", "Automated"


class Incident(models.Model):
    device = models.ForeignKey(Device, on_delete=models.CASCADE, related_name="incidents")
    fault_type = models.CharField(max_length=30, choices=FaultType.choices)
    remediation_mode = models.CharField(max_length=20, choices=RemediationMode.choices)

    injected_at = models.DateTimeField()
    detected_at = models.DateTimeField(null=True, blank=True)
    remediation_started_at = models.DateTimeField(null=True, blank=True)
    remediated_at = models.DateTimeField(null=True, blank=True)
    verified_healthy_at = models.DateTimeField(null=True, blank=True)

    trial_number = models.IntegerField(default=1)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-injected_at"]

    def __str__(self) -> str:
        return f"{self.device.device_id} / {self.fault_type} / {self.remediation_mode} #{self.trial_number}"

    @property
    def mttd_seconds(self) -> float | None:
        if self.detected_at:
            return (self.detected_at - self.injected_at).total_seconds()
        return None

    @property
    def mttr_seconds(self) -> float | None:
        if self.verified_healthy_at:
            return (self.verified_healthy_at - self.injected_at).total_seconds()
        return None

    @property
    def is_resolved(self) -> bool:
        return self.verified_healthy_at is not None

    @property
    def phase(self) -> str:
        """Human-readable state, used by the live-status incident page."""
        if self.verified_healthy_at:
            return "recovered"
        if self.remediation_started_at:
            return "remediating"
        if self.detected_at:
            return "detected"
        return "injected"
