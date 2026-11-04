"""
Tier-2 (cloud) remediation: per-device escalation ladders.

Jobs arrive on the remediation-jobs queue (see remediation_dispatcher):
  detection  {device_id, signal, detected_at, evidence}  from the ingestion service
  approval   {incident_id, action}                       from Django (approval mode)
  alert      {payload}                                   Azure Monitor common alert schema

For a detection the worker:
  1. finds the device's active incident (or opens an "organic" one if the
     device really is unhealthy and no trial is running),
  2. stamps detection (unless the edge already detected it earlier),
  3. in manual mode, notifies a human and stops,
  4. claims the incident so only one ladder runs per incident,
  5. checks the circuit breaker (too many cloud actions on this device
     recently -> stop and escalate instead of flapping),
  6. walks the ladder for the signal, cheapest step first, verifying health
     after each step; disruptive steps wait for a human in approval mode;
     the last step is always escalation to a human.

The ladder yields to the device: the probe step waits while get_health
reports edge_busy (on-device healing or OTA self-test in progress), and every
step first checks whether another tier has already resolved the incident.
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from enum import Enum

from . import device_client as device
from . import django_api as api
from . import infra_remediation
from .notify import incident_link, notify_human

logger = logging.getLogger("remediation.ladder")

PROBE_WAIT_S = int(os.environ.get("LADDER_PROBE_WAIT_S", "60"))
EDGE_YIELD_MAX_S = int(os.environ.get("LADDER_EDGE_YIELD_MAX_S", "90"))
SETTLE_S = 15                 # after a healthy probe, wait this long for the device's recovery event
POLL_S = 3
RECENT_UPDATE_WINDOW_S = 1800  # rollback_firmware only applies within 30 min of an OTA update
CIRCUIT_WINDOW_S = int(os.environ.get("CIRCUIT_BREAKER_WINDOW_S", "1800"))
CIRCUIT_MAX_ACTIONS = int(os.environ.get("CIRCUIT_BREAKER_MAX_ACTIONS", "6"))

# test seam: tests replace this to run ladders without real waiting
sleep = time.sleep
monotonic = time.monotonic


@dataclass(frozen=True)
class Step:
    name: str
    disruptive: bool = False
    verify_timeout_s: int = 30


STEPS = {s.name: s for s in [
    Step("probe"),
    Step("restart_service", verify_timeout_s=20),
    Step("reconcile_twin", verify_timeout_s=20),
    Step("apply_desired_config", verify_timeout_s=15),
    Step("rollback_firmware", disruptive=True, verify_timeout_s=120),
    Step("reboot", disruptive=True, verify_timeout_s=90),
    Step("escalate"),
]}

LADDERS = {
    # no telemetry: app hang, bad firmware, or the device is offline
    "telemetry_stale": ["probe", "restart_service", "rollback_firmware", "reboot", "escalate"],
    "heap_low": ["probe", "restart_service", "reboot", "escalate"],
    "message_flood": ["probe", "restart_service", "reboot", "escalate"],
    "config_drift": ["probe", "reconcile_twin", "apply_desired_config", "reboot", "escalate"],
}
DEFAULT_LADDER = ["probe", "restart_service", "reboot", "escalate"]


class Outcome(str, Enum):
    CONTINUE = "continue"                      # not fixed yet, try the next step
    SKIPPED = "skipped"                        # precondition not met
    RESOLVED = "resolved"                      # this step fixed it (verified)
    RESOLVED_ELSEWHERE = "resolved_elsewhere"  # edge / device / human got there first
    ESCALATE = "escalate"                      # jump straight to the human


@dataclass
class StepResult:
    outcome: Outcome
    ok: bool | None = None
    detail: str = ""


@dataclass
class Ctx:
    incident_id: int
    device_id: str
    signal: str
    health: dict = field(default_factory=dict)


def ladder_for(signal: str) -> list[str]:
    return LADDERS.get(signal, DEFAULT_LADDER)


# --- helpers ---

def _resolved(ctx: Ctx) -> bool:
    return bool(api.get_incident(ctx.incident_id).get("verified_healthy_at"))


def _get_health(ctx: Ctx) -> tuple[int | None, dict]:
    status, health = device.invoke(ctx.device_id, "get_health", timeout_s=10)
    if status == 200:
        ctx.health = health
    return status, health


def _verify(ctx: Ctx, timeout_s: int) -> StepResult:
    deadline = monotonic() + timeout_s
    last = "no answer"
    while monotonic() < deadline:
        sleep(POLL_S)
        if _resolved(ctx):
            return StepResult(Outcome.RESOLVED_ELSEWHERE, True, "resolved by another tier while verifying")
        status, health = _get_health(ctx)
        if status == 200 and health.get("healthy"):
            return StepResult(Outcome.RESOLVED, True, "verified healthy")
        last = f"checks={health.get('checks')}" if status == 200 else "unreachable"
    return StepResult(Outcome.CONTINUE, False, f"not healthy after {timeout_s}s ({last})")


def _method_step(ctx: Ctx, step: Step, method: str, payload: dict | None = None) -> StepResult:
    status, response = device.invoke(ctx.device_id, method, payload)
    if status is None or status >= 300:
        return StepResult(Outcome.CONTINUE, False, f"{method} failed: status={status} {response}")
    result = _verify(ctx, step.verify_timeout_s)
    result.detail = f"{method} -> {status}; {result.detail}"
    return result


# --- steps ---

def step_probe(ctx: Ctx, step: Step) -> StepResult:
    """Reachability + health check. Waits (without acting) while the device
    reports it's healing itself, and while it's unreachable - the cloud
    can't fix connectivity, it can only notice it and escalate."""
    deadline = monotonic() + PROBE_WAIT_S
    edge_deadline = monotonic() + EDGE_YIELD_MAX_S
    while True:
        if _resolved(ctx):
            return StepResult(Outcome.RESOLVED_ELSEWHERE, True, "already resolved")
        status, health = _get_health(ctx)
        if status == 200:
            if health.get("edge_busy") and monotonic() < edge_deadline:
                sleep(5)  # yield to the on-device healer / OTA self-test
                continue
            if health.get("healthy"):
                return _settle(ctx)
            return StepResult(Outcome.CONTINUE, True, f"reachable, unhealthy: checks={health.get('checks')}")
        if monotonic() > deadline:
            return StepResult(Outcome.ESCALATE, False,
                              f"unreachable for {PROBE_WAIT_S}s; connectivity can't be fixed from the cloud")
        sleep(5)


