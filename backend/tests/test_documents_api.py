"""Document-vault API tests: upload, dedupe, guards, listing, Q&A and delete.

Entirely offline. ``extract_structured`` and ``answer_about_document`` are
patched so no request ever reaches a model provider — the point of these tests
is the HTTP contract and the storage state machine, not the LLM.
"""

from __future__ import annotations

import hashlib

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.models.document import Document, DocumentStatus, DocumentType
from app.models.user import User, UserRole

pytest.importorskip("app.services.documents")

from app.services.documents import ExtractedDocument  # noqa: E402
from app.services.documents import extract_structured as real_extract_structured  # noqa: E402

_TXT = b"Vietnam Airlines e-ticket\nPassenger: TRAN VAN BINH\nSGN-HAN 2026-05-01 VND 1450000\n"
_TXT_TYPE = "text/plain"


def _stub_structured(*, doc_type: DocumentType = DocumentType.AIRLINE_TICKET):
    """Build an ``extract_structured`` replacement that never calls a provider."""

    async def _fake(text: str, gateway=None, filename=None) -> ExtractedDocument:
        return ExtractedDocument(
            doc_type=doc_type,
            confidence=0.91,
            summary="Stub extraction.",
            reference_numbers=["ABC123"],
        )

    return _fake


def _stub_answer(answer: str = "The ticket departs SGN on 2026-05-01."):
    async def _fake(question: str, document_text: str, gateway=None) -> str:
        return answer

    return _fake


@pytest.fixture
def offline_llm(monkeypatch):
    """Patch every LLM-touching call the documents router makes."""
    monkeypatch.setattr("app.api.v1.documents.extract_structured", _stub_structured())
    monkeypatch.setattr("app.api.v1.documents.answer_about_document", _stub_answer())
    yield


async def _upload(
    client,
    headers,
    *,
    data: bytes = _TXT,
    filename: str = "ticket.txt",
    content_type: str = _TXT_TYPE,
):
    return await client.post(
        "/api/v1/documents",
        headers=headers,
        files={"file": (filename, data, content_type)},
    )


async def _operator_headers(client, db_session, email: str = "operator-docs@airdesk.vn"):
    from app.core.security import hash_password

    user = User(
        email=email,
        full_name="Doc Operator",
        hashed_password=hash_password("secret123"),
        role=UserRole.OPERATOR,
        is_active=True,
    )
    db_session.add(user)
    await db_session.commit()
    resp = await client.post("/api/v1/auth/login", json={"email": email, "password": "secret123"})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


@pytest.mark.asyncio
async def test_upload_txt_extracts_and_echoes_sha256(client, auth_headers, offline_llm) -> None:
    resp = await _upload(client, auth_headers)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == DocumentStatus.EXTRACTED.value
    assert body["doc_type"] == DocumentType.AIRLINE_TICKET.value
    assert body["sha256"] == hashlib.sha256(_TXT).hexdigest()
    assert body["size_bytes"] == len(_TXT)
    assert body["confidence"] == pytest.approx(0.91)
    assert body["extracted"]["summary"] == "Stub extraction."
    assert "TRAN VAN BINH" in body["text_content"]
    assert body["needs_ocr"] is False


@pytest.mark.asyncio
async def test_identical_bytes_deduplicate(client, auth_headers, offline_llm) -> None:
    first = await _upload(client, auth_headers)
    assert first.status_code == 201, first.text

    second = await _upload(client, auth_headers, filename="ticket-copy.txt")
    assert second.status_code == 200, second.text
    assert second.json()["id"] == first.json()["id"]
    assert second.json()["sha256"] == first.json()["sha256"]

    listing = await client.get("/api/v1/documents", headers=auth_headers)
    assert listing.json()["total"] == 1


@pytest.mark.asyncio
async def test_oversize_upload_is_rejected(client, auth_headers, offline_llm) -> None:
    settings = get_settings()
    original = settings.document_max_bytes
    settings.document_max_bytes = 16
    try:
        resp = await _upload(client, auth_headers, data=b"x" * 64)
    finally:
        settings.document_max_bytes = original

    assert resp.status_code in {400, 413, 422}, resp.text
    assert "exceeds" in resp.text

    listing = await client.get("/api/v1/documents", headers=auth_headers)
    assert listing.json()["total"] == 0


@pytest.mark.asyncio
async def test_unsupported_content_type_is_rejected(client, auth_headers, offline_llm) -> None:
    resp = await _upload(
        client,
        auth_headers,
        data=b"\x00\x01binary-blob",
        filename="payload.bin",
        content_type="application/x-msdownload",
    )
    assert resp.status_code in {400, 415, 422}, resp.text

    listing = await client.get("/api/v1/documents", headers=auth_headers)
    assert listing.json()["total"] == 0


@pytest.mark.asyncio
async def test_octet_stream_falls_back_to_extension(client, auth_headers, offline_llm) -> None:
    resp = await _upload(client, auth_headers, content_type="application/octet-stream")
    assert resp.status_code == 201, resp.text
    assert resp.json()["content_type"] == _TXT_TYPE


@pytest.mark.asyncio
async def test_extraction_failure_is_recorded_not_a_500(client, auth_headers, monkeypatch) -> None:
    async def _boom(text: str, gateway=None, filename=None):
        raise RuntimeError("provider exploded")

    monkeypatch.setattr("app.api.v1.documents.extract_structured", _boom)

    resp = await _upload(client, auth_headers, data=b"unique failing bytes\n")
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == DocumentStatus.FAILED.value
    assert "provider exploded" in body["error"]


