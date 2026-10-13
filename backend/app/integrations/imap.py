"""Inbound email integration: IMAP fetching and RFC822 parsing.

Mirrors :mod:`app.integrations.email` in spirit: when ``IMAP_HOST`` is unset the
client is a safe no-op that returns an empty batch instead of raising, so the
app — and the test suite — run without a mailbox.

The stdlib ``imaplib`` client is blocking, so every network call is pushed onto
a worker thread with :func:`asyncio.to_thread`. Parsing is completely
defensive: one malformed message is logged and skipped, never fatal.
"""

from __future__ import annotations

import asyncio
import contextlib
import email
import imaplib
import re
from dataclasses import dataclass, field
from email.header import decode_header, make_header
from email.message import Message
from email.utils import parseaddr, parsedate_to_datetime
from typing import Any

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

#: Bodies and subjects are truncated so a single huge message cannot bloat the DB.
MAX_BODY_CHARS = 20_000
#: Attachment payloads are read for the pipeline but never persisted wholesale.
MAX_ATTACHMENT_BYTES = 5_000_000

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_HTML_SCRIPT_RE = re.compile(r"(?is)<(script|style).*?</\1>")
_WHITESPACE_RE = re.compile(r"[ \t\r\f\v]+")


@dataclass
class Attachment:
    """Metadata plus raw bytes for one MIME part with a filename."""

    filename: str
    content_type: str
    size: int
    data: bytes = b""

    def meta(self) -> dict[str, Any]:
        """JSON-safe view (raw bytes are never persisted)."""
        return {
            "filename": self.filename,
            "content_type": self.content_type,
            "size": self.size,
        }


@dataclass
class ParsedEmail:
    """A normalised inbound message."""

    message_id: str
    subject: str
    from_address: str
    to_address: str
    date: str | None = None
    body_text: str = ""
    body_html: str = ""
    attachments: list[Attachment] = field(default_factory=list)
    headers: dict[str, str] = field(default_factory=dict)
    size_bytes: int = 0
    #: Set when the mailbox did not supply a Message-ID (we mint a fallback).
    message_id_inferred: bool = False

    def meta(self) -> dict[str, Any]:
        """JSON-safe metadata stored on the ``InboundEmail`` row."""
        return {
            "date": self.date,
            "size_bytes": self.size_bytes,
            "message_id_inferred": self.message_id_inferred,
            "attachments": [a.meta() for a in self.attachments],
            "headers": self.headers,
        }


def decode_mime_header(value: str | None) -> str:
    """Decode an RFC2047 encoded-word header ("=?UTF-8?B?...?=") to text.

    Falls back to the raw value when the header is too broken to decode —
    a mangled subject must never stop a message from being processed.
    """
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value))).strip()
    except Exception:  # noqa: BLE001 - defensive: headers come from the wild
        return value.strip()


