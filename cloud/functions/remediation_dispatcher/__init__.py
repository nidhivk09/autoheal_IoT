"""
HTTP entry point for everything that wants remediation:
  - Azure Monitor action group webhook (common alert schema)
  - the ingestion service's per-device detections   {"kind": "detection", ...}
  - Django approval-mode resumes                     {"kind": "approval", ...}

It only validates and enqueues; remediation_worker does the work. Ladders
take minutes (verify waits, reboots), longer than an HTTP trigger may hold a
request open, and a queue gives each job its own execution.
"""
import json
import logging

import azure.functions as func

logger = logging.getLogger("remediation_dispatcher")

REQUIRED_FIELDS = {
    "detection": ("device_id", "signal"),
    "approval": ("incident_id", "action"),
}


def _to_job(payload: dict) -> dict | None:
    if isinstance(payload.get("data"), dict) and "essentials" in payload["data"]:
        return {"kind": "alert", "payload": payload}
    kind = payload.get("kind")
    required = REQUIRED_FIELDS.get(kind)
    if required is None or any(not payload.get(f) for f in required):
        return None
    return payload


def main(req: func.HttpRequest, job: func.Out[str]) -> func.HttpResponse:
    try:
        payload = req.get_json()
    except ValueError:
        return func.HttpResponse("invalid JSON body", status_code=400)

    queued = _to_job(payload) if isinstance(payload, dict) else None
    if queued is None:
        logger.warning("rejected remediation request: %s", payload)
        return func.HttpResponse("unrecognized request", status_code=400)

    job.set(json.dumps(queued))
    logger.info("queued %s job", queued["kind"])
    return func.HttpResponse(
        json.dumps({"queued": queued["kind"]}), status_code=202, mimetype="application/json"
    )
