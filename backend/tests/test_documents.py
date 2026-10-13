"""Document extraction tests — fully offline, no network, no database.

The LLM boundary is exercised with :class:`StubGateway`, a fake that records
the calls it received and replays a scripted list of replies. That keeps every
assertion about *our* parsing/retry logic rather than about a provider.

The PDF fixture is built by hand: a well-formed (if minimal) PDF whose content
stream holds one ``BT /F1 12 Tf (HELLO PDF) Tj ET`` block, FlateDecode'd with
``zlib`` exactly like a real producer would. That is what makes the parser
reproducible to test — no ``pypdf``, no network, no binary blobs in the repo.
"""

from __future__ import annotations

import io
import json
import zipfile
import zlib

import pytest

from app.core.llm import LiteLLMGateway, LLMResult
from app.models.document import DocumentType
from app.services import documents
from app.services.documents import (
    ALLOWED_CONTENT_TYPES,
    MAX_TEXT_CHARS,
    ExtractedDocument,
    answer_about_document,
    extract_structured,
    extract_text,
    sha256_of,
)

DOCX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
PDF_CONTENT_TYPE = "application/pdf"


class StubGateway:
    """Scripted, network-free stand-in for :class:`LiteLLMGateway`."""

    def __init__(self, replies: list[str] | None = None) -> None:
        self._replies = list(replies or [])
        self.calls: list[dict] = []

    async def complete(self, **kwargs) -> LLMResult:
        self.calls.append(kwargs)
        if not self._replies:
            raise AssertionError("StubGateway ran out of scripted replies")
        # The last reply repeats, so a test that under-scripts still fails loudly
        # on content rather than on a missing fixture.
        reply = self._replies[0] if len(self._replies) == 1 else self._replies.pop(0)
        return LLMResult(content=reply, model="stub/model")


# ---------------------------------------------------------------------------
# Offline guarantee
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_real_llm_calls(monkeypatch):
    """Make any un-stubbed LLM call fail loudly instead of hitting a provider.

    This is a safety net, not decoration. ``extract_structured`` and
    ``answer_about_document`` take an *explicit* gateway argument — not a
    FastAPI ``Depends`` — so a test that forgets to pass one would silently
    fall through to the real ``LiteLLMGateway`` and make a live, billed HTTP
    request. Every test in this module injects its own stub; if one ever
    stops doing so, this fixture turns that into a fast, obvious failure.
    """

    def _forbidden(*args, **kwargs):
        raise AssertionError(
            "A test tried to build a real LLM gateway. Inject a stub gateway instead."
        )

    monkeypatch.setattr(LiteLLMGateway, "__init__", _forbidden)
    yield


# ---------------------------------------------------------------------------
# Builders for the in-memory fixtures
# ---------------------------------------------------------------------------


def _docx_bytes(*paragraphs: str) -> bytes:
    """Minimal valid .docx: a zip containing only ``word/document.xml``."""
    body = "".join(f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>" for text in paragraphs)
    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}</w:body></w:document>"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", document_xml)
    return buffer.getvalue()


