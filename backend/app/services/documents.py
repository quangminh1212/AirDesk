"""Document text extraction and LLM-backed structured extraction.

Two clearly separated stages, because they have very different cost and
failure profiles:

1. **Text extraction** (:func:`extract_text`) is pure stdlib, offline and
   cheap. It turns the uploaded bytes into plain text and decides whether the
   file actually *has* a text layer. A scanned ticket has none, so it is
   flagged ``needs_ocr`` instead of being silently treated as empty.
2. **Structured extraction** (:func:`extract_structured`) sends that text to
   the LLM and validates the reply into :class:`ExtractedDocument`. Model
   output is untrusted input: it is unwrapped, de-fenced, validated, retried
   once with a repair prompt, and finally degraded to a safe empty payload —
   it never raises into the request path.

The module deliberately owns no ORM writes. Callers persist the result onto
``app.models.document.Document``; this keeps the extraction logic testable
without a database and reusable by a background worker.

Why no third-party parsers: PDF/DOCX handling here is intentionally
approximate (see :func:`_extract_pdf`), and pulling a 30 MB parser stack into
the upload path for that is not a trade this service wants to make.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
import zlib
from dataclasses import dataclass, field
from typing import Any, Protocol

from pydantic import BaseModel, Field
from pydantic import ValidationError as PydanticValidationError

from app.core.llm import LLMResult, get_llm_gateway
from app.core.logging import get_logger
from app.models.document import DocumentType

logger = get_logger(__name__)

#: Extracted text stored on the row is truncated to this many characters.
MAX_TEXT_CHARS = 200_000

#: Document text sent to the model for Q&A is truncated to this many characters.
MAX_QA_CHARS = 60_000

#: Content types :func:`extract_text` knows how to handle.
ALLOWED_CONTENT_TYPES: frozenset[str] = frozenset(
    {
        "text/plain",
        "text/csv",
        "application/json",
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }
)

#: Plain-text-ish types decoded directly as UTF-8.
_TEXT_CONTENT_TYPES: frozenset[str] = frozenset({"text/plain", "text/csv", "application/json"})

PDF_CONTENT_TYPE = "application/pdf"
DOCX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

#: Fallback signature check so a mislabelled upload still gets parsed. Only
#: consulted when the declared content type is unknown; a declared type always
#: wins, which is what makes the "unsupported" branch reachable and testable.
_MAGIC_PDF = b"%PDF-"
_MAGIC_ZIP = b"PK\x03\x04"


class LLMGateway(Protocol):
    """The single gateway method this module depends on.

    Structural (not nominal) so tests can inject a stub without importing
    LiteLLM, and so the service is not coupled to :class:`LiteLLMGateway`.
    """

    async def complete(self, **kwargs: Any) -> LLMResult: ...


@dataclass
class ExtractionResult:
    """Outcome of parsing an uploaded file into plain text.

    ``error`` is set only for *unsupported* input. A supported but text-less
    file (a scan) is not an error: it is ``needs_ocr=True`` with empty text,
    which is exactly the state the UI should surface as "run OCR".
    """

    text: str
    needs_ocr: bool = False
    error: str | None = None
    page_count: int | None = None
    meta: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        """True when no error was recorded (may still need OCR)."""
        return self.error is None

    def truncated_text(self, limit: int = MAX_TEXT_CHARS) -> str:
        """Text cropped to ``limit`` characters, for storage."""
        return self.text[:limit]


# ---------------------------------------------------------------------------
# Structured payload
# ---------------------------------------------------------------------------


class AmountItem(BaseModel):
    """A money amount found on the document."""

    label: str = ""
    value: float = 0.0
    currency: str | None = None


class PassengerItem(BaseModel):
    """One traveller / document holder."""

    first_name: str = ""
    last_name: str = ""
    document_number: str | None = None
    date_of_birth: str | None = None


class LineItem(BaseModel):
    """One row of an invoice / ticket breakdown."""

    description: str = ""
    quantity: float | None = None
    unit_price: float | None = None
    amount: float | None = None


class ExtractedDocument(BaseModel):
    """Validated structured payload produced by the LLM extraction step.

    Every list defaults to empty and every optional has a default, so a model
    that returns only the fields it understood still validates.
    """

    doc_type: DocumentType = DocumentType.OTHER
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    summary: str = ""
    reference_numbers: list[str] = Field(default_factory=list)
    amounts: list[AmountItem] = Field(default_factory=list)
    dates: list[str] = Field(default_factory=list)
    parties: list[str] = Field(default_factory=list)
    passengers: list[PassengerItem] = Field(default_factory=list)
    line_items: list[LineItem] = Field(default_factory=list)


def _fallback_document(summary: str = "Extraction failed.") -> ExtractedDocument:
    """Safe payload used when the model cannot be coerced into the schema."""
    return ExtractedDocument(
        doc_type=DocumentType.OTHER,
        confidence=0.0,
        summary=summary,
        reference_numbers=[],
        amounts=[],
        dates=[],
        parties=[],
        passengers=[],
        line_items=[],
    )


#: Exact JSON skeleton embedded in the extraction prompt.
_JSON_SKELETON = """{
  "doc_type": "airline_ticket | invoice | itinerary | passport | contract | purchase_order | other",
  "confidence": 0.0,
  "summary": "one-paragraph summary of the document",
  "reference_numbers": ["ABC123"],
  "amounts": [{"label": "total", "value": 1450000.0, "currency": "VND"}],
  "dates": ["2026-05-01"],
  "parties": ["Vietnam Airlines"],
  "passengers": [
    {
      "first_name": "Binh",
      "last_name": "Tran",
      "document_number": "C0123456",
      "date_of_birth": "1990-01-31"
    }
  ],
  "line_items": [
    {"description": "SGN-HAN fare", "quantity": 1, "unit_price": 1450000.0, "amount": 1450000.0}
  ]
}"""

_EXTRACTION_SYSTEM_PROMPT = (
    "You are a precise document-intake assistant for a Vietnamese travel agency. "
    "You extract structured data from document text that may be in Vietnamese or English. "
    "Reply with a single JSON object and nothing else: no prose, no markdown, no code fences. "
    "Use the JSON schema exactly as given. Never invent values: if a field is not present in "
    "the text, leave the list empty, use null for an unknown optional, and 0.0 for an unknown "
    "amount. Dates must be ISO-8601 (YYYY-MM-DD). `confidence` is your own 0..1 estimate that "
    'the extraction is correct. Choose `doc_type` from the allowed set and use "other" when '
    "the document does not clearly match any of them."
)

_REPAIR_INSTRUCTION = (
    "Your previous reply could not be parsed as the required JSON object. "
    "Here is what you returned:\n\n---\n{bad}\n---\n\n"
    "The parser reported this error:\n\n{error}\n\n"
    "Return ONLY a corrected, single JSON object matching the schema exactly. "
    "No markdown fences, no commentary."
)

_QA_SYSTEM_PROMPT = (
    "You answer questions strictly and only from the document text supplied by the user. "
    "If the answer is not present in that text, say plainly that you cannot find it in the "
    "document. Never use outside knowledge, never guess, and never invent reference numbers, "
    "amounts or dates. Keep answers short and quote the relevant fragment when it helps."
)

#: ```json ... ``` / ``` ... ``` wrappers some models add around JSON.
_FENCE_RE = re.compile(r"```[ \t]*(?:json|JSON)?[ \t]*\r?\n?(.*?)\r?\n?[ \t]*```", re.DOTALL)
#: A bare "json" language tag left behind if the model emitted an unbalanced fence.
_LEADING_TAG_RE = re.compile(r"^\s*(?:json|JSON)\s*[\r\n]+")

#: Wrapper keys models use when they nest the payload one level too deep.
_WRAPPER_KEYS = ("document", "extracted_document", "data", "result", "extraction", "output")


# ---------------------------------------------------------------------------
# Text extraction
# ---------------------------------------------------------------------------


def sha256_of(data: bytes) -> str:
    """Hex SHA-256 of ``data`` — the de-duplication key for uploads."""
    return hashlib.sha256(data).hexdigest()


def _extract_plain(data: bytes) -> ExtractionResult:
    """Decode a text-like payload; malformed bytes are replaced, never fatal."""
    text = data.decode("utf-8", errors="replace")
    return ExtractionResult(text=text, needs_ocr=False, meta={"chars": len(text)})


# --- PDF -------------------------------------------------------------------

#: String operands of the text-showing operators, in both literal and hex form.
#: Only ``Tj``/``TJ``/``'``/``"`` consume a string; layout operators such as
#: ``Td``/``Tf``/``Td`` carry no user-visible characters and are skipped.
_PDF_TEXT_OP_RE = re.compile(
    rb"(?P<lit>\((?:\\.|[^\\()])*\)|<[0-9A-Fa-f\s]*>)"  # one string operand
    rb"\s*(?:Tj|TJ|'|\")"  # ...followed by the operator that shows it
    rb"|\[(?:[^\[\]\\]|\\.)*\]\s*TJ",  # or a whole TJ array
    re.DOTALL,
)
#: Hex strings only appear inside text objects; outside one, `<...>` is a dict.
_PDF_HEXSTRING_RE = re.compile(rb"<([0-9A-Fa-f\s]+)>")
#: A PDF literal string body: `(...)` with backslash escapes.
_PDF_LITERAL_RE = re.compile(rb"\(((?:\\.|[^\\()])*)\)", re.DOTALL)
_PDF_PAGE_RE = re.compile(rb"/Type\s*/Page\b")
#: A PDF stream body sits between ``stream`` and ``endstream``. The spec allows
#: any whitespace (or none at all) around the body, and producers differ, so the
#: body is captured greedily up to the last ``endstream`` of the block and then
#: trimmed rather than assuming a CR/LF pair on both sides.
_PDF_STREAM_RE = re.compile(rb"stream(?:\r\n|\r|\n)?(.*?)endstream", re.DOTALL)
_PDF_TRAILING_EOL = re.compile(rb"(?:\r\n|\r|\n)$")
_PDF_ESCAPES = {
    ord("n"): b"\n",
    ord("r"): b"\r",
    ord("t"): b"\t",
    ord("b"): b"\b",
    ord("f"): b"\f",
    ord("("): b"(",
    ord(")"): b")",
    ord("\\"): b"\\",
}


def _pdf_unescape_into(raw: bytes, out: bytearray) -> None:
    """Append a PDF literal string body to ``out``, resolving escapes."""
    index = 0
    length = len(raw)
    while index < length:
        byte = raw[index]
        if byte != 0x5C:  # backslash
            out.append(byte)
            index += 1
            continue
        index += 1
        if index >= length:
            break  # trailing lone backslash
        escaped = raw[index]
        index += 1
        if escaped in _PDF_ESCAPES:
            out.extend(_PDF_ESCAPES[escaped])
        elif escaped in (0x0A, 0x0D):  # line continuation: the newline is dropped
            if escaped == 0x0D and index < length and raw[index] == 0x0A:
                index += 1
        elif 0x30 <= escaped <= 0x37:  # up to 3 octal digits, e.g. \101 -> "A"
            octal = bytes([escaped])
            while len(octal) < 3 and index < length and 0x30 <= raw[index] <= 0x37:
                octal += bytes([raw[index]])
                index += 1
            out.append(int(octal, 8) & 0xFF)
        else:
            out.append(escaped)


def _pdf_text_from_stream(raw: bytes) -> str:
    """Pull visible strings out of a single (already inflated) content stream.

    Only string operands of ``Tj``/``TJ``/``'``/``"`` are collected: text-side
    operators like ``Td``/``Tf`` carry no user-visible characters. Strings
    inside a single ``TJ`` array are concatenated because in PDF they are
    pieces of one word split for kerning, not separate words.
    """
    pieces: list[str] = []
    for match in _PDF_TEXT_OP_RE.finditer(raw):
        operand = match.group(0)
        if match.lastgroup:
            # A single string operand: slice it off so the operator token itself
            # can never be mistaken for a hex string (`Tj` is not hex, but a
            # stray `<...>` elsewhere in the operator run would be).
            operand = match.group(match.lastgroup)
        buffer = bytearray()
        for token in _PDF_HEXSTRING_RE.finditer(operand):
            digits = re.sub(rb"\s+", b"", token.group(1)).decode("ascii")
            if len(digits) % 2:
                digits += "0"
            try:
                buffer.extend(bytes.fromhex(digits))
            except ValueError:  # pragma: no cover - regex already restricts to hex
                continue
        for string_match in _PDF_LITERAL_RE.finditer(operand):
            _pdf_unescape_into(string_match.group(1), buffer)
        # A hex-decoded run must not also contribute the operator token's own
        # bytes, so the raw fallback is only used when nothing decoded.
        if not buffer and match.lastgroup is None:
            buffer.extend(re.sub(rb"\s+", b"", operand))
        if buffer:
            pieces.append(buffer.decode("latin-1"))
    # Each collected operand is a run of text; PDF re-positions between runs
    # with Td/TD rather than with whitespace, so runs are newline-joined to
    # keep successive lines separate while never splitting a word apart.
    #
    # Deliberately NO raw-bytes fallback here: returning the undecoded stream
    # (operators, colour commands, image dictionary snippets) as "text" would
    # look like a successful extraction and silently suppress the needs_ocr
    # signal that a genuine scan has to raise.
    return "\n".join(pieces)


def _pdf_page_count(raw: bytes) -> int | None:
    """Count pages by ``/Type /Page``; ``None`` when the PDF looks malformed."""
    count = len(_PDF_PAGE_RE.findall(raw))
    if count:
        return count
    # Some writers omit the space: "/Type/Page".
    count = len(re.findall(rb"/Type/Page\b", raw))
    return count or None


def _extract_pdf(data: bytes) -> ExtractionResult:
    """Extract text from a PDF using stdlib only.

    Strategy (deliberately shallow, see the module docstring): walk
    ``stream``…``endstream`` blocks, inflate the ``/FlateDecode`` ones, and
    read the string operands of the text operators inside each. Pages are
    counted from ``/Type /Page`` markers.

    When nothing readable comes back the file is reported as ``needs_ocr`` —
    a scanned or vector-only PDF is a normal input, not an exception.
    """
    page_count = _pdf_page_count(data)
    meta: dict = {"bytes": len(data)}
    if page_count is not None:
        meta["page_count"] = page_count

    chunks: list[str] = []
    stream_count = 0
    inflated_count = 0
    flate_count = 0
    for match in re.finditer(_PDF_STREAM_RE, data):
        stream_count += 1
        header = data[max(0, match.start() - 400) : match.start()]
        is_flate = b"/FlateDecode" in header
        if is_flate:
            flate_count += 1
        # Trim exactly one EOL from each side; the EOL after "stream" may already
        # have been consumed as part of the delimiter, hence ``lstrip`` is wrong.
        block = _PDF_TRAILING_EOL.sub(b"", match.group(1))
        block = block.lstrip(b"\r\n")
        if is_flate:
            try:
                block = zlib.decompress(block)
                inflated_count += 1
            except zlib.error:
                # Keep the raw bytes: the fallback regex below may still find
                # literal string operands in an unencoded stream.
                continue
        text = _pdf_text_from_stream(block)
        if text.strip():
            chunks.append(text)

    text = "\n".join(chunks).strip()
    meta.update(
        {
            "streams": stream_count,
            "flate_streams": flate_count,
            "inflated_streams": inflated_count,
            "chars": len(text),
        }
    )
    if not text:
        logger.info("pdf_without_text_layer", bytes=len(data), page_count=page_count)
        return ExtractionResult(
            text="",
            needs_ocr=True,
            page_count=page_count,
            meta=meta,
        )
    return ExtractionResult(text=text, needs_ocr=False, page_count=page_count, meta=meta)


# --- DOCX ------------------------------------------------------------------

#: A paragraph that carries no text still ends a line in Word.
_DOCX_PARAGRAPH_RE = re.compile(r"</w:p>")
#: Tabs and hard breaks are real whitespace, not structure to drop.
_DOCX_BREAK_RE = re.compile(r"<w:(?:br|cr)\b[^>]*/?>")
_DOCX_TAB_RE = re.compile(r"<w:tab\b[^>]*/?>")
_DOCX_TAG_RE = re.compile(r"<[^>]+>")


def _extract_docx(data: bytes) -> ExtractionResult:
    """Read ``word/document.xml`` out of the OOXML zip and flatten it to text.

    Paragraph ends become newlines, tabs/breaks become whitespace, and every
    remaining tag is dropped before XML entities are unescaped.
    """
    import io
    import zipfile

    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            xml_bytes = archive.read("word/document.xml")
    except (zipfile.BadZipFile, KeyError, OSError) as exc:
        logger.warning("docx_parse_failed", error=str(exc))
        return ExtractionResult(text="", error=f"could not read DOCX: {exc}")

    xml_text = xml_bytes.decode("utf-8", errors="replace")
    xml_text = _DOCX_PARAGRAPH_RE.sub("\n", xml_text)
    xml_text = _DOCX_BREAK_RE.sub("\n", xml_text)
    xml_text = _DOCX_TAB_RE.sub("\t", xml_text)
    xml_text = _DOCX_TAG_RE.sub("", xml_text)
    xml_text = html.unescape(xml_text)

    lines = [line.strip() for line in xml_text.splitlines()]
    text = "\n".join(line for line in lines if line)
    return ExtractionResult(text=text, needs_ocr=False, meta={"chars": len(text)})


def extract_text(filename: str, content_type: str, data: bytes) -> ExtractionResult:
    """Extract plain text from an uploaded file. Never raises.

    ``content_type`` is authoritative when it is one we support. When it is
    not, the bytes are sniffed for a PDF/ZIP signature so a mislabelled upload
    still gets parsed; only genuinely unknown input yields ``error``.
    """
    declared = (content_type or "").split(";")[0].strip().lower()

    try:
        if declared in _TEXT_CONTENT_TYPES:
            result = _extract_plain(data)
        elif declared == PDF_CONTENT_TYPE:
            result = _extract_pdf(data)
        elif declared == DOCX_CONTENT_TYPE:
            result = _extract_docx(data)
        elif data.startswith(_MAGIC_PDF):
            logger.info("extract_text_sniffed_pdf", filename=filename, declared=declared)
            result = _extract_pdf(data)
        elif data.startswith(_MAGIC_ZIP):
            logger.info("extract_text_sniffed_docx", filename=filename, declared=declared)
            result = _extract_docx(data)
        else:
            return ExtractionResult(
                text="",
                error=f"unsupported content type: {content_type!r}",
                meta={"filename": filename, "content_type": content_type},
            )
    except Exception as exc:  # noqa: BLE001 - extraction must never break the upload
        logger.warning(
            "extract_text_failed",
            filename=filename,
            content_type=content_type,
            error=str(exc),
        )
        return ExtractionResult(
            text="",
            error=f"extraction failed: {exc}",
            meta={"filename": filename, "content_type": content_type},
        )

    result.meta.setdefault("filename", filename)
    result.meta.setdefault("content_type", content_type)
    if len(result.text) > MAX_TEXT_CHARS:
        result.meta["truncated"] = True
        result.text = result.text[:MAX_TEXT_CHARS]
    return result


# ---------------------------------------------------------------------------
# LLM JSON plumbing
# ---------------------------------------------------------------------------


def _strip_code_fences(raw: str) -> str:
    """Remove ```json … ``` wrappers and any stray ``json`` language tag."""
    text = (raw or "").strip()
    fenced = _FENCE_RE.search(text)
    if fenced:
        text = fenced.group(1).strip()
    if text.startswith("```"):
        text = text[3:]
        if text.lower().startswith("json"):
            text = text[4:]
    text = text.rstrip()
    if text.endswith("```"):
        text = text[:-3]
    # A model may emit a fence it never closed. The json.loads call will fail
    # and trigger the repair path, which is the outcome we want.
    return _LEADING_TAG_RE.sub("", text).strip()


def _unwrap(payload: Any) -> Any:
    """Descend through a single-key wrapper object such as ``{"document": …}``."""
    for _ in range(4):
        if not isinstance(payload, dict):
            break
        wrapper = next((k for k in _WRAPPER_KEYS if k in payload), None)
        if wrapper is None or not isinstance(payload[wrapper], dict):
            break
        payload = payload[wrapper]
    return payload


def _parse_extracted(raw: str) -> ExtractedDocument:
    """Parse model output into :class:`ExtractedDocument`.

    Raises ``json.JSONDecodeError`` or ``pydantic.ValidationError`` so the
    caller can decide between retrying and falling back.
    """
    payload = json.loads(_strip_code_fences(raw))
    if isinstance(payload, list):
        payload = next((item for item in payload if isinstance(item, dict)), {})
        payload = _unwrap(payload)
    payload = _unwrap(payload)
    if not isinstance(payload, dict):
        raise ValueError(f"model returned {type(payload).__name__}, expected a JSON object")
    return ExtractedDocument.model_validate(payload)


def _build_extraction_messages(text: str, filename: str | None) -> list[dict[str, Any]]:
    """System + user prompt carrying the schema skeleton and the document."""
    source = f"Filename: {filename}\n\n" if filename else ""
    user_content = (
        "Extract structured data from the document text below.\n\n"
        "Return exactly one JSON object with this shape:\n"
        f"{_JSON_SKELETON}\n\n"
        '"doc_type" must be one of: ' + ", ".join(t.value for t in DocumentType) + "\n\n"
        f"{source}"
        "Document text:\n---\n"
        f"{text[:MAX_TEXT_CHARS]}\n---"
    )
    return [
        {"role": "system", "content": _EXTRACTION_SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]


async def extract_structured(
    text: str,
    gateway: LLMGateway | None = None,
    filename: str | None = None,
) -> ExtractedDocument:
    """Ask the model to structure ``text``; always returns a valid payload.

    The model output is treated as untrusted: it is de-fenced, unwrapped,
    validated, and retried once with the parse error fed back. If that also
    fails the caller still gets a well-formed ``ExtractedDocument`` marked
    ``OTHER`` / ``0.0`` rather than an exception.
    """
    gateway = gateway or get_llm_gateway()
    messages = _build_extraction_messages(text, filename)

    if not text.strip():
        logger.info("extract_structured_empty_text", filename=filename)
        return _fallback_document("No text was available to extract from.")

    last_raw = ""
    last_error = ""
    for attempt in (1, 2):
        try:
            result = await gateway.complete(
                messages=messages,
                response_format={"type": "json_object"},
                temperature=0.0,
            )
        except Exception as exc:  # noqa: BLE001 - gateway already retries/falls back
            logger.warning(
                "extract_structured_llm_failed",
                filename=filename,
                attempt=attempt,
                error=str(exc),
            )
            return _fallback_document(f"Extraction failed: {exc}")

        last_raw = result.content or ""
        try:
            extracted = _parse_extracted(last_raw)
        except (json.JSONDecodeError, PydanticValidationError, ValueError) as exc:
            last_error = str(exc)
            logger.warning(
                "extract_structured_invalid_json",
                filename=filename,
                attempt=attempt,
                error=last_error,
            )
            if attempt == 1:
                messages = [
                    *messages,
                    {"role": "assistant", "content": last_raw},
                    {
                        "role": "user",
                        "content": _REPAIR_INSTRUCTION.format(bad=last_raw, error=last_error),
                    },
                ]
                continue
            break
        else:
            logger.info(
                "extract_structured_ok",
                filename=filename,
                attempt=attempt,
                doc_type=extracted.doc_type.value,
                confidence=extracted.confidence,
            )
            return extracted

    logger.warning(
        "extract_structured_giving_up",
        filename=filename,
        error=last_error,
        raw_preview=last_raw[:200],
    )
    return _fallback_document("Extraction failed.")


async def answer_about_document(
    question: str,
    document_text: str,
    gateway: LLMGateway | None = None,
) -> str:
    """Answer ``question`` using only ``document_text`` as ground truth.

    The text is truncated before sending: a 200k-character stored document
    would otherwise burn the context window on every follow-up question.
    """
    gateway = gateway or get_llm_gateway()
    excerpt = (document_text or "")[:MAX_QA_CHARS]

    if not excerpt.strip():
        return "There is no extracted text for this document to answer from."

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": _QA_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (f"Document text:\n---\n{excerpt}\n---\n\nQuestion: {question}"),
        },
    ]
    try:
        result = await gateway.complete(messages=messages, temperature=0.0)
    except Exception as exc:  # noqa: BLE001 - a Q&A failure must not 500 the request
        logger.warning("answer_about_document_failed", error=str(exc))
        return "I could not answer that because the language model was unavailable."

    answer = (result.content or "").strip()
    logger.info(
        "document_question_answered", question_chars=len(question), answer_chars=len(answer)
    )
    return answer or "I could not find the answer in this document."


__all__ = [
    "ALLOWED_CONTENT_TYPES",
    "MAX_QA_CHARS",
    "MAX_TEXT_CHARS",
    "AmountItem",
    "ExtractedDocument",
    "ExtractionResult",
    "LineItem",
    "PassengerItem",
    "answer_about_document",
    "extract_structured",
    "extract_text",
    "sha256_of",
]
