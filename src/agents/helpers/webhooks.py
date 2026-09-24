import logging
from typing import Any, Dict, Optional

import httpx


async def send_webhook_response(
    webhook_url: str,
    payload: Dict[str, Any],
    timeout_seconds: float,
    logger: Optional[logging.Logger] = None,
) -> None:
    active_logger = logger or logging.getLogger(__name__)
    
    # Ignorar si está vacío o es un placeholder de GHL (ej: [[webhook_url]], {{webhook_url}})
    if not webhook_url or webhook_url.startswith("[[") or webhook_url.startswith("{{"):
        active_logger.info("Skipping webhook response: URL is empty or a placeholder (%s)", webhook_url)
        return

    active_logger.info("Posting webhook response to %s with status=%s", webhook_url, payload.get("status"))
    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        try:
            response = await client.post(webhook_url, json=payload)
            response.raise_for_status()
            active_logger.info("Webhook sent successfully to %s", webhook_url)
        except Exception as exc:
            active_logger.error("Failed to send webhook to %s: %s", webhook_url, exc)