def _pdf_bytes(content: bytes, *, compress: bool = True, trailing_eol: bool = True) -> bytes:
    """Tiny but structurally real PDF with a single (optionally Flate) stream.

    ``trailing_eol`` mimics the two stream layouts found in the wild: a CRLF
    after the body (most producers) and a bare ``endstream`` immediately after
    it (what a minifier emits). The parser must survive both.
    """
    if compress:
        stored = zlib.compress(content)
        filter_entry = " /Filter /FlateDecode"
    else:
        stored = content
        filter_entry = ""
    separator = b"\n" if trailing_eol else b""
    parts: list[bytes] = [b"%PDF-1.4\n"]
    offsets: list[int] = []
    for obj, body in enumerate(
        [
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
            b"<< /Length %d%s >>\nstream\n%s%sendstream\n"
            % (len(stored), filter_entry.encode(), stored, separator),
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        ],
        start=1,
    ):
        offsets.append(sum(len(p) for p in parts))
        parts.append(b"%d 0 obj\n%s\nendobj\n" % (obj, body))
    xref_offset = sum(len(p) for p in parts)
    parts.append(b"xref\n0 6\n0000000000 65535 f \n")
    for offset in offsets:
        parts.append(b"%010d 00000 n \n" % offset)
    parts.append(b"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % xref_offset)
    return b"".join(parts)


def _json_reply(**overrides) -> str:
    payload = {
        "doc_type": "airline_ticket",
        "confidence": 0.91,
        "summary": "One-way SGN-HAN e-ticket.",
        "reference_numbers": ["ABC123"],
        "amounts": [{"label": "total", "value": 1450000.0, "currency": "VND"}],
        "dates": ["2026-05-01"],
        "parties": ["Vietnam Airlines"],
        "passengers": [
            {
                "first_name": "Binh",
                "last_name": "Tran",
                "document_number": "C0123456",
                "date_of_birth": "1990-01-31",
            }
        ],
        "line_items": [
            {"description": "SGN-HAN", "quantity": 1, "unit_price": 1450000.0, "amount": 1450000.0}
        ],
    }
    payload.update(overrides)
    return json.dumps(payload)


# ---------------------------------------------------------------------------
# extract_text: plain text-like payloads
# ---------------------------------------------------------------------------


def test_extract_text_plain() -> None:
    result = extract_text("note.txt", "text/plain", b"Hello AirDesk")
    assert result.error is None
    assert result.text == "Hello AirDesk"
    assert result.needs_ocr is False


def test_extract_text_csv() -> None:
    payload = b"origin,destination,fare\nSGN,HAN,1450000\n"
    result = extract_text("fares.csv", "text/csv", payload)
    assert result.error is None
    assert "SGN,HAN,1450000" in result.text
    assert result.needs_ocr is False


def test_extract_text_json() -> None:
    result = extract_text("doc.json", "application/json", b'{"pnr": "ABC123"}')
    assert "ABC123" in result.text


def test_extract_text_plain_tolerates_bad_utf8() -> None:
    """Malformed bytes are replaced, not raised."""
    result = extract_text("broken.txt", "text/plain", b"ok \xff\xfe end")
    assert result.error is None
    assert "ok" in result.text and "end" in result.text


def test_extract_text_unsupported_content_type_reports_error() -> None:
    result = extract_text("photo.png", "image/png", b"\x89PNG\r\n\x1a\nnot really a pdf")
    assert result.error is not None
    assert "image/png" in result.error
    assert result.text == ""
    assert result.needs_ocr is False


def test_extract_text_unsupported_does_not_raise() -> None:
    """Nothing about extraction may propagate an exception to the caller."""
    for content_type in ("", "application/x-tar", "image/jpeg", "nonsense"):
        result = extract_text("x.bin", content_type, b"\x00\x01\x02")
        assert result.error is not None


def test_extract_text_truncates_to_max_chars() -> None:
    payload = b"x" * (MAX_TEXT_CHARS + 5_000)
    result = extract_text("big.txt", "text/plain", payload)
    assert len(result.text) == MAX_TEXT_CHARS
    assert result.meta.get("truncated") is True


def test_allowed_content_types_covers_supported_types() -> None:
    assert {
        "text/plain",
        "text/csv",
        "application/json",
        PDF_CONTENT_TYPE,
        DOCX_CONTENT_TYPE,
    } == set(ALLOWED_CONTENT_TYPES)


# ---------------------------------------------------------------------------
# extract_text: DOCX
# ---------------------------------------------------------------------------


def test_extract_text_docx_paragraphs() -> None:
    data = _docx_bytes("First paragraph", "Second paragraph")
    result = extract_text("ticket.docx", DOCX_CONTENT_TYPE, data)
    assert result.error is None
    assert "First paragraph" in result.text
    assert "Second paragraph" in result.text
    # Two paragraphs => two lines.
    assert [line for line in result.text.splitlines() if line] == [
        "First paragraph",
        "Second paragraph",
    ]


def test_extract_text_docx_unescapes_entities() -> None:
    result = extract_text("a.docx", DOCX_CONTENT_TYPE, _docx_bytes("Fare &amp; taxes &lt;incl&gt;"))
    assert result.text == "Fare & taxes <incl>"


def test_extract_text_docx_corrupt_zip_does_not_raise() -> None:
    result = extract_text("bad.docx", DOCX_CONTENT_TYPE, b"not a zip at all")
    assert result.error is not None
    assert result.text == ""


# ---------------------------------------------------------------------------
# extract_text: PDF
# ---------------------------------------------------------------------------


def test_extract_text_pdf_flate_encoded_text() -> None:
    data = _pdf_bytes(b"BT /F1 12 Tf (HELLO PDF) Tj ET\n")
    result = extract_text("ticket.pdf", PDF_CONTENT_TYPE, data)
    assert "HELLO PDF" in result.text
    assert result.needs_ocr is False
    assert result.error is None
    assert result.page_count == 1


def test_extract_text_pdf_without_text_layer_needs_ocr() -> None:
    """A stream that decodes fine but contains no text operators is a scan."""
    data = _pdf_bytes(b"q 612 0 0 792 0 0 cm /Im0 Do Q\n")
    result = extract_text("scan.pdf", PDF_CONTENT_TYPE, data)
    assert result.needs_ocr is True
    assert result.text == ""
    # A text-less PDF is normal input, not an error.
    assert result.error is None
    assert result.page_count == 1


def test_extract_text_pdf_tj_array_joins_kerning_pieces() -> None:
    data = _pdf_bytes(b"BT /F1 12 Tf [(HEL) -20 (LO) 10 ( PDF)] TJ ET\n")
    result = extract_text("kerning.pdf", PDF_CONTENT_TYPE, data)
    assert "HELLO PDF" in result.text


def test_extract_text_pdf_hex_string_operand() -> None:
    data = _pdf_bytes(b"BT /F1 12 Tf <48454C4C4F20484558> Tj ET\n")
    result = extract_text("hex.pdf", PDF_CONTENT_TYPE, data)
    assert "HELLO HEX" in result.text


def test_extract_text_pdf_literal_escapes() -> None:
    data = _pdf_bytes(rb"BT /F1 12 Tf (Line \(one\) \101) Tj ET" + b"\n")
    result = extract_text("escapes.pdf", PDF_CONTENT_TYPE, data)
    assert "Line (one) A" in result.text


def test_extract_text_pdf_stream_without_trailing_eol() -> None:
    """Some producers emit ``...dataendstream`` with no newline before the marker."""
    data = _pdf_bytes(b"BT /F1 12 Tf (NO NEWLINE) Tj ET\n", trailing_eol=False)
    result = extract_text("min.pdf", PDF_CONTENT_TYPE, data)
    assert "NO NEWLINE" in result.text
    assert result.needs_ocr is False


def test_extract_text_pdf_uncompressed_stream() -> None:
    """A stream with no /FlateDecode is read as-is."""
    data = _pdf_bytes(b"BT /F1 12 Tf (PLAIN STREAM) Tj ET\n", compress=False)
    result = extract_text("plain.pdf", PDF_CONTENT_TYPE, data)
    assert "PLAIN STREAM" in result.text


def test_extract_text_pdf_garbage_does_not_raise() -> None:
    result = extract_text("junk.pdf", PDF_CONTENT_TYPE, b"%PDF-1.4\nno streams here\n%%EOF")
    assert result.error is None
    assert result.needs_ocr is True


def test_extract_text_sniffs_mislabelled_pdf() -> None:
    """An unknown content type still gets parsed when the signature is a PDF."""
    data = _pdf_bytes(b"BT /F1 12 Tf (SNIFFED) Tj ET\n")
    result = extract_text("weird", "application/octet-stream", data)
    assert "SNIFFED" in result.text


# ---------------------------------------------------------------------------
# sha256_of
# ---------------------------------------------------------------------------


def test_sha256_of_is_stable_and_distinguishes_input() -> None:
    first = sha256_of(b"airdesk")
    assert first == sha256_of(b"airdesk")
    assert first != sha256_of(b"airdesk!")
    assert len(first) == 64
    assert first.islower()


# ---------------------------------------------------------------------------
# extract_structured
# ---------------------------------------------------------------------------


async def test_extract_structured_parses_clean_json() -> None:
    gateway = StubGateway([_json_reply()])
    extracted = await extract_structured("SGN-HAN e-ticket for TRAN BINH", gateway)

    assert isinstance(extracted, ExtractedDocument)
    assert extracted.doc_type is DocumentType.AIRLINE_TICKET
    assert extracted.confidence == pytest.approx(0.91)
    assert extracted.reference_numbers == ["ABC123"]
    assert extracted.amounts[0].value == pytest.approx(1_450_000.0)
    assert extracted.amounts[0].currency == "VND"
    assert len(extracted.passengers) == 1
    assert extracted.passengers[0].first_name == "Binh"
    assert extracted.passengers[0].last_name == "Tran"
    assert extracted.passengers[0].document_number == "C0123456"
    assert extracted.line_items[0].amount == pytest.approx(1_450_000.0)
    # Exactly one call: no repair round-trip on the happy path.
    assert len(gateway.calls) == 1
    assert gateway.calls[0]["response_format"] == {"type": "json_object"}
    assert gateway.calls[0]["temperature"] == 0.0


async def test_extract_structured_parses_fenced_json() -> None:
    gateway = StubGateway([f"```json\n{_json_reply()}\n```"])
    extracted = await extract_structured("text", gateway)
    assert extracted.doc_type is DocumentType.AIRLINE_TICKET
    assert extracted.passengers[0].last_name == "Tran"
    assert len(gateway.calls) == 1


async def test_extract_structured_parses_bare_fenced_json() -> None:
    gateway = StubGateway([f"```\n{_json_reply()}\n```"])
    extracted = await extract_structured("text", gateway)
    assert extracted.doc_type is DocumentType.AIRLINE_TICKET


async def test_extract_structured_unwraps_document_wrapper() -> None:
    payload = json.loads(_json_reply())
    gateway = StubGateway([json.dumps({"document": payload})])
    extracted = await extract_structured("text", gateway)
    assert extracted.doc_type is DocumentType.AIRLINE_TICKET
    assert extracted.reference_numbers == ["ABC123"]
    assert extracted.passengers[0].first_name == "Binh"


async def test_extract_structured_unwraps_fenced_wrapper() -> None:
    payload = json.loads(_json_reply())
    gateway = StubGateway([f"```json\n{json.dumps({'document': payload})}\n```"])
    extracted = await extract_structured("text", gateway)
    assert extracted.doc_type is DocumentType.AIRLINE_TICKET


async def test_extract_structured_repairs_invalid_json_once() -> None:
    """Garbage first, valid JSON on the repair call -> the valid payload."""
    gateway = StubGateway(["not json at all", _json_reply()])
    extracted = await extract_structured("text", gateway, filename="t.pdf")

    assert extracted.doc_type is DocumentType.AIRLINE_TICKET
    assert extracted.confidence == pytest.approx(0.91)
    assert len(gateway.calls) == 2
    # The repair prompt must carry both the bad output and the parse error.
    repair_messages = gateway.calls[1]["messages"]
    assert repair_messages[-1]["role"] == "user"
    assert "not json at all" in repair_messages[-1]["content"]


async def test_extract_structured_repairs_schema_violation() -> None:
    """Schema-invalid JSON (bad enum / out-of-range confidence) triggers repair."""
    gateway = StubGateway(
        [json.dumps({"doc_type": "not_a_type", "confidence": 5.0}), _json_reply()]
    )
    extracted = await extract_structured("text", gateway)
    assert extracted.doc_type is DocumentType.AIRLINE_TICKET
    assert len(gateway.calls) == 2


async def test_extract_structured_falls_back_when_always_invalid() -> None:
    gateway = StubGateway(["not json at all"])
    extracted = await extract_structured("some document text", gateway)

    assert extracted.doc_type is DocumentType.OTHER
    assert extracted.confidence == 0.0
    assert extracted.summary == "Extraction failed."
    assert extracted.passengers == []
    assert extracted.reference_numbers == []
    # Both attempts were made before giving up.
    assert len(gateway.calls) == 2


async def test_extract_structured_falls_back_on_gateway_error() -> None:
    """A failing gateway degrades to the safe payload instead of propagating."""

    class ExplodingGateway:
        def __init__(self) -> None:
            self.calls = 0

        async def complete(self, **kwargs) -> LLMResult:
            self.calls += 1
            raise RuntimeError("provider is down")

    gateway = ExplodingGateway()
    extracted = await extract_structured("text", gateway)
    assert extracted.doc_type is DocumentType.OTHER
    assert extracted.confidence == 0.0
    assert gateway.calls == 1


async def test_extract_structured_validates_partial_json() -> None:
    """Every field is optional, so a minimal object still validates."""
    gateway = StubGateway([json.dumps({"doc_type": "invoice", "confidence": 0.5})])
    extracted = await extract_structured("text", gateway)
    assert extracted.doc_type is DocumentType.INVOICE
    assert extracted.confidence == pytest.approx(0.5)
    assert extracted.passengers == []
    assert extracted.summary == ""


async def test_extract_structured_handles_empty_text_without_calling_llm() -> None:
    gateway = StubGateway([_json_reply()])
    extracted = await extract_structured("   ", gateway)
    assert extracted.doc_type is DocumentType.OTHER
    assert extracted.confidence == 0.0
    assert gateway.calls == []


async def test_extract_structured_uses_injected_gateway_when_omitted(monkeypatch) -> None:
    """``gateway=None`` resolves through the module-level default gateway.

    ``extract_structured`` takes an explicit gateway argument rather than a
    FastAPI ``Depends``, so a router wires it up with the value it receives
    from ``deps.get_agent_gateway``. This test pins that resolution path
    *offline*: ``get_llm_gateway`` is monkeypatched, so omitting the argument
    must never reach a real provider.
    """
    gateway = StubGateway([_json_reply()])
    monkeypatch.setattr(documents, "get_llm_gateway", lambda: gateway)

    extracted = await extract_structured("SGN-HAN e-ticket")

    assert extracted.doc_type is DocumentType.AIRLINE_TICKET
    assert extracted.passengers[0].last_name == "Tran"
    assert len(gateway.calls) == 1


# ---------------------------------------------------------------------------
# answer_about_document
# ---------------------------------------------------------------------------


async def test_answer_about_document_returns_stub_answer() -> None:
    gateway = StubGateway(["Departure is 2026-05-01 at 08:00 from SGN."])
    answer = await answer_about_document("When does it leave?", "Ticket SGN 2026-05-01", gateway)

    assert answer == "Departure is 2026-05-01 at 08:00 from SGN."
    assert len(gateway.calls) == 1
    sent = gateway.calls[0]["messages"]
    assert "only" in sent[0]["content"].lower()
    assert "Ticket SGN 2026-05-01" in sent[1]["content"]


async def test_answer_about_document_truncates_long_text() -> None:
    gateway = StubGateway(["ok"])
    long_text = "A" * 100_000 + "TAIL_MARKER"
    answer = await answer_about_document("q?", long_text, gateway)

    assert answer == "ok"
    sent_content = gateway.calls[0]["messages"][1]["content"]
    assert "TAIL_MARKER" not in sent_content
    assert len(sent_content) < 70_000


async def test_answer_about_document_without_text_skips_llm() -> None:
    gateway = StubGateway(["unused"])
    answer = await answer_about_document("q?", "   ", gateway)
    assert "no extracted text" in answer.lower()
    assert gateway.calls == []


async def test_answer_about_document_survives_gateway_error() -> None:
    class ExplodingGateway:
        async def complete(self, **kwargs) -> LLMResult:
            raise RuntimeError("provider is down")

    answer = await answer_about_document("q?", "some text", ExplodingGateway())
    assert isinstance(answer, str)
    assert answer
