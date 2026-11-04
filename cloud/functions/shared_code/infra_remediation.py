"""
Hub- and cluster-level remediation, triggered by Azure Monitor alerts
(common alert schema). These aren't tied to a device incident: per-device
failures are detected by the ingestion service and handled by the
escalation ladder in remediation_logic.py instead.

The old "device disconnected" alert is gone - Azure Monitor's IoT Hub
metrics carry no device id, so it couldn't tell which device in a fleet had
gone quiet. Per-device staleness detection replaced it.
"""
from __future__ import annotations

import logging
import os
import tempfile
from datetime import datetime, timezone
from enum import Enum

logger = logging.getLogger("remediation.infra")


class AlertKind(str, Enum):
    IOTHUB_THROTTLING = "alert-iothub-throttling"
    AKS_CRASH_LOOP = "alert-aks-crash-loop"
    UNKNOWN = "unknown"


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

    return {"actions": [{"action": "scale_iothub_units", "from": current_units, "to": new_units}]}


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
                    "annotations": {"autoheal/restartedAt": datetime.now(timezone.utc).isoformat()}
                }
            }
        }
    }
    apps_v1.patch_namespaced_deployment(deployment_name, namespace, patch_body)

    return {"actions": [{"action": "restart_aks_deployment", "deployment": deployment_name}]}


HANDLERS = {
    AlertKind.IOTHUB_THROTTLING: remediate_iothub_throttling,
    AlertKind.AKS_CRASH_LOOP: remediate_aks_crash_loop,
}


def handle_alert(payload: dict) -> dict:
    kind = classify_alert(payload)
    logger.info("alert classified as %s", kind)
    handler = HANDLERS.get(kind)
    if handler is None:
        return {"status": "ignored", "reason": "unrecognized alert"}
    return {"status": "remediated", "alert": kind.value, **handler()}
