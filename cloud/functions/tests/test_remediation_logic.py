"""
Escalation-ladder tests with fake devices and a fake Django API.

    cd cloud/functions && pip install requests pytest && python -m pytest tests
"""
import pytest

from shared_code import remediation_logic as rl

HEALTHY = {"healthy": True, "edge_busy": False, "checks": {}, "firmware": {"since_update_s": -1}}
UNHEALTHY = {"healthy": False, "edge_busy": False, "checks": {"telemetry_flowing": False},
             "firmware": {"since_update_s": -1, "rollback_possible": True}}


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def monotonic(self):
        return self.t

    def sleep(self, seconds):
        self.t += seconds


class FakeDevice:
    """A device whose health changes in response to methods, per scenario."""

    def __init__(self, health, fixes=(), reachable=True):
        self.health = dict(health)
        self.fixes = set(fixes)     # methods that make the device healthy
        self.reachable = reachable
        self.calls = []

    def invoke(self, device_id, method, payload=None, timeout_s=15):
        self.calls.append(method)
        if not self.reachable:
            return None, {"error": "DeviceNotOnline"}
        if method == "get_health":
            return 200, dict(self.health)
        if method in self.fixes:
            self.health = dict(HEALTHY)
        return 200 if method == "restart_service" else 202, {}


class FakeApi:
    def __init__(self, incident=None, recent_actions=0):
        self.incident = incident
        self.recent_actions = recent_actions
        self.actions = []
        self.claimed = False

    @staticmethod
    def now_iso():
        return "2026-11-04T10:00:00+00:00"

    def get_active_incident(self, device_id):
        if self.incident and not self.incident.get("verified_healthy_at"):
            return self.incident
        return None

    def get_incident(self, incident_id):
        return self.incident

    def create_organic_incident(self, device_id, signal, detected_at):
        self.incident = new_incident(mode="automated", id=99)
        self.incident["fault_type"] = "organic"
        return self.incident

    def update_incident(self, incident_id, fields):
        if self.incident.get("verified_healthy_at") and "verified_healthy_at" in fields:
            return False
        self.incident.update(fields)
        return True

    def claim_incident(self, incident_id):
        if self.claimed:
            return False
        self.claimed = True
        return True

    def record_action(self, incident_id, action, started_at, success, detail="", tier="cloud"):
        self.actions.append((action, success))

    def recent_cloud_action_count(self, device_id, window_s):
        return self.recent_actions

    def push_config(self, device_id, reconcile):
        self.actions.append(("push_config", reconcile))


def new_incident(mode="automated", id=1):
    return {"id": id, "device_id": "esp32-01", "remediation_mode": mode, "detected_at": None,
            "verified_healthy_at": None, "pending_action": "", "detection_signal": ""}


@pytest.fixture
def env(monkeypatch):
    clock = FakeClock()
    monkeypatch.setattr(rl, "sleep", clock.sleep)
    monkeypatch.setattr(rl, "monotonic", clock.monotonic)
    notes = []
    monkeypatch.setattr(rl, "notify_human", lambda text: notes.append(text) or True)

    def setup(device, api):
        monkeypatch.setattr(rl, "device", device)
        monkeypatch.setattr(rl, "api", api)
        return notes
    return setup


def detection(signal="telemetry_stale"):
    return {"kind": "detection", "device_id": "esp32-01", "signal": signal, "detected_at": "t0"}


def test_hung_app_is_fixed_by_restart_service(env):
    device = FakeDevice(UNHEALTHY, fixes={"restart_service"})
    api = FakeApi(new_incident())
    env(device, api)

    result = rl.handle_job(detection())

    assert result["status"] == "remediated"
    assert result["steps"] == ["probe", "restart_service"]
    assert api.incident["resolved_by"] == "cloud"
    assert api.incident["detected_by"] == "cloud"
    assert "reboot" not in device.calls


def test_bad_firmware_falls_through_to_rollback(env):
    health = {**UNHEALTHY, "firmware": {"since_update_s": 120, "rollback_possible": True}}
    device = FakeDevice(health, fixes={"rollback_firmware"})
    api = FakeApi(new_incident())
    env(device, api)

    result = rl.handle_job(detection())

    assert result["status"] == "remediated"
    assert result["steps"] == ["probe", "restart_service", "rollback_firmware"]


def test_rollback_skipped_without_recent_update(env):
    device = FakeDevice(UNHEALTHY, fixes={"reboot"})
    api = FakeApi(new_incident())
    env(device, api)

    result = rl.handle_job(detection())

    assert result["steps"] == ["probe", "restart_service", "rollback_firmware", "reboot"]
    assert "rollback_firmware" not in device.calls
    assert ("rollback_firmware", None) in api.actions


def test_unreachable_device_escalates_without_acting(env):
    device = FakeDevice(UNHEALTHY, reachable=False)
    api = FakeApi(new_incident())
    notes = env(device, api)

    result = rl.handle_job(detection())

    assert result["status"] == "escalated"
    assert api.incident["escalated_at"]
    assert set(device.calls) == {"get_health"}
    assert len(notes) == 1 and "can't be fixed from the cloud" in notes[0]


