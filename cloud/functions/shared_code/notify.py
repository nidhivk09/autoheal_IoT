"""Human escalation: posts to a chat incoming webhook.

ESCALATION_WEBHOOK_URL can be a Slack, Discord or Teams incoming webhook -
the body sets both "text" (Slack/Teams) and "content" (Discord).
"""
from __future__ import annotations

import logging
import os

import requests

logger = logging.getLogger("remediation.notify")


def incident_link(incident_id: int) -> str:
    base = os.environ.get("DASHBOARD_BASE_URL", "").rstrip("/")
    return f"{base}/incidents/{incident_id}/" if base else f"incident #{incident_id}"


def notify_human(text: str) -> bool:
    url = os.environ.get("ESCALATION_WEBHOOK_URL")
    if not url:
        logger.warning("ESCALATION_WEBHOOK_URL not set; escalation only logged: %s", text)
        return False
    try:
        resp = requests.post(url, json={"text": text, "content": text}, timeout=10)
        resp.raise_for_status()
        return True
    except requests.RequestException as exc:
        logger.warning("escalation webhook failed: %s", exc)
        return False
