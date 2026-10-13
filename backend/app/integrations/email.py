"""Email integration: SMTP sending via stdlib, safe and dependency-free.

Used for booking confirmations, escalation notifications and reports. In
development (no SMTP configured) messages are logged instead of sent, so the
system never crashes for lack of a mail server.
"""

from __future__ import annotations

import asyncio
import smtplib
from email.message import EmailMessage

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class EmailClient:
    """Minimal, testable SMTP client."""

    def __init__(self) -> None:
        self._settings = get_settings()

    @property
    def configured(self) -> bool:
        return bool(self._settings.smtp_host)

    def _build(
        self,
        *,
        to: str,
        subject: str,
        body: str,
        html: bool = False,
        in_reply_to: str | None = None,
        references: str | None = None,
    ) -> EmailMessage:
        msg = EmailMessage()
        msg["From"] = self._settings.smtp_from
        msg["To"] = to
        msg["Subject"] = subject
        # Threading headers: without these the reply starts a new mail thread
        # instead of continuing the customer's, so they are always applied when
        # the inbound Message-ID is known.
        if in_reply_to:
            msg["In-Reply-To"] = in_reply_to
        if references:
            msg["References"] = references
        if html:
            msg.set_content(body, subtype="html")
        else:
            msg.set_content(body)
        return msg

    def _send_sync(self, msg: EmailMessage) -> None:
        s = self._settings
        with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=20) as server:  # type: ignore[arg-type]
            server.starttls()
            if s.smtp_user and s.smtp_password:
                server.login(s.smtp_user, s.smtp_password)
            server.send_message(msg)

    async def send(
        self,
        *,
        to: str,
        subject: str,
        body: str,
        html: bool = False,
        in_reply_to: str | None = None,
        references: str | None = None,
    ) -> bool:
        """Send an email; returns True if sent, False if only logged (no SMTP)."""
        if not self.configured:
            logger.info("email_skipped_not_configured", to=to, subject=subject)
            return False
        msg = self._build(
            to=to,
            subject=subject,
            body=body,
            html=html,
            in_reply_to=in_reply_to,
            references=references,
        )
        await asyncio.to_thread(self._send_sync, msg)
        logger.info("email_sent", to=to, subject=subject)
        return True


_client: EmailClient | None = None


def get_email_client() -> EmailClient:
    global _client
    if _client is None:
        _client = EmailClient()
    return _client
