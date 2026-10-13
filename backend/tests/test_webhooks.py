"""Tests for webhook signature verification.

These guard the public webhook endpoints: an unsigned payload must never reach
the agent, because that would let anyone drive the LLM and burn budget.
"""

from __future__ import annotations

import hashlib
import hmac
import json

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.integrations.messaging import (
    verify_meta_signature,
    verify_meta_webhook,
    verify_zalo_signature,
)
from app.models.agent import Conversation

META_SECRET = "meta-app-secret-for-tests"


@pytest.fixture
def configured_webhooks(monkeypatch):
    """Configure both providers with deterministic secrets."""
    settings = get_settings()
    monkeypatch.setattr(settings, "facebook_app_secret", META_SECRET, raising=False)
    monkeypatch.setattr(settings, "facebook_verify_token", "verify-me", raising=False)
    monkeypatch.setattr(settings, "zalo_app_secret", "zalo-secret", raising=False)
    monkeypatch.setattr(settings, "zalo_app_id", "123456", raising=False)
    monkeypatch.setattr(settings, "webhook_require_signature", True, raising=False)
    return settings


def _meta_sig(body: bytes, secret: str = META_SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


# --- unit level ------------------------------------------------------------


def test_meta_signature_accepts_valid(configured_webhooks):
    body = b'{"hello":"world"}'
    assert verify_meta_signature(body, _meta_sig(body)) is True


def test_meta_signature_rejects_tampered_body(configured_webhooks):
    body = b'{"hello":"world"}'
    sig = _meta_sig(body)
    assert verify_meta_signature(b'{"hello":"evil"}', sig) is False


def test_meta_signature_rejects_wrong_secret(configured_webhooks):
    body = b"{}"
    assert verify_meta_signature(body, _meta_sig(body, "wrong-secret")) is False


def test_meta_signature_rejects_missing_header(configured_webhooks):
    assert verify_meta_signature(b"{}", None) is False


def test_meta_signature_rejects_wrong_prefix(configured_webhooks):
    body = b"{}"
    digest = hmac.new(META_SECRET.encode(), body, hashlib.sha256).hexdigest()
    assert verify_meta_signature(body, f"sha1={digest}") is False


def test_meta_signature_false_when_secret_unset(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "facebook_app_secret", None, raising=False)
    body = b"{}"
    # Without a configured secret we cannot verify, so we must refuse.
    assert verify_meta_signature(body, _meta_sig(body)) is False


def test_zalo_signature_accepts_valid(configured_webhooks):
    body = b'{"event":"user_send_text"}'
    ts = "1700000000"
    app_id, secret = "123456", "zalo-secret"
    expected = hashlib.sha256(f"{app_id}{body.decode()}{ts}{secret}".encode()).hexdigest()
    assert verify_zalo_signature(body, ts, f"mac={expected}") is True


def test_zalo_signature_rejects_bad(configured_webhooks):
    assert verify_zalo_signature(b"{}", "1700000000", "mac=deadbeef") is False


def test_zalo_signature_rejects_missing(configured_webhooks):
    assert verify_zalo_signature(b"{}", "1700000000", None) is False


def test_meta_handshake_accepts_correct_token(configured_webhooks):
    assert verify_meta_webhook("subscribe", "verify-me", "challenge-123") == "challenge-123"


def test_meta_handshake_rejects_wrong_token(configured_webhooks):
    assert verify_meta_webhook("subscribe", "nope", "challenge-123") is None


# --- HTTP level ------------------------------------------------------------


async def test_messenger_webhook_rejects_unsigned(client, configured_webhooks):
    resp = await client.post(
        "/api/v1/webhooks/messenger",
        content=json.dumps({"entry": []}).encode(),
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 401


async def test_messenger_webhook_rejects_bad_signature(client, configured_webhooks):
    body = json.dumps({"entry": []}).encode()
    resp = await client.post(
        "/api/v1/webhooks/messenger",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": "sha256=" + "0" * 64,
        },
    )
    assert resp.status_code == 401


async def test_messenger_webhook_accepts_signed_empty_payload(
    client, configured_webhooks, db_session
):
    body = json.dumps({"entry": []}).encode()
    resp = await client.post(
        "/api/v1/webhooks/messenger",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": _meta_sig(body),
        },
    )
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


async def test_zalo_webhook_rejects_unsigned(client, configured_webhooks):
    resp = await client.post(
        "/api/v1/webhooks/zalo",
        content=b"{}",
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 401


async def test_messenger_verification_handshake(client, configured_webhooks):
    resp = await client.get(
        "/api/v1/webhooks/messenger",
        params={"hub.mode": "subscribe", "hub.verify_token": "verify-me", "hub.challenge": "abc"},
    )
    assert resp.status_code == 200
    assert resp.text == "abc"


async def test_messenger_verification_rejects_bad_token(client, configured_webhooks):
    resp = await client.get(
        "/api/v1/webhooks/messenger",
        params={"hub.mode": "subscribe", "hub.verify_token": "bad", "hub.challenge": "abc"},
    )
    assert resp.status_code == 403


async def test_signed_message_creates_conversation(client, configured_webhooks, db_session):
    """A properly signed message reaches the agent and persists a conversation.

    The LLM gateway comes from the `client` fixture's ASGI dependency override,
    so this stays offline and deterministic.
    """
    body = json.dumps(
        {
            "entry": [
                {"messaging": [{"sender": {"id": "psid-123"}, "message": {"text": "Xin chao"}}]}
            ]
        }
    ).encode()

    resp = await client.post(
        "/api/v1/webhooks/messenger",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": _meta_sig(body),
        },
    )
    assert resp.status_code == 200

    convo = await db_session.scalar(
        select(Conversation).where(Conversation.external_id == "psid-123")
    )
    assert convo is not None