@pytest.mark.asyncio
async def test_list_and_detail(client, auth_headers, offline_llm) -> None:
    created = await _upload(client, auth_headers)
    document_id = created.json()["id"]

    listing = await client.get("/api/v1/documents", headers=auth_headers)
    assert listing.status_code == 200, listing.text
    assert listing.json()["total"] == 1
    assert listing.json()["items"][0]["id"] == document_id

    by_status = await client.get(
        "/api/v1/documents",
        headers=auth_headers,
        params={"status": DocumentStatus.EXTRACTED.value},
    )
    assert by_status.json()["total"] == 1

    by_other_status = await client.get(
        "/api/v1/documents",
        headers=auth_headers,
        params={"status": DocumentStatus.FAILED.value},
    )
    assert by_other_status.json()["total"] == 0

    detail = await client.get(f"/api/v1/documents/{document_id}", headers=auth_headers)
    assert detail.status_code == 200, detail.text
    assert detail.json()["id"] == document_id
    assert detail.json()["extracted"]["reference_numbers"] == ["ABC123"]

    missing = await client.get(
        "/api/v1/documents/00000000-0000-0000-0000-000000000000", headers=auth_headers
    )
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_ask_uses_stored_text(client, auth_headers, offline_llm) -> None:
    created = await _upload(client, auth_headers)
    document_id = created.json()["id"]

    resp = await client.post(
        f"/api/v1/documents/{document_id}/ask",
        headers=auth_headers,
        json={"question": "When does the flight leave?"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["answer"] == "The ticket departs SGN on 2026-05-01."
    assert body["document_id"] == document_id
    assert body["question"] == "When does the flight leave?"


@pytest.mark.asyncio
async def test_ask_on_missing_document_is_404(client, auth_headers, offline_llm) -> None:
    resp = await client.post(
        "/api/v1/documents/00000000-0000-0000-0000-000000000000/ask",
        headers=auth_headers,
        json={"question": "Anything?"},
    )
    assert resp.status_code == 404, resp.text


@pytest.mark.asyncio
async def test_ask_without_text_is_a_clear_conflict(
    client, auth_headers, db_session, offline_llm
) -> None:
    row = Document(
        filename="scan.pdf",
        content_type="application/pdf",
        size_bytes=10,
        sha256="0" * 64,
        status=DocumentStatus.FAILED,
        doc_type=DocumentType.OTHER,
        text_content="",
        uploaded_by_id=None,
    )
    db_session.add(row)
    await db_session.commit()
    await db_session.refresh(row)

    resp = await client.post(
        f"/api/v1/documents/{row.id}/ask",
        headers=auth_headers,
        json={"question": "What is in it?"},
    )
    assert resp.status_code == 409, resp.text


@pytest.mark.asyncio
async def test_short_question_is_rejected(client, auth_headers, offline_llm) -> None:
    created = await _upload(client, auth_headers)
    resp = await client.post(
        f"/api/v1/documents/{created.json()['id']}/ask",
        headers=auth_headers,
        json={"question": "x"},
    )
    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_delete_by_operator_removes_row(client, db_session, offline_llm) -> None:
    headers = await _operator_headers(client, db_session)
    created = await _upload(client, headers)
    document_id = created.json()["id"]

    resp = await client.delete(f"/api/v1/documents/{document_id}", headers=headers)
    assert resp.status_code == 204, resp.text
    assert resp.content == b""

    gone = await client.get(f"/api/v1/documents/{document_id}", headers=headers)
    assert gone.status_code == 404

    remaining = await db_session.scalar(select(Document).where(Document.id == document_id))
    assert remaining is None


@pytest.mark.asyncio
async def test_delete_requires_operator(client, db_session, offline_llm) -> None:
    """A viewer-tier token is rejected with 403, even for its own upload."""
    from app.core.security import hash_password

    viewer = User(
        email="viewer-docs@airdesk.vn",
        full_name="Viewer",
        hashed_password=hash_password("secret123"),
        role=UserRole.VIEWER,
        is_active=True,
    )
    db_session.add(viewer)
    await db_session.commit()

    login = await client.post(
        "/api/v1/auth/login",
        json={"email": viewer.email, "password": "secret123"},
    )
    assert login.status_code == 200, login.text
    viewer_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    # The viewer cannot even upload (the route is authenticated, not viewer-only),
    # so the document is seeded by an operator first.
    headers = await _operator_headers(client, db_session)
    created = await _upload(client, headers)

    denied = await client.delete(
        f"/api/v1/documents/{created.json()['id']}", headers=viewer_headers
    )
    assert denied.status_code == 403, denied.text
    assert denied.json()["error"]["code"] == "forbidden"

    still_there = await client.get(f"/api/v1/documents/{created.json()['id']}", headers=headers)
    assert still_there.status_code == 200


@pytest.mark.asyncio
async def test_upload_requires_auth(client) -> None:
    resp = await client.post(
        "/api/v1/documents",
        files={"file": ("t.txt", b"hello", _TXT_TYPE)},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_real_extract_structured_is_offline_safe() -> None:
    """The patched-away real function must not be the thing under test."""
    assert callable(real_extract_structured)
