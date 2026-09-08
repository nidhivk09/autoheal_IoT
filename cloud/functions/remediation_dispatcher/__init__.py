import json
import logging

import azure.functions as func

from .remediation_logic import dispatch

logger = logging.getLogger("remediation_dispatcher")


def main(req: func.HttpRequest) -> func.HttpResponse:
    try:
        payload = req.get_json()
    except ValueError:
        logger.error("request body was not valid JSON")
        return func.HttpResponse("invalid JSON body", status_code=400)

    try:
        result = dispatch(payload)
    except Exception:  # noqa: BLE001 - always return a response, never let Functions retry-storm
        logger.exception("unhandled error in remediation dispatch")
        return func.HttpResponse(
            json.dumps({"status": "error"}), status_code=500, mimetype="application/json"
        )

    return func.HttpResponse(json.dumps(result), status_code=200, mimetype="application/json")
