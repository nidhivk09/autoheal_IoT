from django.db import models
from django.utils import timezone

# A device with no telemetry for longer than this is considered offline.
# Kept in sync with the "device disconnected" Azure Monitor alert window
# documented in cloud/infra/monitor.bicep.
STALE_AFTER_SECONDS = 30


class Device(models.Model):
    class Status(models.TextChoices):
        ONLINE = "online", "Online"
        OFFLINE = "offline", "Offline"
        DEGRADED = "degraded", "Degraded"
        UNKNOWN = "unknown", "Unknown"

    device_id = models.CharField(max_length=100, unique=True)  # matches IoT Hub device identity
    display_name = models.CharField(max_length=100)
    device_type = models.CharField(max_length=50, default="raspberry_pi")
    is_physical = models.BooleanField(default=True)
    ip_address = models.CharField(max_length=50, blank=True)
    last_seen = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.UNKNOWN)

    class Meta:
        ordering = ["device_id"]

    def __str__(self) -> str:
        return self.display_name or self.device_id

    @property
    def is_stale(self) -> bool:
        if self.last_seen is None:
            return True
        return (timezone.now() - self.last_seen).total_seconds() > STALE_AFTER_SECONDS

    def latest_metrics(self) -> dict:
        """Most recent value per metric name, e.g. {'cpu_percent': 12.3, ...}."""
        recent = self.metrics.order_by("metric", "-timestamp").distinct("metric")
        return {m.metric: m.value for m in recent}


class DeviceMetric(models.Model):
    """One (metric, value) reading at a point in time. Written by the AKS
    ingestion service (cloud/ingestion_service/main.py) as it consumes IoT
    Hub telemetry - see that file for the exact insert this model mirrors."""

    device = models.ForeignKey(Device, on_delete=models.CASCADE, related_name="metrics")
    timestamp = models.DateTimeField()
    metric = models.CharField(max_length=50)
    value = models.FloatField()

    class Meta:
        indexes = [
            models.Index(fields=["device", "metric", "-timestamp"]),
        ]
        ordering = ["-timestamp"]

    def __str__(self) -> str:
        return f"{self.device.device_id} {self.metric}={self.value} @ {self.timestamp}"