def _settle(ctx: Ctx) -> StepResult:
    """Device is healthy without the cloud doing anything. Give its recovery
    event a moment to land; if none does, credit the device itself."""
    deadline = monotonic() + SETTLE_S
    while monotonic() < deadline:
        if _resolved(ctx):
            return StepResult(Outcome.RESOLVED_ELSEWHERE, True, "device recovered; recovery event received")
        sleep(POLL_S)
    now = api.now_iso()
    api.update_incident(ctx.incident_id, {"remediated_at": now, "verified_healthy_at": now, "resolved_by": "self"})
    return StepResult(Outcome.RESOLVED_ELSEWHERE, True, "device healthy with no recovery event; credited as self")


def step_restart_service(ctx: Ctx, step: Step) -> StepResult:
    return _method_step(ctx, step, "restart_service")


def step_apply_desired_config(ctx: Ctx, step: Step) -> StepResult:
    return _method_step(ctx, step, "apply_desired_config")


def step_reconcile_twin(ctx: Ctx, step: Step) -> StepResult:
    """Twin-native fix for drift: bump desired.reconcile_token so the device
    reapplies its desired config."""
    try:
        api.push_config(ctx.device_id, reconcile=True)
    except Exception as exc:  # noqa: BLE001
        return StepResult(Outcome.CONTINUE, False, f"twin update failed: {exc}")
    result = _verify(ctx, step.verify_timeout_s)
    result.detail = f"desired.reconcile_token bumped; {result.detail}"
    return result


def step_rollback_firmware(ctx: Ctx, step: Step) -> StepResult:
    fw = ctx.health.get("firmware") or {}
    since = fw.get("since_update_s", -1)
    if not (0 <= since <= RECENT_UPDATE_WINDOW_S and fw.get("rollback_possible")):
        return StepResult(Outcome.SKIPPED, None, f"no recent OTA update (since_update_s={since})")
    return _method_step(ctx, step, "rollback_firmware")


def step_reboot(ctx: Ctx, step: Step) -> StepResult:
    return _method_step(ctx, step, "reboot")


STEP_HANDLERS = {
    "probe": step_probe,
    "restart_service": step_restart_service,
    "reconcile_twin": step_reconcile_twin,
    "apply_desired_config": step_apply_desired_config,
    "rollback_firmware": step_rollback_firmware,
    "reboot": step_reboot,
}


def escalate(ctx: Ctx, reason: str) -> dict:
    started = api.now_iso()
    api.update_incident(ctx.incident_id, {"escalated_at": started})
    sent = notify_human(
        f":rotating_light: {ctx.device_id}: automated remediation gave up ({ctx.signal}). "
        f"{reason}. {incident_link(ctx.incident_id)}"
    )
    api.record_action(ctx.incident_id, "escalate", started, success=sent, detail=reason)
    return {"status": "escalated", "reason": reason}


def _request_approval(ctx: Ctx, step_name: str) -> dict:
    started = api.now_iso()
    api.update_incident(ctx.incident_id, {"pending_action": f"{ctx.signal}:{step_name}"})
    notify_human(
        f":warning: {ctx.device_id}: remediation wants to run `{step_name}` ({ctx.signal}). "
        f"Approve it at {incident_link(ctx.incident_id)}"
    )
    api.record_action(ctx.incident_id, "request_approval", started, success=None, detail=step_name)
    return {"status": "awaiting_approval", "step": step_name}


