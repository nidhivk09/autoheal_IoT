"""Build both firmware variants locally and upload them for OTA.

Does the same job as .github/workflows/build-firmware.yml, without needing
GitHub secrets: builds the good and the deliberately bad image with a version
stamp, then uploads them to the `firmware` blob container with the metadata
the dashboard reads (controller/firmware_store.py).

    cd firmware
    python scripts/release.py --version 1.0.1

Needs PlatformIO (`pio`) on PATH, azure-storage-blob (installed in the webapp
venv), and FIRMWARE_STORAGE_CONNECTION_STRING in the repo-root .env.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

FIRMWARE_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = FIRMWARE_DIR.parent


def load_env() -> None:
    env_file = REPO_ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def build(pio: str, env: str, version: str) -> Path:
    print(f"==> building {env} as {version}")
    flags = f'-DFW_VERSION=\\"{version}\\"'
    subprocess.run(
        [pio, "run", "-e", env],
        cwd=FIRMWARE_DIR,
        env={**os.environ, "PLATFORMIO_BUILD_FLAGS": flags},
        check=True,
    )
    return FIRMWARE_DIR / ".pio" / "build" / env / "firmware.bin"


def upload(path: Path, blob_name: str, version: str, variant: str) -> None:
    from azure.storage.blob import BlobServiceClient

    conn_str = os.environ.get("FIRMWARE_STORAGE_CONNECTION_STRING")
    if not conn_str:
        sys.exit("FIRMWARE_STORAGE_CONNECTION_STRING is not set in .env")
    container = BlobServiceClient.from_connection_string(conn_str).get_container_client(
        os.environ.get("FIRMWARE_CONTAINER", "firmware")
    )
    with path.open("rb") as f:
        container.upload_blob(blob_name, f, overwrite=True, metadata={"version": version, "variant": variant})
    print(f"==> uploaded {blob_name} ({path.stat().st_size:,} bytes, variant={variant})")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", required=True, help="e.g. 1.0.1 (the bad build gets a -bad suffix)")
    parser.add_argument("--pio", default=shutil.which("pio") or "pio", help="path to the PlatformIO CLI")
    parser.add_argument("--skip-build", action="store_true", help="upload the existing .pio build output")
    args = parser.parse_args()

    load_env()
    good_version, bad_version = args.version, f"{args.version}-bad"
    dist = FIRMWARE_DIR / "dist"
    dist.mkdir(exist_ok=True)

    for env, version, variant in (("esp32dev", good_version, "good"), ("esp32dev_bad", bad_version, "bad")):
        built = FIRMWARE_DIR / ".pio" / "build" / env / "firmware.bin"
        if not args.skip_build:
            built = build(args.pio, env, version)
        out = dist / f"firmware-{version}.bin"
        shutil.copyfile(built, out)
        upload(out, out.name, version, variant)

    print("\nDone. Refresh the dashboard's Firmware & OTA page to see both releases.")


if __name__ == "__main__":
    main()
