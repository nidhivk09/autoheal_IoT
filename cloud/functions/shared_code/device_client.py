"""Direct-method calls to devices through IoT Hub.

Uses the IoT Hub REST API rather than the azure-iot-hub SDK: that SDK
depends on uamqp, which no longer builds on current toolchains (same reason
controller/iothub_rest.py exists on the Django side).
  POST /twins/{id}/methods
https://learn.microsoft.com/rest/api/iothub/service/devices/invoke-method
"""
from __future__ import annotations

import hmac
import logging
import os
import time
from base64 import b64decode, b64encode
from hashlib import sha256
from urllib.parse import quote_plus, urlencode

import requests

logger = logging.getLogger("remediation.device")

API_VERSION = "2021-04-12"

_conn: dict | None = None
_token = ""
_token_expires = 0.0


def _connection() -> dict:
    global _conn
    if _conn is None:
        conn_str = os.environ["IOTHUB_CONNECTION_STRING"]
        _conn = dict(p.split("=", 1) for p in conn_str.split(";") if "=" in p)
    return _conn


def _auth_header() -> str:
    global _token, _token_expires
    if time.time() > _token_expires - 300:
        c = _connection()
        expiry = int(time.time() + 3600)
        to_sign = f"{quote_plus(c['HostName'])}\n{expiry}".encode("utf-8")
        sig = b64encode(hmac.new(b64decode(c["SharedAccessKey"]), to_sign, sha256).digest())
        _token = "SharedAccessSignature " + urlencode(
            {"sr": c["HostName"], "sig": sig, "se": str(expiry), "skn": c["SharedAccessKeyName"]}
        )
        _token_expires = float(expiry)
    return _token


def invoke(device_id: str, method: str, payload: dict | None = None, timeout_s: int = 15) -> tuple[int | None, dict]:
    """Returns (status, payload). status is None when the device couldn't be
    reached at all (offline, or it didn't answer in time)."""
    url = f"https://{_connection()['HostName']}/twins/{device_id}/methods?api-version={API_VERSION}"
    body = {"methodName": method, "responseTimeoutInSeconds": timeout_s, "payload": payload or {}}
    try:
        resp = requests.post(
            url,
            json=body,
            headers={"Authorization": _auth_header(), "Content-Type": "application/json"},
            timeout=timeout_s + 10,
        )
    except requests.RequestException as exc:
        logger.info("direct method %s on %s failed: %s", method, device_id, exc)
        return None, {"error": str(exc)}

    if resp.status_code != 200:
        # 404 DeviceNotOnline / 504 timeout: the device never answered.
        logger.info("direct method %s on %s failed: HTTP %s %s", method, device_id, resp.status_code, resp.text[:200])
        return None, {"error": resp.text[:500], "http_status": resp.status_code}
    data = resp.json()
    return data.get("status"), (data.get("payload") or {})
