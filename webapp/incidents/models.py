from django.db import models

from devices.models import Device


class FaultType(models.TextChoices):
    NETWORK_LOSS = "network_loss", "Network loss (AP outage)"
    TASK_HANG = "task_hang", "Task hang (app component stops)"
    MEMORY_LEAK = "memory_leak", "Memory leak"
    MESSAGE_FLOOD = "message_flood", "Message flood (runaway telemetry)"
    CONFIG_DRIFT = "config_drift", "Config drift"
    BAD_FIRMWARE = "bad_firmware", "Bad firmware update"
    # Raised by real detections that don't belong to an injected trial.
    ORGANIC = "organic", "Organic (not injected)"


INJECTABLE_FAULT_TYPES = [c for c in FaultType.choices if c[0] != FaultType.ORGANIC]

# Which recovery-event signals count as healing which injected fault. An
# edge event with any other signal during a trial is logged as an action but
# doesn't resolve the incident.
EXPECTED_SIGNALS = {
    FaultType.NETWORK_LOSS: {"link_lost"},
    FaultType.TASK_HANG: {"heartbeat_lost"},
    FaultType.MEMORY_LEAK: {"heap_low"},
    FaultType.MESSAGE_FLOOD: {"message_flood"},
    FaultType.CONFIG_DRIFT: {"config_drift"},
    FaultType.BAD_FIRMWARE: {"ota_self_test_failed"},
}


class RemediationMode(models.TextChoices):
    MANUAL = "manual", "Manual"
    AUTOMATED = "automated", "Automated"
    APPROVAL = "approval", "Automated, approval for disruptive steps"


class Tier(models.TextChoices):
    EDGE = "edge", "Edge (on-device)"
    CLOUD = "cloud", "Cloud"
    HUMAN = "human", "Human"
    # Recovered without any credited remediation (e.g. the baseline
    # firmware's fixed-interval retry after the outage ended).
    SELF = "self", "Self-recovered"


class Incident(models.Model):
    device = models.ForeignKey(Device, on_delete=models.CASCADE, related_name="incidents")
    fault_type = models.CharField(max_length=30, choices=FaultType.choices)
    remediation_mode = models.CharField(max_length=20, choices=RemediationMode.choices)
    # Experiment condition: was on-device healing enabled for this trial?
    edge_healing = models.BooleanField(null=True)

    injected_at = models.DateTimeField()
    detected_at = models.DateTimeField(null=True, blank=True)
    remediation_started_at = models.DateTimeField(null=True, blank=True)
    remediated_at = models.DateTimeField(null=True, blank=True)
    verified_healthy_at = models.DateTimeField(null=True, blank=True)

    detected_by = models.CharField(max_length=10, choices=Tier.choices, blank=True)
    detection_signal = models.CharField(max_length=40, blank=True)
    resolved_by = models.CharField(max_length=10, choices=Tier.choices, blank=True)
    escalated_at = models.DateTimeField(null=True, blank=True)
    # Disruptive ladder step waiting for a human to approve (approval mode),
    # as "<detection signal>:<step>" so the Function can resume that ladder.
    pending_action = models.CharField(max_length=60, blank=True)
    # Telemetry messages the device dropped while it was offline.
    messages_dropped = models.IntegerField(null=True, blank=True)

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
    def pending_step(self) -> str:
        return self.pending_action.partition(":")[2] or self.pending_action

    @property
    def is_resolved(self) -> bool:
        return self.verified_healthy_at is not None

    @property
    def phase(self) -> str:
        """Human-readable state, used by the live-status incident page."""
        if self.verified_healthy_at:
            return "recovered"
        if self.pending_action:
            return "awaiting approval"
        if self.escalated_at:
            return "escalated"
        if self.remediation_started_at:
            return "remediating"
        if self.detected_at:
            return "detected"
        return "injected"


class RemediationAction(models.Model):
    """One step taken to heal an incident, by any tier - the per-incident
    timeline, and the history the remediation Function's circuit breaker
    counts (cloud/functions/shared_code/remediation_logic.py)."""

    incident = models.ForeignKey(Incident, on_delete=models.CASCADE, related_name="actions")
    tier = models.CharField(max_length=10, choices=Tier.choices)
    action = models.CharField(max_length=40)
    started_at = models.DateTimeField()
    finished_at = models.DateTimeField(null=True, blank=True)
    success = models.BooleanField(null=True)
    detail = models.TextField(blank=True)

    class Meta:
        ordering = ["started_at"]

    def __str__(self) -> str:
        return f"{self.tier}:{self.action} for incident {self.incident_id}"
