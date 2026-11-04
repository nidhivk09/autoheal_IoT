import json
import logging

import azure.functions as func

from ..shared_code.remediation_logic import handle_job

logger = logging.getLogger("remediation_worker")


def main(msg: func.QueueMessage) -> None:
    job = json.loads(msg.get_body().decode("utf-8"))
    try:
        result = handle_job(job)
    except Exception:  # noqa: BLE001 - a retried half-run ladder would act twice; log and drop
        logger.exception("remediation job failed: %s", job)
        return
    logger.info("remediation job %s -> %s", job.get("kind"), json.dumps(result, default=str))
