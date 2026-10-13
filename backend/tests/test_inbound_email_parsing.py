"""Inbound email parsing (fully offline, real MIME bytes).

``parse_message`` is the boundary between a raw IMAP fetch and everything the
document/inbox pipeline sees afterwards, so a silent mis-decode here corrupts
subjects, bodies and attachment payloads downstream. These tests build genuine
MIME messages with the stdlib and parse them through the real code path.

The multipart cases matter most: a container part has no payload of its own, and
``get_payload(decode=True)`` can hand back a nested ``Message`` (or ``None``)
rather than bytes. The decoders must degrade to empty string / empty bytes
instead of raising or slicing garbage.
"""

from __future__ import annotations

from email.message import EmailMessage, Message

from app.integrations.imap import _decode_attachment, parse_message


def _plain_email() -> bytes:
    msg = EmailMessage()
    msg["Subject"] = "Xác nhận đặt vé SGN-HAN"
    msg["From"] = "Khach Hang <khach@example.com>"
    msg["To"] = "support@airdesk.vn"
    msg["Message-ID"] = "<abc123@example.com>"
    msg["Date"] = "Fri, 01 May 2026 08:00:00 +0700"
    msg.set_content("Chuyến bay của tôi đã được đặt chưa?")
    return msg.as_bytes()


def _multipart_email() -> bytes:
    msg = EmailMessage()
    msg["Subject"] = "Hoá đơn kèm tệp"
    msg["From"] = "Ke Toan <ketoan@example.com>"
    msg["To"] = "support@airdesk.vn"
    msg["Message-ID"] = "<multi456@example.com>"
    msg.set_content("Vui lòng xem tệp đính kèm.")
    msg.add_alternative("<p>Vui lòng xem <b>tệp</b> đính kèm.</p>", subtype="html")
    msg.add_attachment(
        b"%PDF-1.4 fake invoice bytes",
        maintype="application",
        subtype="pdf",
        filename="hoa-don.pdf",
    )
    return msg.as_bytes()


def test_plain_message_parses_header_and_body() -> None:
    parsed = parse_message(_plain_email())
    assert parsed.subject == "Xác nhận đặt vé SGN-HAN"
    assert "khach@example.com" in parsed.from_address
    assert parsed.to_address == "support@airdesk.vn"
    assert parsed.message_id == "<abc123@example.com>"
    assert parsed.message_id_inferred is False
    assert "đặt chưa" in parsed.body_text


def test_non_ascii_survives_the_round_trip() -> None:
    """Vietnamese diacritics must not be mangled by the header decoder."""
    parsed = parse_message(_plain_email())
    assert "đặt vé" in parsed.subject
    assert "Chuyến bay" in parsed.body_text


def test_multipart_message_exposes_html_and_attachment() -> None:
    parsed = parse_message(_multipart_email())
    assert parsed.body_html, "the html alternative must be captured"
    assert len(parsed.attachments) == 1
    attachment = parsed.attachments[0]
    assert attachment.filename == "hoa-don.pdf"
    assert attachment.content_type == "application/pdf"
    assert attachment.data.startswith(b"%PDF")
    assert attachment.size == len(attachment.data)


def test_attachment_meta_is_json_safe() -> None:
    """Raw bytes must never leak into the persisted metadata dict."""
    meta = parse_message(_multipart_email()).attachments[0].meta()
    assert meta == {
        "filename": "hoa-don.pdf",
        "content_type": "application/pdf",
        "size": len(b"%PDF-1.4 fake invoice bytes"),
    }


def test_message_without_message_id_gets_an_inferred_one() -> None:
    msg = EmailMessage()
    msg["Subject"] = "no id"
    msg["From"] = "a@example.com"
    msg["To"] = "b@example.com"
    msg.set_content("body")
    parsed = parse_message(msg.as_bytes())
    assert parsed.message_id, "a fallback id must always be present"
    assert parsed.message_id_inferred is True


def test_html_only_message_yields_text() -> None:
    """A message with no text/plain part must still produce body_text."""
    msg = EmailMessage()
    msg["Subject"] = "html only"
    msg["From"] = "a@example.com"
    msg["To"] = "b@example.com"
    msg["Message-ID"] = "<html789@example.com>"
    msg.set_content("<p>Chi co <b>HTML</b> thoi.</p>", subtype="html")
    parsed = parse_message(msg.as_bytes())
    assert "HTML" in parsed.body_text


def test_attachment_payload_is_bytes_for_every_attachment() -> None:
    """Attachment payloads are always ``bytes`` and the file survives intact."""
    parsed = parse_message(_multipart_email())
    assert parsed.attachments, "the pdf part must be surfaced as an attachment"
    for attachment in parsed.attachments:
        assert isinstance(attachment.data, bytes)
    # The document-number/idempotency checks downstream slice these bytes.
    assert parsed.attachments[0].data == b"%PDF-1.4 fake invoice bytes"


def test_decode_attachment_normalises_a_payload_less_part() -> None:
    """``_decode_attachment`` returns ``b""`` when a part has no payload bytes.

    The helper exists so callers can always slice ``attachment.data``, so the
    contract under test is "returns bytes, never None" -- not any particular
    implementation. It is asserted directly rather than through
    ``parse_message`` because ``_is_attachment`` filters on filename and MIME
    containers never carry one, so the parser cannot currently route a payload-
    less part here.
    """
    container = Message()
    container["Content-Type"] = "multipart/mixed"
    container.set_payload(["a", "b"])  # a container: no decodable bytes

    assert container.get_payload(decode=True) is None
    result = _decode_attachment(container)
    assert result == b""
    assert isinstance(result, bytes)


def test_size_bytes_reflects_the_raw_message() -> None:
    raw = _plain_email()
    assert parse_message(raw).size_bytes == len(raw)
