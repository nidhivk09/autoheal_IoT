"""Client for Django's internal API (webapp/incidents/internal_api.py)."""
from __future__ import annotations

import os
from datetime import datetime, timezone

import requests

DJANGO_INTERNAL_API_BASE = os.environ.get("DJANGO_INTERNAL_API_BASE", "").rstrip("/")
INTERNAL_API_TOKEN = os.environ.get("INTERNAL_API_TOKEN", "")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _headers() -> dict:
    return {"Authorization": f"Bearer {INTERNAL_API_TOKEN}", "Content-Type": "application/json"}


def _url(path: str) -> str:
    return f"{DJANGO_INTERNAL_API_BASE}/internal/{path.lstrip('/')}"


def get_active_incident(device_id: str) -> dict | None:
    resp = requests.get(_url("incidents/active/"), params={"device_id": device_id}, headers=_headers(), timeout=10)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json()


def get_incident(incident_id: int) -> dict:
    resp = requests.get(_url(f"incidents/{incident_id}/"), headers=_headers(), timeout=10)
    resp.raise_for_status()
    return resp.json()


def create_organic_incident(device_id: str, signal: str, detected_at: str) -> dict:
    resp = requests.post(
        _url("incidents/"),
        json={"device_id": device_id, "detection_signal": signal, "detected_at": detected_at},
        headers=_headers(),
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def update_incident(incident_id: int, fields: dict) -> bool:
    """Returns False if Django refused because another tier already resolved it."""
    resp = requests.patch(_url(f"incidents/{incident_id}/"), json=fields, headers=_headers(), timeout=10)
    if resp.status_code == 409:
        return False
    resp.raise_for_status()
    return True


def claim_incident(incident_id: int) -> bool:
    resp = requests.post(_url(f"incidents/{incident_id}/claim/"), headers=_headers(), timeout=10)
    if resp.status_code == 409:
        return False
    resp.raise_for_status()
    return True


def record_action(incident_id: int, action: str, started_at: str, success: bool | None,
                  detail: str = "", tier: str = "cloud") -> None:
    resp = requests.post(
        _url(f"incidents/{incident_id}/actions/"),
        json={
            "tier": tier, "action": action, "started_at": started_at, "finished_at": now_iso(),
            "success": success, "detail": detail[:2000],
        },
        headers=_headers(),
        timeout=10,
    )
    resp.raise_for_status()


def recent_cloud_action_count(device_id: str, window_s: int) -> int:
    resp = requests.get(
        _url(f"devices/{device_id}/recent-cloud-actions/"),
        params={"window_s": window_s},
        headers=_headers(),
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()["count"]


def push_config(device_id: str, reconcile: bool) -> None:
    resp = requests.post(
        _url(f"devices/{device_id}/push-config/"),
        json={"reconcile": reconcile},
        headers=_headers(),
        timeout=20,
    )
    resp.raise_for_status()