def html_to_text(html: str) -> str:
    """Crude but dependency-free HTML-to-text used when no text/plain part exists."""
    without_scripts = _HTML_SCRIPT_RE.sub(" ", html)
    text = _HTML_TAG_RE.sub(" ", without_scripts)
    text = text.replace("&nbsp;", " ").replace("&amp;", "&")
    text = text.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"')
    lines = [_WHITESPACE_RE.sub(" ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line).strip()


def _decode_part(part: Message) -> str:
    """Decode a MIME part body to text, tolerating unknown charsets."""
    payload = part.get_payload(decode=True)
    # The stubs type this as `bytes | None`, but for a multipart container it
    # can hand back a nested `Message`; only text parts are decodable here.
    if not isinstance(payload, bytes):
        raw = part.get_payload()
        return raw if isinstance(raw, str) else ""
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except LookupError:
        return payload.decode("utf-8", errors="replace")


def _decode_attachment(part: Message) -> bytes:
    """Return a MIME part's decoded bytes, never ``None``.

    ``get_payload(decode=True)`` is typed ``bytes | None`` and a container part
    does report ``None``, so normalise that to empty bytes; the caller then
    slices a real ``bytes`` object. (Only text parts reach here in practice --
    ``_is_attachment`` requires a filename, which containers do not carry.)
    """
    payload = part.get_payload(decode=True)
    return payload if isinstance(payload, bytes) else b""


def _is_attachment(part: Message) -> bool:
    if part.get_filename():
        return True
    disposition = (part.get_content_disposition() or "").lower()
    return disposition == "attachment"


def _first_address(value: str | None) -> str:
    """Return the bare address from a header, e.g. ``An <a@b.vn>`` -> ``a@b.vn``."""
    if not value:
        return ""
    _, addr = parseaddr(value)
    return (addr or value).strip()


def _infer_message_id(*, from_address: str, subject: str, date: str | None) -> str:
    """Deterministic fallback id so a poll can still de-duplicate the message."""
    import hashlib

    seed = f"{from_address}|{subject}|{date or ''}".encode()
    return f"<generated-{hashlib.sha256(seed).hexdigest()[:32]}@airdesk.local>"


def parse_message(raw: bytes) -> ParsedEmail:
    """Parse an RFC822 message into a :class:`ParsedEmail`.

    Attachment metadata is always extracted; raw attachment bytes are kept in
    memory (bounded by ``MAX_ATTACHMENT_BYTES``) so a caller can persist them
    elsewhere without re-fetching the message.
    """
    msg = email.message_from_bytes(raw)

    subject = decode_mime_header(msg.get("Subject"))
    from_address = _first_address(msg.get("From"))
    to_address = _first_address(msg.get("To")) or decode_mime_header(msg.get("To"))
    date_raw = msg.get("Date")
    date_iso: str | None = None
    if date_raw:
        try:
            date_iso = parsedate_to_datetime(date_raw).isoformat()
        except Exception:  # noqa: BLE001 - malformed Date: headers are common
            date_iso = None

    text_chunks: list[str] = []
    html_chunks: list[str] = []
    attachments: list[Attachment] = []

    for part in msg.walk():
        if part.get_content_maintype() == "multipart":
            continue
        content_type = part.get_content_type()
        if _is_attachment(part):
            filename = decode_mime_header(part.get_filename()) or "unnamed"
            payload = _decode_attachment(part)
            attachments.append(
                Attachment(
                    filename=filename,
                    content_type=content_type,
                    size=len(payload),
                    data=payload[:MAX_ATTACHMENT_BYTES],
                )
            )
        elif content_type == "text/plain":
            text_chunks.append(_decode_part(part))
        elif content_type == "text/html":
            html_chunks.append(_decode_part(part))

    body_html = "\n".join(html_chunks).strip()
    body_text = "\n".join(chunk.strip() for chunk in text_chunks if chunk.strip()).strip()
    if not body_text and body_html:
        body_text = html_to_text(body_html)

    message_id = (msg.get("Message-ID") or "").strip()
    inferred = not message_id
    if inferred:
        message_id = _infer_message_id(from_address=from_address, subject=subject, date=date_iso)

    headers = {
        key.lower(): decode_mime_header(value)
        for key, value in msg.items()
        if key.lower() in {"in-reply-to", "references", "reply-to", "cc", "return-path"}
    }

    return ParsedEmail(
        message_id=message_id[:255],
        subject=subject[:500],
        from_address=from_address[:320],
        to_address=to_address[:320],
        date=date_iso,
        body_text=body_text[:MAX_BODY_CHARS],
        body_html=body_html[:MAX_BODY_CHARS],
        attachments=attachments,
        headers=headers,
        size_bytes=len(raw),
        message_id_inferred=inferred,
    )


class ImapClient:
    """Minimal asynchronous IMAP reader for a single mailbox."""

    def __init__(self) -> None:
        self._settings = get_settings()

    @property
    def configured(self) -> bool:
        """True when a host is set; otherwise every call is a no-op."""
        return bool(self._settings.imap_host)

    # --- blocking internals (run via asyncio.to_thread) --------------------
    def _connect(self) -> imaplib.IMAP4:
        settings = self._settings
        host = settings.imap_host or ""
        if settings.imap_port == 993:
            conn: imaplib.IMAP4 = imaplib.IMAP4_SSL(host, settings.imap_port, timeout=30)
        else:
            conn = imaplib.IMAP4(host, settings.imap_port, timeout=30)
        if settings.imap_user and settings.imap_password:
            conn.login(settings.imap_user, settings.imap_password)
        return conn

    def _fetch_sync(self, limit: int) -> list[bytes]:
        """Fetch up to ``limit`` UNSEEN messages as raw RFC822 bytes."""
        settings = self._settings
        conn = self._connect()
        raw_messages: list[bytes] = []
        try:
            conn.select(settings.imap_mailbox, readonly=False)
            status, data = conn.search(None, "UNSEEN")
            if status != "OK" or not data or not data[0]:
                return []
            uids = data[0].split()[-limit:]
            for uid in uids:
                try:
                    fetched = conn.fetch(uid, "(RFC822)")
                except Exception as exc:  # noqa: BLE001 - skip a single bad message
                    logger.warning("imap_fetch_message_failed", uid=uid.decode(), error=str(exc))
                    continue
                if fetched[0] != "OK" or not fetched[1]:
                    continue
                for item in fetched[1]:
                    if isinstance(item, tuple) and len(item) > 1 and isinstance(item[1], bytes):
                        raw_messages.append(item[1])
        finally:
            with contextlib.suppress(Exception):
                conn.logout()
        return raw_messages

    # --- async public API --------------------------------------------------
    async def fetch_unseen(self, limit: int | None = None) -> list[ParsedEmail]:
        """Return parsed UNSEEN messages; an empty list when unconfigured."""
        if not self.configured:
            logger.info("imap_fetch_skipped_not_configured", mailbox=self._settings.imap_mailbox)
            return []
        max_messages = limit or self._settings.imap_max_messages
        raw_messages = await asyncio.to_thread(self._fetch_sync, max_messages)

        parsed: list[ParsedEmail] = []
        for raw in raw_messages:
            try:
                parsed.append(parse_message(raw))
            except Exception as exc:  # noqa: BLE001 - never let one message break the poll
                logger.warning("imap_parse_failed", error=str(exc), size=len(raw))
        logger.info("imap_fetched", count=len(parsed), mailbox=self._settings.imap_mailbox)
        return parsed


_client: ImapClient | None = None


def get_imap_client() -> ImapClient:
    """Return the process-wide IMAP client."""
    global _client
    if _client is None:
        _client = ImapClient()
    return _client