def run_ladder(ctx: Ctx, start_index: int = 0, approved_step: str | None = None) -> dict:
    ladder = ladder_for(ctx.signal)
    steps_run = []
    for name in ladder[start_index:]:
        incident = api.get_incident(ctx.incident_id)
        if incident.get("verified_healthy_at"):
            return {"status": "resolved_elsewhere", "steps": steps_run}
        if name == "escalate":
            return {**escalate(ctx, f"ladder exhausted after {', '.join(steps_run) or 'no steps'}"),
                    "steps": steps_run}

        step = STEPS[name]
        if step.disruptive and incident.get("remediation_mode") == "approval" and name != approved_step:
            return {**_request_approval(ctx, name), "steps": steps_run}

        started = api.now_iso()
        result = STEP_HANDLERS[name](ctx, step)
        steps_run.append(name)
        logger.info("incident %s step %s -> %s (%s)", ctx.incident_id, name, result.outcome.value, result.detail)
        api.record_action(ctx.incident_id, name, started, success=result.ok, detail=result.detail)

        if result.outcome == Outcome.RESOLVED:
            now = api.now_iso()
            credited = api.update_incident(ctx.incident_id, {
                "remediated_at": started, "verified_healthy_at": now, "resolved_by": "cloud",
            })
            return {"status": "remediated" if credited else "resolved_elsewhere", "steps": steps_run}
        if result.outcome == Outcome.RESOLVED_ELSEWHERE:
            return {"status": "resolved_elsewhere", "steps": steps_run}
        if result.outcome == Outcome.ESCALATE:
            return {**escalate(ctx, result.detail), "steps": steps_run}
    return {"status": "ladder_exhausted", "steps": steps_run}


# --- job handlers ---

def handle_detection(job: dict) -> dict:
    device_id = job["device_id"]
    signal = job["signal"]
    detected_at = job.get("detected_at") or api.now_iso()

    incident = api.get_active_incident(device_id)
    if incident is None:
        status, health = device.invoke(device_id, "get_health", timeout_s=10)
        if status == 200 and health.get("healthy"):
            return {"status": "ignored", "reason": "no active incident and the device is healthy"}
        incident = api.create_organic_incident(device_id, signal, detected_at)
    elif not incident.get("detected_at"):
        api.update_incident(incident["id"], {
            "detected_at": detected_at, "detected_by": "cloud", "detection_signal": signal,
        })

    ctx = Ctx(incident["id"], device_id, signal)

    if incident["remediation_mode"] == "manual":
        notify_human(f":bell: {device_id}: {signal} detected (manual mode). {incident_link(ctx.incident_id)}")
        return {"status": "detected_only", "incident_id": ctx.incident_id}

    if not api.claim_incident(ctx.incident_id):
        return {"status": "already_claimed", "incident_id": ctx.incident_id}

    recent = api.recent_cloud_action_count(device_id, CIRCUIT_WINDOW_S)
    if recent >= CIRCUIT_MAX_ACTIONS:
        return {**escalate(ctx, f"circuit breaker open: {recent} cloud actions on this device in "
                                f"{CIRCUIT_WINDOW_S // 60} min"), "incident_id": ctx.incident_id}

    return {"incident_id": ctx.incident_id, **run_ladder(ctx)}


def handle_approval(job: dict) -> dict:
    incident = api.get_incident(int(job["incident_id"]))
    pending = incident.get("pending_action") or ""
    if incident.get("verified_healthy_at") or pending != job.get("action"):
        return {"status": "ignored", "reason": "stale approval"}
    signal, _, step_name = pending.partition(":")
    ladder = ladder_for(signal)
    if step_name not in ladder:
        return {"status": "ignored", "reason": f"step {step_name} not in ladder for {signal}"}

    api.update_incident(incident["id"], {"pending_action": ""})
    ctx = Ctx(incident["id"], incident["device_id"], signal)
    # The probe step refreshes ctx.health, which rollback_firmware's precondition reads.
    _get_health(ctx)
    return {"incident_id": ctx.incident_id,
            **run_ladder(ctx, start_index=ladder.index(step_name), approved_step=step_name)}


def handle_alert(job: dict) -> dict:
    result = infra_remediation.handle_alert(job.get("payload") or {})
    if result.get("status") == "remediated":
        notify_human(f":gear: infrastructure alert {result.get('alert')} remediated: {result.get('actions')}")
    return result


JOB_HANDLERS = {
    "detection": handle_detection,
    "approval": handle_approval,
    "alert": handle_alert,
}


def handle_job(job: dict) -> dict:
    handler = JOB_HANDLERS.get(job.get("kind"))
    if handler is None:
        return {"status": "ignored", "reason": f"unknown job kind {job.get('kind')!r}"}
    return handler(job)
