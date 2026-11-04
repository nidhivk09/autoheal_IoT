"""
Firmware releases in Azure Blob Storage.

CI (.github/workflows/build-firmware.yml) uploads two images per build to the
private `firmware` container, tagged with blob metadata:
  firmware-<version>.bin       metadata variant=good
  firmware-<version>-bad.bin   metadata variant=bad   (for the bad_firmware fault)

Devices never get storage credentials; every OTA call hands the device a
short-lived read-only SAS URL generated here.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from azure.storage.blob import BlobSasPermissions, BlobServiceClient, generate_blob_sas

SAS_TTL = timedelta(hours=2)


@dataclass
class FirmwareRelease:
    blob_name: str
    version: str
    variant: str  # "good" | "bad"
    size: int
    uploaded_at: datetime | None


def _service() -> BlobServiceClient:
    conn_str = os.environ.get("FIRMWARE_STORAGE_CONNECTION_STRING")
    if not conn_str:
        raise RuntimeError("FIRMWARE_STORAGE_CONNECTION_STRING is not set (see .env.example)")
    return BlobServiceClient.from_connection_string(conn_str)


def _container() -> str:
    return os.environ.get("FIRMWARE_CONTAINER", "firmware")


def list_releases() -> list[FirmwareRelease]:
    container = _service().get_container_client(_container())
    releases = []
    for blob in container.list_blobs(include=["metadata"]):
        meta = blob.metadata or {}
        releases.append(FirmwareRelease(
            blob_name=blob.name,
            version=meta.get("version", blob.name),
            variant=meta.get("variant", "good"),
            size=blob.size,
            uploaded_at=blob.last_modified,
        ))
    releases.sort(key=lambda r: r.uploaded_at or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return releases


def latest_release(variant: str) -> FirmwareRelease | None:
    return next((r for r in list_releases() if r.variant == variant), None)


def find_release(blob_name: str) -> FirmwareRelease | None:
    return next((r for r in list_releases() if r.blob_name == blob_name), None)


def sas_url(blob_name: str) -> str:
    service = _service()
    token = generate_blob_sas(
        account_name=service.account_name,
        container_name=_container(),
        blob_name=blob_name,
        account_key=service.credential.account_key,
        permission=BlobSasPermissions(read=True),
        expiry=datetime.now(timezone.utc) + SAS_TTL,
    )
    return f"{service.url.rstrip('/')}/{_container()}/{blob_name}?{token}"
