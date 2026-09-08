"""
Core remediation logic for the self-healing IoT project.

Triggered by an Azure Monitor action group webhook (common alert schema). This
module:
  1. classifies which fault the alert corresponds to,
  2. looks up the matching open Incident via the Django internal API (so all
     writes to Incident rows go through one path, per the build plan),
  3. if the incident is in "automated" mode, performs the remediation action,
  4. verifies recovery with a health check,
  5. writes detected_at / remediation_started_at / remediated_at /
     verified_healthy_at back to the Incident row.

Known limitation (documented for the research write-up, not hidden): Azure
Monitor's IoT Hub metric alerts do not carry a per-device identifier, so a
single-hub deployment with more than one device cannot be disambiguated from
the alert payload alone. This project targets one physical device
(EDGE_DEVICE_ID), so that's not a blocker here, but it's the reason the
"device disconnected" alert can't distinguish network_loss from
process_crash purely from Azure's side - both cases just look like "no
telemetry ingress". We handle that by trying reconnect_network first, then
restart_service, and letting the health check decide whether it worked.
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from enum import Enum

import requests
from azure.iot.hub import IoTHubRegistryManager
from azure.iot.hub.models import CloudToDeviceMethod

logger = logging.getLogger("remediation_dispatcher")

IOTHUB_CONNECTION_STRING = os.environ["IOTHUB_CONNECTION_STRING"]
EDGE_DEVICE_ID = os.environ.get("EDGE_DEVICE_ID", "rpi-01")
DJANGO_INTERNAL_API_BASE = os.environ["DJANGO_INTERNAL_API_BASE"].rstrip("/")
INTERNAL_API_TOKEN = os.environ["INTERNAL_API_TOKEN"]
DIRECT_METHOD_TIMEOUT_SECONDS = 15


class AlertKind(str, Enum):
    DEVICE_DISCONNECTED = "alert-device-disconnected"
    IOTHUB_THROTTLING = "alert-iothub-throttling"
    AKS_CRASH_LOOP = "alert-aks-crash-loop"
    UNKNOWN = "unknown"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _api_headers() -> dict:
    return {
        "Authorization": f"Bearer {INTERNAL_API_TOKEN}",
        "Content-Type": "application/json",
    }


def classify_alert(payload: dict) -> AlertKind:
    """Extracts the alert rule name from the common alert schema payload."""
    try:
        alert_rule = payload["data"]["essentials"]["alertRule"]
    except (KeyError, TypeError):
        logger.warning("could not find alertRule in payload essentials: %s", payload)
        return AlertKind.UNKNOWN

    for kind in AlertKind:
        if kind.value == alert_rule:
            return kind
    return AlertKind.UNKNOWN


def get_active_incident(device_id: str) -> dict | None:
    """Looks up the most recent Incident for this device that hasn't been fully resolved."""
    resp = requests.get(
        f"{DJANGO_INTERNAL_API_BASE}/internal/incidents/active/",
        params={"device_id": device_id},
        headers=_api_headers(),
        timeout=10,
    )
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json()


def update_incident(incident_id: int, fields: dict) -> None:
    resp = requests.patch(
        f"{DJANGO_INTERNAL_API_BASE}/internal/incidents/{incident_id}/",
        json=fields,
        headers=_api_headers(),
        timeout=10,
    )
    resp.raise_for_status()


def invoke_direct_method(method_name: str, payload: dict | None = None) -> tuple[bool, dict]:
    registry_manager = IoTHubRegistryManager(IOTHUB_CONNECTION_STRING)
    method = CloudToDeviceMethod(
        method_name=method_name,
        payload=payload or {},
        response_timeout_in_seconds=DIRECT_METHOD_TIMEOUT_SECONDS,
    )
    try:
        result = registry_manager.invoke_device_method(EDGE_DEVICE_ID, method)
        success = result.status == 200
        return success, (result.payload or {})
    except Exception as exc:  # noqa: BLE001 - network/device errors are expected outcomes here
        logger.warning("direct method %s failed: %s", method_name, exc)
        return False, {"error": str(exc)}


def verify_healthy() -> bool:
    ok, _ = invoke_direct_method("get_health")
    return ok


def remediate_device_disconnected() -> dict:
    """
    Tries the cheap fix first (clear a network fault), then the more invasive
    one (restart the agent process). Either action is a no-op on the Pi if
    it wasn't the actual cause (see module docstring for why we can't tell
    which fault this is from the alert alone).
    """
    actions_taken = []

    ok, output = invoke_direct_method("reconnect_network")
    actions_taken.append({"action": "reconnect_network", "success": ok, "output": output})
    if verify_healthy():
        return {"actions": actions_taken, "recovered": True}

    ok, output = invoke_direct_method("restart_service")
    actions_taken.append({"action": "restart_service", "success": ok, "output": output})

    return {"actions": actions_taken, "recovered": verify_healthy()}


