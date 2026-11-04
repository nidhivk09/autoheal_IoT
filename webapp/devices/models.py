from django.db import models
from django.utils import timezone

# A device with no telemetry for longer than this is considered offline.
# Kept in sync with STALE_AFTER_SECONDS in cloud/ingestion_service/detector.py,
# which raises the per-device "telemetry_stale" detection.
STALE_AFTER_SECONDS = 30


class Device(models.Model):
    class Status(models.TextChoices):
        ONLINE = "online", "Online"
        OFFLINE = "offline", "Offline"
        DEGRADED = "degraded", "Degraded"
        UNKNOWN = "unknown", "Unknown"

    device_id = models.CharField(max_length=100, unique=True)  # matches IoT Hub device identity
    display_name = models.CharField(max_length=100)
    device_type = models.CharField(max_length=50, default="esp32")
    is_physical = models.BooleanField(default=True)
    ip_address = models.CharField(max_length=50, blank=True)
    last_seen = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.UNKNOWN)
    # written by the ingestion service from the "fw" telemetry field
    firmware_version = models.CharField(max_length=50, blank=True, db_default="")

    # Desired config - Django is the source of truth, the device twin is the
    # transport (devices/services.py:push_desired_config). The ingestion
    # service compares these columns against the running config the device
    # reports in telemetry to detect config drift, so they need DB defaults
    # for its raw-SQL inserts.
    desired_telemetry_interval_s = models.PositiveIntegerField(default=5, db_default=5)
    desired_heap_low_bytes = models.PositiveIntegerField(default=70000, db_default=70000)
    desired_edge_healing = models.BooleanField(default=True, db_default=True)

    # While set in the future (e.g. during a firmware rollout), the ingestion
    # service doesn't raise staleness detections for this device.
    maintenance_until = models.DateTimeField(null=True, blank=True)

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
        """Most recent value per metric name, e.g. {'free_heap': 182344.0, ...}."""
        recent = self.metrics.order_by("metric", "-timestamp").distinct("metric")
        return {m.metric: m.value for m in recent}

    def desired_config(self) -> dict:
        return {
            "telemetry_interval_s": self.desired_telemetry_interval_s,
            "heap_low_bytes": self.desired_heap_low_bytes,
            "edge_healing": self.desired_edge_healing,
        }

    @staticmethod
    def running_config(latest: dict) -> dict | None:
        """The running config the device last reported, from latest_metrics()."""
        keys = ("telemetry_interval_s", "heap_low_bytes", "edge_healing")
        if not all(k in latest for k in keys):
            return None
        return {
            "telemetry_interval_s": int(latest["telemetry_interval_s"]),
            "heap_low_bytes": int(latest["heap_low_bytes"]),
            "edge_healing": bool(latest["edge_healing"]),
        }


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


class FirmwareRollout(models.Model):
    """A staged OTA rollout of one firmware release across the fleet: the
    first device is the canary, and the rollout halts as soon as any device
    fails to come back healthy on the new version (devices/rollout.py)."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        COMPLETED = "completed", "Completed"
        HALTED = "halted", "Halted"

    blob_name = models.CharField(max_length=200)
    version = models.CharField(max_length=50)
    devices = models.ManyToManyField(Device, related_name="rollouts")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    created_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    log = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"rollout {self.version} ({self.status})"

    def append_log(self, line: str) -> None:
        stamp = timezone.now().strftime("%H:%M:%S")
        self.log += f"[{stamp}] {line}\n"
        self.save(update_fields=["log"])
