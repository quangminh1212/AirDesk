"""Webhook endpoints for Zalo OA and Facebook Messenger.

Both providers sign their payloads. Every inbound request is verified before it
reaches the agent: an unsigned or badly-signed body is rejected with 401. This
matters because these endpoints are public on the internet — without the check
anyone could drive the agent and burn LLM budget.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response, status
from sqlalchemy import select

from app.agents.runner import FlightAgent
from app.api.deps import DbSession, Gateway
from app.core.config import get_settings
from app.core.logging import get_logger
from app.integrations.messaging import (
    MessengerClient,
    ZaloClient,
    verify_meta_signature,
    verify_meta_webhook,
    verify_zalo_signature,
)
from app.models.agent import Channel, Conversation

router = APIRouter(prefix="/webhooks", tags=["webhooks"])
logger = get_logger(__name__)


async def _find_or_create_conversation(
    db: DbSession, *, channel: Channel, external_id: str
) -> Conversation:
    convo = await db.scalar(
        select(Conversation).where(
            Conversation.channel == channel, Conversation.external_id == external_id
        )
    )
    if convo is None:
        convo = Conversation(channel=channel, external_id=external_id, customer_ref=external_id)
        db.add(convo)
        await db.commit()
        await db.refresh(convo)
    return convo


def _signature_ok(*, provider: str, raw_body: bytes, request: Request) -> bool:
    """Validate a provider signature, honouring the require-signature setting."""
    if not get_settings().webhook_require_signature:
        logger.warning("webhook_signature_not_required", provider=provider)
        return True

    if provider == "messenger":
        return verify_meta_signature(raw_body, request.headers.get("x-hub-signature-256"))

    return verify_zalo_signature(
        raw_body,
        request.headers.get("x-zevent-timestamp") or request.headers.get("x-zalo-timestamp"),
        request.headers.get("x-zevent-signature") or request.headers.get("x-zalo-signature"),
    )


@router.get("/messenger", summary="Facebook Messenger webhook verification")
async def verify_messenger(request: Request) -> Response:
    params = request.query_params
    challenge = verify_meta_webhook(
        params.get("hub.mode", ""),
        params.get("hub.verify_token", ""),
        params.get("hub.challenge", ""),
    )
    if challenge is None:
        return Response(status_code=403)
    return Response(content=challenge, media_type="text/plain")


@router.post("/messenger", summary="Facebook Messenger inbound messages")
async def messenger_webhook(db: DbSession, gateway: Gateway, request: Request) -> dict[str, str]:
    raw_body = await request.body()
    if not _signature_ok(provider="messenger", raw_body=raw_body, request=request):
        logger.warning("messenger_webhook_rejected", reason="bad_signature")
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid webhook signature.")

    body = await request.json()
    client = MessengerClient()
    agent = FlightAgent(db, gateway)

    for entry in body.get("entry", []):
        for event in entry.get("messaging", []):
            sender = event.get("sender", {}).get("id")
            text = (event.get("message") or {}).get("text")
            if not sender or not text:
                continue
            await _handle_inbound(
                db=db,
                agent=agent,
                client=client,
                channel=Channel.MESSENGER,
                external_id=sender,
                text=text,
                channel_name="messenger",
            )
    return {"status": "ok"}


@router.post("/zalo", summary="Zalo OA inbound messages")
async def zalo_webhook(db: DbSession, gateway: Gateway, request: Request) -> dict[str, str]:
    raw_body = await request.body()
    if not _signature_ok(provider="zalo", raw_body=raw_body, request=request):
        logger.warning("zalo_webhook_rejected", reason="bad_signature")
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid webhook signature.")

    body = await request.json()
    client = ZaloClient()
    agent = FlightAgent(db, gateway)

    sender = (body.get("sender") or {}).get("id") or body.get("user_id_by_app")
    text = (body.get("message") or {}).get("text")
    if sender and text:
        await _handle_inbound(
            db=db,
            agent=agent,
            client=client,
            channel=Channel.ZALO,
            external_id=sender,
            text=text,
            channel_name="zalo",
        )
    return {"status": "ok"}


async def _handle_inbound(
    *,
    db: DbSession,
    agent: FlightAgent,
    client: object,
    channel: Channel,
    external_id: str,
    text: str,
    channel_name: str,
) -> None:
    """Route one inbound message to the agent and deliver the reply.

    Failures are logged and swallowed: a platform retry must never turn into a
    500 for the provider, and one bad message must not block the rest.
    """
    convo = await _find_or_create_conversation(db, channel=channel, external_id=external_id)
    try:
        reply = await agent.handle(
            user_message=text, conversation_id=convo.id, channel=channel_name
        )
    except Exception as exc:  # noqa: BLE001 - provider must still receive 200
        logger.error(
            "webhook_agent_failed", channel=channel_name, external_id=external_id, error=str(exc)
        )
        return

    send = getattr(client, "send_text", None)
    if send is None:
        return
    try:
        await send(external_id, reply.reply)
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "webhook_send_failed", channel=channel_name, external_id=external_id, error=str(exc)
        )