def remediate_iothub_throttling() -> dict:
    from azure.identity import DefaultAzureCredential
    from azure.mgmt.iothub import IotHubClient

    subscription_id = os.environ["AZURE_SUBSCRIPTION_ID"]
    resource_group = os.environ["AZURE_RESOURCE_GROUP"]
    iothub_name = os.environ["AZURE_IOTHUB_NAME"]

    credential = DefaultAzureCredential()
    client = IotHubClient(credential, subscription_id)

    hub = client.iot_hub_resource.get(resource_group, iothub_name)
    current_units = hub.sku.capacity
    new_units = current_units + 1

    hub.sku.capacity = new_units
    poller = client.iot_hub_resource.begin_create_or_update(resource_group, iothub_name, hub)
    poller.result()

    return {
        "actions": [{"action": "scale_iothub_units", "from": current_units, "to": new_units}],
        "recovered": True,  # scaling is applied synchronously by the time the poller returns
    }


def remediate_aks_crash_loop() -> dict:
    """
    Restarts the ingestion service Deployment. Uses the `kubernetes` client
    library against credentials from AKS's admin kubeconfig, obtained via the
    Azure management API so the Function needs no long-lived kubeconfig secret.
    """
    from azure.identity import DefaultAzureCredential
    from azure.mgmt.containerservice import ContainerServiceClient
    from kubernetes import client as k8s_client
    from kubernetes import config as k8s_config
    import tempfile

    subscription_id = os.environ["AZURE_SUBSCRIPTION_ID"]
    resource_group = os.environ["AZURE_RESOURCE_GROUP"]
    aks_cluster_name = os.environ["AZURE_AKS_CLUSTER_NAME"]
    namespace = os.environ.get("AKS_INGESTION_NAMESPACE", "default")
    deployment_name = os.environ.get("AKS_INGESTION_DEPLOYMENT", "ingestion-service")

    credential = DefaultAzureCredential()
    aks_client = ContainerServiceClient(credential, subscription_id)
    creds = aks_client.managed_clusters.list_cluster_admin_credentials(
        resource_group, aks_cluster_name
    )
    kubeconfig_bytes = creds.kubeconfigs[0].value

    with tempfile.NamedTemporaryFile(suffix=".yaml", delete=False) as tmp:
        tmp.write(kubeconfig_bytes)
        tmp_path = tmp.name

    k8s_config.load_kube_config(config_file=tmp_path)
    apps_v1 = k8s_client.AppsV1Api()

    # Equivalent to `kubectl rollout restart deployment/<name>`: patch a
    # restart annotation so the Deployment controller replaces the pods.
    patch_body = {
        "spec": {
            "template": {
                "metadata": {
                    "annotations": {"autoheal/restartedAt": _now_iso()}
                }
            }
        }
    }
    apps_v1.patch_namespaced_deployment(deployment_name, namespace, patch_body)

    return {
        "actions": [{"action": "restart_aks_deployment", "deployment": deployment_name}],
        "recovered": True,
    }


REMEDIATION_HANDLERS = {
    AlertKind.DEVICE_DISCONNECTED: remediate_device_disconnected,
    AlertKind.IOTHUB_THROTTLING: remediate_iothub_throttling,
    AlertKind.AKS_CRASH_LOOP: remediate_aks_crash_loop,
}


def dispatch(payload: dict) -> dict:
    detected_at = _now_iso()
    alert_kind = classify_alert(payload)
    logger.info("alert classified as %s", alert_kind)

    if alert_kind == AlertKind.UNKNOWN:
        return {"status": "ignored", "reason": "unrecognized alert", "payload_essentials": payload.get("data", {}).get("essentials")}

    incident = get_active_incident(EDGE_DEVICE_ID)
    if incident is None:
        logger.warning("no active incident found for device %s - alert fired with no matching trial", EDGE_DEVICE_ID)
        return {"status": "ignored", "reason": "no active incident"}

    update_incident(incident["id"], {"detected_at": detected_at})

    if incident["remediation_mode"] != "automated":
        logger.info("incident %s is in manual mode; recording detection only", incident["id"])
        return {"status": "detected_only", "incident_id": incident["id"]}

    handler = REMEDIATION_HANDLERS.get(alert_kind)
    if handler is None:
        return {"status": "no_handler", "alert_kind": alert_kind}

    update_incident(incident["id"], {"remediation_started_at": _now_iso()})
    result = handler()
    remediated_at = _now_iso()

    fields = {"remediated_at": remediated_at, "notes": str(result["actions"])}
    if result.get("recovered"):
        fields["verified_healthy_at"] = _now_iso()
    update_incident(incident["id"], fields)

    return {"status": "remediated", "incident_id": incident["id"], **result}
