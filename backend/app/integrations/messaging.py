"""Messaging integrations: Zalo OA and Facebook Messenger.

Both are webhook-driven: an inbound normalised message is routed to the agent,
and the reply is sent back through the platform's send API. When credentials
are absent the senders log instead of calling out, so the app always runs.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass
class InboundMessage:
    """Platform-neutral inbound message."""

    channel: str  # "zalo" | "messenger"
    external_user_id: str
    text: str
    external_conversation_id: str | None = None
    raw: dict[str, Any] | None = None


class ZaloClient:
    """Zalo Official Account messaging client."""

    BASE = "https://openapi.zalo.me/v3.0/oa/message/cs"

    def __init__(self) -> None:
        self._settings = get_settings()

    @property
    def configured(self) -> bool:
        return bool(self._settings.zalo_oa_access_token)

    async def send_text(self, user_id: str, text: str) -> bool:
        if not self.configured:
            logger.info("zalo_send_skipped", user_id=user_id)
            return False
        payload = {"recipient": {"user_id": user_id}, "message": {"text": text}}
        headers = {"access_token": self._settings.zalo_oa_access_token or ""}
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(self.BASE, json=payload, headers=headers)
        ok = resp.status_code < 400
        logger.info("zalo_send", user_id=user_id, ok=ok, status=resp.status_code)
        return ok


class MessengerClient:
    """Facebook Messenger Send API client."""

    BASE = "https://graph.facebook.com/v21.0/me/messages"

    def __init__(self) -> None:
        self._settings = get_settings()

    @property
    def configured(self) -> bool:
        return bool(self._settings.facebook_page_access_token)

    async def send_text(self, recipient_id: str, text: str) -> bool:
        if not self.configured:
            logger.info("messenger_send_skipped", recipient_id=recipient_id)
            return False
        params = {"access_token": self._settings.facebook_page_access_token}
        payload = {
            "recipient": {"id": recipient_id},
            "message": {"text": text},
        }
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(self.BASE, params=params, json=payload)
        ok = resp.status_code < 400
        logger.info("messenger_send", recipient_id=recipient_id, ok=ok, status=resp.status_code)
        return ok


def verify_meta_webhook(mode: str, token: str, challenge: str) -> str | None:
    """Validate the Facebook/Meta webhook verification handshake."""
    settings = get_settings()
    if (
        mode == "subscribe"
        and settings.facebook_verify_token
        and hmac.compare_digest(token, settings.facebook_verify_token)
    ):
        return challenge
    return None


def verify_meta_signature(raw_body: bytes, signature_header: str | None) -> bool:
    """Verify the ``X-Hub-Signature-256`` header sent by Meta.

    Meta signs the *raw* request body with the app secret using HMAC-SHA256.
    Returns False when the app secret is not configured, so an unconfigured
    deployment cannot accept unauthenticated webhook traffic.
    """
    settings = get_settings()
    app_secret = settings.facebook_app_secret
    if not app_secret or not signature_header:
        return False
    if not signature_header.startswith("sha256="):
        return False
    provided = signature_header.removeprefix("sha256=")
    expected = hmac.new(app_secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(provided, expected)


def verify_zalo_signature(
    raw_body: bytes, timestamp: str | None, signature_header: str | None
) -> bool:
    """Verify Zalo OA's webhook signature.

    Zalo computes ``sha256(app_id + raw_body + timestamp + oa_secret)`` and
    sends it in the ``X-ZEvent-Signature`` header (``mac=...`` prefix may be
    present). The OA secret is the ``zalo_app_secret`` setting.
    """
    settings = get_settings()
    secret = settings.zalo_app_secret
    if not secret or not signature_header:
        return False
    provided = signature_header
    if "=" in provided:
        provided = provided.split("=", 1)[1]

    payload = f"{settings.zalo_app_id or ''}{raw_body.decode('utf-8', 'ignore')}"
    payload += f"{timestamp or ''}{secret}"
    expected = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return hmac.compare_digest(provided.strip(), expected)