def test_ladder_exhausted_escalates(env):
    device = FakeDevice(UNHEALTHY)  # nothing helps
    api = FakeApi(new_incident())
    env(device, api)

    result = rl.handle_job(detection("heap_low"))

    assert result["status"] == "escalated"
    assert result["steps"] == ["probe", "restart_service", "reboot"]


def test_cloud_yields_while_edge_is_busy_then_stops(env):
    device = FakeDevice({**UNHEALTHY, "edge_busy": True})
    api = FakeApi(new_incident())
    env(device, api)

    original = device.invoke

    def invoke(*args, **kwargs):
        # the edge finishes its heal while the cloud is waiting
        if device.calls.count("get_health") == 3:
            api.incident["verified_healthy_at"] = "t1"
            api.incident["resolved_by"] = "edge"
        return original(*args, **kwargs)

    device.invoke = invoke
    result = rl.handle_job(detection())

    assert result["status"] == "resolved_elsewhere"
    assert api.incident["resolved_by"] == "edge"
    assert set(device.calls) == {"get_health"}


def test_healthy_device_without_event_is_credited_as_self(env):
    device = FakeDevice(HEALTHY)
    api = FakeApi(new_incident())
    env(device, api)

    result = rl.handle_job(detection())

    assert result["status"] == "resolved_elsewhere"
    assert api.incident["resolved_by"] == "self"


def test_approval_mode_pauses_before_disruptive_step_and_resumes(env):
    health = {**UNHEALTHY, "firmware": {"since_update_s": 60, "rollback_possible": True}}
    device = FakeDevice(health, fixes={"rollback_firmware"})
    api = FakeApi(new_incident(mode="approval"))
    notes = env(device, api)

    paused = rl.handle_job(detection())
    assert paused["status"] == "awaiting_approval"
    assert api.incident["pending_action"] == "telemetry_stale:rollback_firmware"
    assert "rollback_firmware" not in device.calls
    assert any("Approve" in n for n in notes)

    resumed = rl.handle_job({"kind": "approval", "incident_id": 1,
                             "action": "telemetry_stale:rollback_firmware"})
    assert resumed["status"] == "remediated"
    assert api.incident["pending_action"] == ""
    assert "rollback_firmware" in device.calls


def test_stale_approval_is_ignored(env):
    device = FakeDevice(UNHEALTHY)
    api = FakeApi(new_incident(mode="approval"))
    env(device, api)

    result = rl.handle_job({"kind": "approval", "incident_id": 1, "action": "telemetry_stale:reboot"})
    assert result["status"] == "ignored"
    assert device.calls == []


def test_manual_mode_only_detects(env):
    device = FakeDevice(UNHEALTHY)
    api = FakeApi(new_incident(mode="manual"))
    notes = env(device, api)

    result = rl.handle_job(detection())

    assert result["status"] == "detected_only"
    assert api.incident["detected_at"] == "t0"
    assert device.calls == [] and len(notes) == 1


def test_second_detection_for_claimed_incident_is_dropped(env):
    device = FakeDevice(UNHEALTHY, fixes={"restart_service"})
    api = FakeApi(new_incident())
    api.claimed = True
    env(device, api)

    assert rl.handle_job(detection())["status"] == "already_claimed"
    assert device.calls == []


def test_circuit_breaker_escalates_instead_of_acting(env):
    device = FakeDevice(UNHEALTHY)
    api = FakeApi(new_incident(), recent_actions=rl.CIRCUIT_MAX_ACTIONS)
    env(device, api)

    result = rl.handle_job(detection())

    assert result["status"] == "escalated"
    assert "circuit breaker" in result["reason"]
    assert device.calls == []


def test_config_drift_uses_twin_reconcile_first(env):
    device = FakeDevice({**UNHEALTHY, "checks": {"config_in_sync": False}})
    api = FakeApi(new_incident())
    env(device, api)

    def push_config(device_id, reconcile):
        api.actions.append(("push_config", reconcile))
        device.health = dict(HEALTHY)

    api.push_config = push_config
    result = rl.handle_job(detection("config_drift"))

    assert result["status"] == "remediated"
    assert result["steps"] == ["probe", "reconcile_twin"]
    assert ("push_config", True) in api.actions


def test_organic_detection_ignored_when_device_healthy(env):
    device = FakeDevice(HEALTHY)
    api = FakeApi(None)
    env(device, api)

    assert rl.handle_job(detection())["status"] == "ignored"
    assert api.incident is None


def test_organic_incident_opened_when_device_unhealthy(env):
    device = FakeDevice(UNHEALTHY, fixes={"restart_service"})
    api = FakeApi(None)
    env(device, api)

    result = rl.handle_job(detection())

    assert result["incident_id"] == 99
    assert result["status"] == "remediated"
