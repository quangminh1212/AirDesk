"""Inbound email ingest: classify, persist, route and reply.

The pipeline is intentionally conservative, because mail is the channel where a
wrong automatic answer is most visible to a customer:

1. **Classify** every message with a *rule-based* classifier first. It is
   deterministic, free, auditable and works offline; an LLM classifier can be
   layered on top later without changing any caller.
2. **Persist** one ``InboundEmail`` row per RFC5322 Message-ID. A repeated poll
   finds the existing row and skips it, so IMAP ``UNSEEN`` glitches cannot
   duplicate work.
3. **Route** by intent. Customer requests (booking, change, refund, invoice,
   complaint, general enquiry) reuse or create an ``EMAIL`` conversation and are
   handed to the existing :class:`~app.agents.runner.FlightAgent`, whose reply
   is sent back with ``In-Reply-To``/``References`` so mail clients thread it.
4. **Degrade, never lose.** No auto-reply, no LLM or no SMTP does not fail the
   message: the row is still stored and classified. Only an actual agent or
   send *error* marks the row ``failed`` with the reason, for a human to pick up.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.runner import FlightAgent
from app.core.config import get_settings
from app.core.llm import LiteLLMGateway
from app.core.logging import get_logger
from app.integrations.email import EmailClient
from app.integrations.imap import ImapClient, ParsedEmail, get_imap_client
from app.models.agent import Channel, Conversation, ConversationStatus
from app.models.email import EmailIntent, InboundEmail, InboundEmailStatus
from app.schemas.inbox import PollSummary

logger = get_logger(__name__)

#: Intents that warrant an automatic agent reply.
CUSTOMER_INTENTS: frozenset[EmailIntent] = frozenset(
    {
        EmailIntent.FLIGHT_BOOKING,
        EmailIntent.BOOKING_CHANGE,
        EmailIntent.REFUND_REQUEST,
        EmailIntent.INVOICE_OR_DOCUMENT,
        EmailIntent.COMPLAINT,
        EmailIntent.GENERAL_INQUIRY,
    }
)

#: Intents stored but never answered automatically.
SILENT_INTENTS: frozenset[EmailIntent] = frozenset({EmailIntent.SPAM, EmailIntent.UNKNOWN})

# ---------------------------------------------------------------------------
# Rule-based classifier
# ---------------------------------------------------------------------------

# Vietnamese is matched both with and without diacritics, because customers
# routinely type unaccented ("huy ve", "hoan tien") and mail clients mangle
# encodings. Keep every keyword folded (lowercase, no accents, single spaces).
_INTENT_KEYWORDS: dict[EmailIntent, tuple[str, ...]] = {
    EmailIntent.FLIGHT_BOOKING: (
        "dat ve",
        "dat cho",
        "book ve",
        "mua ve",
        "giu cho",
        "can ve",
        "tim ve",
        "ve may bay",
        "chuyen bay",
        "khu hoi",
        "mot chieu",
        "book flight",
        "book a flight",
        "book ticket",
        "flight booking",
        "flight ticket",
        "one way",
        "round trip",
        "one-way",
        "two passengers",
        "how much is a flight",
        "need a flight",
        "want to fly",
        "reserve a seat",
    ),
    EmailIntent.BOOKING_CHANGE: (
        "doi ve",
        "doi ngay",
        "doi chuyen",
        "doi gio bay",
        "thay doi lich",
        "thay doi chuyen bay",
        "hoan doi",
        "doi ten hanh khach",
        "sua ve",
        "sua thong tin",
        "doi hanh trinh",
        "change flight",
        "change my flight",
        "change booking",
        "change my booking",
        "change date",
        "reschedule",
        "move my flight",
        "amend booking",
        "modify booking",
        "rebook",
        "name correction",
    ),
    EmailIntent.REFUND_REQUEST: (
        "hoan tien",
        "hoan ve",
        "xin hoan",
        "tra lai tien",
        "huy ve",
        "huy chuyen",
        "huy dat cho",
        "yeu cau hoan",
        "refund",
        "refund request",
        "request a refund",
        "money back",
        "cancel my flight",
        "cancel my booking",
        "cancel the booking",
        "cancellation",
    ),
    EmailIntent.INVOICE_OR_DOCUMENT: (
        "hoa don",
        "hoadon",
        "xuat hoa don",
        "chung tu",
        "bien lai",
        "gui hoa don",
        "vat",
        "invoice",
        "receipt",
        "tax invoice",
        "issue an invoice",
        "billing document",
        "e-ticket",
        "eticket",
        "itinerary receipt",
        "attach the invoice",
    ),
    EmailIntent.COMPLAINT: (
        "khieu nai",
        "phan nan",
        "phan hoi tieu cuc",
        "cham tre",
        "tre chuyen",
        "thai do",
        "khong hai long",
        "buc xuc",
        "complaint",
        "complain",
        "unacceptable",
        "rude",
        "never again",
        "terrible service",
        "escalate this",
        "poor service",
        "i demand",
    ),
    EmailIntent.GENERAL_INQUIRY: (
        "hoi",
        "tu van",
        "cho hoi",
        "xin hoi",
        "muon biet",
        "thong tin",
        "gia ve",
        "bang gia",
        "co chuyen",
        "enquiry",
        "inquiry",
        "question",
        "could you tell me",
        "i would like to know",
        "information about",
        "do you have",
        "can you help",
        "any flights",
        "what time",
        "baggage allowance",
        "visa",
        "price list",
    ),
    EmailIntent.SPAM: (
        "unsubscribe",
        "click here",
        "limited time offer",
        "crypto",
        "bitcoin",
        "loan offer",
        "work from home",
        "nigerian prince",
        "increase your sales",
        "seo services",
        "backlink",
        "quang cao",
        "khuyen mai dac biet",
        "kiem tien online",
        "vay von nhanh",
        "tang luot truy cap",
        "no-reply",
        "noreply",
        "do not reply",
    ),
}

#: Strong "this is marketing/automated mail" signals.
_SPAM_TECHNICAL_HINTS: tuple[str, ...] = (
    "bulk",
    "precedence: bulk",
    "auto-submitted",
    "list-unsubscribe",
)

#: Signals of a real business/bulk send rather than spam (no-reply alone is weak).
_SPAM_EXEMPT_HINTS: tuple[str, ...] = (
    "invoice",
    "hoa don",
    "receipt",
    "e-ticket",
    "eticket",
    "itinerary",
    "booking confirmation",
)

#: Order matters: the first matching intent wins a score tie.
_INTENT_ORDER: tuple[EmailIntent, ...] = (
    EmailIntent.SPAM,
    EmailIntent.REFUND_REQUEST,
    EmailIntent.BOOKING_CHANGE,
    EmailIntent.COMPLAINT,
    EmailIntent.INVOICE_OR_DOCUMENT,
    EmailIntent.FLIGHT_BOOKING,
    EmailIntent.GENERAL_INQUIRY,
)

#: Above this character length a single hit is still treated as decisive.
_SHORT_TEXT_CHARS = 40

_PUNCT_RE = re.compile(r"[^\w\s]+")
_SPACE_RE = re.compile(r"\s+")


def _fold(text: str) -> str:
    """Lowercase and strip diacritics/spacing so keyword matching is robust.

    ``"Đổi vé"`` -> ``"doi ve"``, ``"HỦY  VÉ"`` -> ``"huy ve"``.
    """
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    # Vietnamese "đ" does not decompose under NFKD.
    stripped = stripped.replace("đ", "d").replace("Đ", "D")
    # Keep the hyphen: "one-way" and "e-ticket" are legitimate keywords.
    stripped = _PUNCT_RE.sub(" ", stripped.replace("-", " - "))
    return _SPACE_RE.sub(" ", stripped).strip().casefold()


def _matches(haystack: str, needle: str) -> bool:
    """Word-boundary aware keyword match on the folded text."""
    return re.search(rf"(?<!\w){re.escape(needle)}(?!\w)", haystack) is not None


@dataclass
class IntentVerdict:
    """Classifier output, including the evidence so a reviewer can audit it."""

    intent: EmailIntent
    confidence: float
    scores: dict[str, int] = field(default_factory=dict)
    matched: list[str] = field(default_factory=list)


def classify_email(
    subject: str = "", body: str = "", *, headers: dict[str, str] | None = None
) -> IntentVerdict:
    """Classify an inbound message with deterministic Vietnamese/English rules.

    Keywords found in the subject are weighted twice as heavily as keywords in
    the body, which mirrors how people write business mail. Returns
    ``EmailIntent.UNKNOWN`` when nothing at all matches.
    """
    headers = {k.lower(): v for k, v in (headers or {}).items()}
    subject_folded = _fold(subject)
    body_folded = _fold(body)
    haystack = f"{subject_folded} \n {body_folded}".strip()

    technical = " ".join(
        [subject_folded, body_folded] + [f"{k}:{v}".lower() for k, v in headers.items()]
    )
    spam_evidence = {
        "bulk": _matches(technical, "bulk") or "precedence bulk" in technical,
        "auto_submitted": "auto-submitted" in technical,
        "list_unsubscribe": "list-unsubscribe" in technical,
    }

    scores: dict[EmailIntent, int] = {}
    matched: list[str] = []
    for intent, keywords in _INTENT_KEYWORDS.items():
        score = 0
        for keyword in keywords:
            in_subject = _matches(subject_folded, keyword)
            in_body = _matches(body_folded, keyword)
            if not (in_subject or in_body):
                continue
            score += 2 if in_subject else 1
            matched.append(keyword)
        scores[intent] = score

    # Hard spam override: automated/marketing headers are unambiguous.
    if spam_evidence["auto_submitted"] or spam_evidence["list_unsubscribe"]:
        exempt = any(_matches(haystack, hint) for hint in _SPAM_EXEMPT_HINTS)
        if not exempt:
            scores[EmailIntent.SPAM] = max(scores[EmailIntent.SPAM], 3)
            matched.extend(k for k, v in spam_evidence.items() if v)
    # A mail sent only to a no-reply address is almost always bulk.
    no_reply_recipient = str(headers.get("to", "")).casefold()
    if "no-reply" in no_reply_recipient or "noreply" in no_reply_recipient:
        scores[EmailIntent.SPAM] = max(scores[EmailIntent.SPAM], 1)

    best: EmailIntent | None = None
    best_score = 0
    for intent in _INTENT_ORDER:
        score = scores.get(intent, 0)
        if score > best_score:
            best, best_score = intent, score

    if best is None or best_score == 0:
        return IntentVerdict(intent=EmailIntent.UNKNOWN, confidence=0.2, scores=_as_str_int(scores))

    total = sum(scores.values()) or best_score
    margin = best_score / total
    text_len = len(body_folded) + len(subject_folded)
    # A lone keyword in a long message is weaker evidence than in a one-liner.
    length_factor = 1.0 if text_len <= _SHORT_TEXT_CHARS else 0.85
    confidence = min(0.95, 0.45 + 0.3 * margin + 0.1 * min(best_score, 3) / 3)
    confidence = round(confidence * (1.0 if best_score >= 2 else 0.9) * length_factor, 3)
    return IntentVerdict(
        intent=best,
        confidence=confidence,
        scores=_as_str_int(scores),
        matched=sorted(set(matched)),
    )


def _as_str_int(scores: dict[EmailIntent, int]) -> dict[str, int]:
    return {intent.value: score for intent, score in scores.items() if score}


# ---------------------------------------------------------------------------
# Ingest service
# ---------------------------------------------------------------------------


def _reply_subject(subject: str) -> str:
    """Prefix ``Re:`` exactly once (mail clients add more themselves)."""
    cleaned = (subject or "").strip()
    if not cleaned:
        return "Re: your message to AirDesk"
    if re.match(r"(?i)^\s*(re|fwd|fw)\s*:", cleaned):
        return cleaned
    return f"Re: {cleaned}"


class EmailIngestService:
    """Turns raw mailbox messages into stored, classified, answered rows."""

    def __init__(
        self,
        db: AsyncSession,
        *,
        gateway: LiteLLMGateway | None = None,
        imap: ImapClient | None = None,
        mailer: EmailClient | None = None,
    ) -> None:
        self._db = db
        self._settings = get_settings()
        self._gateway = gateway
        self._imap = imap
        self._mailer = mailer

    # --- dependencies are resolved lazily so the service stays testable ----
    @property
    def imap(self) -> ImapClient:
        return self._imap or get_imap_client()

    @property
    def gateway(self) -> LiteLLMGateway:
        if self._gateway is None:
            from app.core.llm import get_llm_gateway

            self._gateway = get_llm_gateway()
        return self._gateway

    @property
    def mailer(self) -> EmailClient:
        if self._mailer is None:
            from app.integrations.email import get_email_client

            self._mailer = get_email_client()
        return self._mailer

    # --- public API --------------------------------------------------------
    async def poll_and_ingest(self, *, limit: int | None = None) -> PollSummary:
        """Run one IMAP poll plus ingest cycle and summarise the outcome."""
        summary = PollSummary(configured=self.imap.configured)
        if not self.imap.configured:
            logger.info("email_poll_skipped_not_configured")
        try:
            messages = await self.imap.fetch_unseen(limit)
        except Exception as exc:  # noqa: BLE001 - a mailbox outage must not 500 the API
            logger.error("email_poll_failed", error=str(exc))
            summary.errors.append(f"imap: {exc}")
            return summary

        summary.fetched = len(messages)
        for message in messages:
            try:
                row = await self.ingest(message)
            except Exception as exc:  # noqa: BLE001 - one bad message, not the batch
                logger.exception("email_ingest_failed", message_id=message.message_id)
                summary.failed += 1
                summary.errors.append(f"{message.message_id}: {exc}")
                continue
            if row is None:
                summary.skipped += 1
            elif row.status is InboundEmailStatus.FAILED:
                summary.failed += 1
                summary.errors.append(f"{row.message_id}: {row.error}")
            elif row.status is InboundEmailStatus.IGNORED:
                summary.ignored += 1
            else:
                summary.processed += 1

        logger.info(
            "email_poll_complete",
            fetched=summary.fetched,
            processed=summary.processed,
            failed=summary.failed,
            skipped=summary.skipped,
            ignored=summary.ignored,
        )
        return summary

    async def ingest(self, message: ParsedEmail) -> InboundEmail | None:
        """Store and route one parsed message; ``None`` if it was a duplicate."""
        existing = await self._db.scalar(
            select(InboundEmail).where(InboundEmail.message_id == message.message_id)
        )
        if existing is not None:
            logger.info("email_duplicate_skipped", message_id=message.message_id)
            return None

        verdict = classify_email(message.subject, message.body_text, headers=message.headers)
        row = InboundEmail(
            message_id=message.message_id,
            from_address=message.from_address,
            to_address=message.to_address,
            subject=message.subject,
            body_text=message.body_text,
            intent=verdict.intent,
            status=InboundEmailStatus.RECEIVED,
            raw_meta={
                **message.meta(),
                "classification": {
                    "confidence": verdict.confidence,
                    "scores": verdict.scores,
                    "matched": verdict.matched,
                },
            },
        )
        self._db.add(row)
        await self._db.flush()

        if verdict.intent in SILENT_INTENTS:
            row.status = (
                InboundEmailStatus.IGNORED
                if verdict.intent is EmailIntent.SPAM
                else InboundEmailStatus.RECEIVED
            )
            row.processed_at = datetime.now(UTC)
            if self._settings.email_auto_reply_enabled and verdict.intent is EmailIntent.SPAM:
                row.raw_meta = {**(row.raw_meta or {}), "auto_reply": "suppressed_spam"}
            await self._db.commit()
            await self._db.refresh(row)
            return row

        row.status = InboundEmailStatus.PROCESSING
        await self._db.flush()
        try:
            reply_text = await self._run_agent(row, message)
        except Exception as exc:  # noqa: BLE001 - record and hand to a human
            logger.exception("email_agent_failed", message_id=row.message_id)
            await self._db.rollback()
            await self._mark_failed(row, f"agent: {exc}")
            return row

        row.status = InboundEmailStatus.PROCESSED
        row.processed_at = datetime.now(UTC)
        meta = dict(row.raw_meta or {})
        meta["auto_reply"] = reply_text
        if not self._settings.email_auto_reply_enabled:
            meta["reply_suppressed"] = "email_auto_reply_enabled=false"
            row.raw_meta = meta
            await self._db.commit()
            await self._db.refresh(row)
            return row

        try:
            sent = await self.mailer.send(
                to=message.from_address,
                subject=_reply_subject(message.subject),
                body=reply_text,
                in_reply_to=_thread_id(message.message_id),
                references=_thread_references(message),
            )
        except Exception as exc:  # noqa: BLE001 - the reply content is still valuable
            logger.error("email_send_failed", message_id=row.message_id, error=str(exc))
            meta["send_error"] = str(exc)
            row.raw_meta = meta
            await self._db.rollback()
            await self._mark_failed(row, f"send: {exc}")
            return row

        meta["reply_sent"] = sent
        if not sent:
            meta["reply_suppressed"] = "smtp_not_configured"
        row.raw_meta = meta
        await self._db.commit()
        await self._db.refresh(row)
        return row

    # --- internals ---------------------------------------------------------
    async def _run_agent(self, row: InboundEmail, message: ParsedEmail) -> str:
        """Reuse/create the EMAIL conversation and let the flight agent reply."""
        conversation = await self._find_or_create_conversation(row)
        row.conversation_id = conversation.id
        await self._db.flush()

        agent = FlightAgent(self._db, self.gateway)
        reply = await agent.handle(
            user_message=message.body_text or message.subject,
            conversation_id=conversation.id,
            channel=Channel.EMAIL.value,
            customer_ref=message.from_address,
        )
        return reply.reply

    async def _find_or_create_conversation(self, row: InboundEmail) -> Conversation:
        """Reuse the thread's conversation when it exists, else open a new one.

        Threading uses the ``In-Reply-To``/``References`` headers first, falling
        back to a per-sender open conversation so a follow-up email from the same
        customer continues the agent's memory of the case.
        """
        root_id = row.raw_meta and (row.raw_meta.get("headers") or {}).get("references")
        if root_id:
            first_reference = str(root_id).split()[0]
            referenced = await self._db.scalar(
                select(InboundEmail).where(InboundEmail.message_id == first_reference)
            )
            if referenced is not None and referenced.conversation_id:
                conversation = await self._db.get(Conversation, referenced.conversation_id)
                if conversation is not None:
                    return conversation

        convo = await self._db.scalar(
            select(Conversation)
            .where(
                Conversation.channel == Channel.EMAIL,
                Conversation.customer_ref == row.from_address,
                Conversation.status != ConversationStatus.CLOSED,
            )
            .order_by(Conversation.created_at.desc())
        )
        if convo is not None:
            return convo

        convo = Conversation(
            channel=Channel.EMAIL,
            external_id=row.from_address,
            customer_ref=row.from_address,
            status=ConversationStatus.OPEN,
            summary=row.subject[:500] or None,
        )
        self._db.add(convo)
        await self._db.flush()
        return convo

    async def _mark_failed(self, row: InboundEmail, error: str) -> None:
        """Persist a failure marker in a fresh transaction (rollback-safe)."""
        fresh = await self._db.get(InboundEmail, row.id)
        if fresh is None:  # pragma: no cover - only if the row was removed meanwhile
            return
        fresh.status = InboundEmailStatus.FAILED
        fresh.error = error[:4000]
        fresh.processed_at = datetime.now(UTC)
        await self._db.commit()
        await self._db.refresh(fresh)


def _thread_id(message_id: str) -> str:
    """Message-ID without angle brackets, as SMTP headers expect."""
    return message_id.strip().strip("<>")


def _thread_references(message: ParsedEmail) -> str | None:
    """Build the ``References`` chain so replies stay in the customer's thread."""
    chain: list[str] = []
    for header in ("references", "in-reply-to"):
        value = (message.headers or {}).get(header)
        if value:
            chain.extend(value.replace(",", " ").split())
    if message.message_id:
        chain.append(message.message_id)
    cleaned = [_thread_id(part) for part in chain if part]
    seen: list[str] = []
    for item in cleaned:
        if item and item not in seen and item not in message.message_id:
            seen.append(item)
    return " ".join(seen) or None


async def count_inbound(
    db: AsyncSession, *, status: InboundEmailStatus | None = None, intent: EmailIntent | None = None
) -> int:
    """Count stored messages, honouring the same filters as the list endpoint."""
    stmt = select(func.count()).select_from(InboundEmail)
    if status is not None:
        stmt = stmt.where(InboundEmail.status == status)
    if intent is not None:
        stmt = stmt.where(InboundEmail.intent == intent)
    return await db.scalar(stmt) or 0
