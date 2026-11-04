"""
Thin IoT Hub service client over the REST API (direct methods + device twin).

Used instead of the azure-iot-hub SDK on the Django side because that SDK
pulls in uamqp, which has no wheels for recent Python versions. The calls
used here are small and stable:
  POST  /twins/{id}/methods   invoke a direct method
  GET   /twins/{id}           read a twin
  PATCH /twins/{id}           update desired properties
https://learn.microsoft.com/rest/api/iothub/service/devices
"""
from __future__ import annotations

import hmac
import os
import time
from base64 import b64decode, b64encode
from dataclasses import dataclass, field
from hashlib import sha256
from urllib.parse import quote_plus, urlencode

import requests

API_VERSION = "2021-04-12"


@dataclass
class MethodResult:
    # None when the device couldn't be reached at all (offline / timed out)
    status: int | None
    payload: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status is not None and 200 <= self.status < 300


def _parse_connection_string(conn_str: str) -> dict:
    parts = dict(p.split("=", 1) for p in conn_str.split(";") if "=" in p)
    missing = {"HostName", "SharedAccessKeyName", "SharedAccessKey"} - parts.keys()
    if missing:
        raise ValueError(f"IoT Hub connection string missing {sorted(missing)}")
    return parts


def generate_sas_token(uri: str, key: str, policy_name: str, ttl_seconds: int = 3600) -> str:
    expiry = int(time.time() + ttl_seconds)
    to_sign = f"{quote_plus(uri)}\n{expiry}".encode("utf-8")
    signature = b64encode(hmac.new(b64decode(key), to_sign, sha256).digest())
    token = {"sr": uri, "sig": signature, "se": str(expiry), "skn": policy_name}
    return "SharedAccessSignature " + urlencode(token)


class IoTHubService:
    def __init__(self, connection_string: str):
        parts = _parse_connection_string(connection_string)
        self.host = parts["HostName"]
        self._key = parts["SharedAccessKey"]
        self._policy = parts["SharedAccessKeyName"]
        self._token = ""
        self._token_expires = 0.0

    @classmethod
    def from_env(cls) -> "IoTHubService":
        conn_str = os.environ.get("IOTHUB_CONNECTION_STRING")
        if not conn_str:
            raise RuntimeError("IOTHUB_CONNECTION_STRING is not set (see .env.example)")
        return cls(conn_str)

    def _headers(self) -> dict:
        if time.time() > self._token_expires - 300:
            self._token = generate_sas_token(self.host, self._key, self._policy)
            self._token_expires = time.time() + 3600
        return {"Authorization": self._token, "Content-Type": "application/json"}

    def _url(self, device_id: str, suffix: str = "") -> str:
        return f"https://{self.host}/twins/{device_id}{suffix}?api-version={API_VERSION}"

    def invoke_method(
        self, device_id: str, method: str, payload: dict | None = None, timeout_s: int = 15
    ) -> MethodResult:
        body = {
            "methodName": method,
            "responseTimeoutInSeconds": timeout_s,
            "payload": payload or {},
        }
        try:
            resp = requests.post(
                self._url(device_id, "/methods"),
                json=body,
                headers=self._headers(),
                timeout=timeout_s + 10,
            )
        except requests.RequestException as exc:
            return MethodResult(None, {"error": str(exc)})

        if resp.status_code != 200:
            # 404 DeviceNotOnline / 504 timeout: the device never answered.
            return MethodResult(None, {"error": resp.text[:500], "http_status": resp.status_code})
        data = resp.json()
        return MethodResult(data.get("status"), data.get("payload") or {})

    def get_twin(self, device_id: str) -> dict:
        resp = requests.get(self._url(device_id), headers=self._headers(), timeout=15)
        resp.raise_for_status()
        return resp.json()

    def update_desired(self, device_id: str, desired: dict) -> dict:
        headers = {**self._headers(), "If-Match": "*"}
        resp = requests.patch(
            self._url(device_id),
            json={"properties": {"desired": desired}},
            headers=headers,
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()
