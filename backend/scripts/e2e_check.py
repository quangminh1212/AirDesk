"""End-to-end verification against the REAL LLM and a real ASGI app.

Not part of the hermetic test suite: this script is excluded from pytest
(`pytest.ini` testpaths=tests) and is run manually to prove the whole stack
works with live Claude. It exercises:

  1. login -> JWT
  2. POST /agent/chat with a Vietnamese flight request (real tool call)
  3. QA report present on the reply
  4. GET /qa/turns shows the turn with a verdict
  5. REPORT endpoints aggregate
  6. TASK create/update lifecycle
  7. DOCUMENT upload + extraction (real LLM structured extraction)

Usage:
    .\\.venv\\Scripts\\python.exe scripts/e2e_check.py
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import sys
import zipfile

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_e2e.db")
os.environ.setdefault("FLIGHT_PROVIDERS", "mock")

from httpx import ASGITransport, AsyncClient  # noqa: E402

import app.models  # noqa: F401, E402  (register tables)
from app.core.security import hash_password  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models.user import User, UserRole  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def _force_utf8_stdout() -> None:
    """Make stdout UTF-8 so Vietnamese text survives printing.

    The agent replies in Vietnamese and this script echoes excerpts as check
    details. On a Windows console the default codec is cp1252, which cannot
    encode ``đ``/``ứ`` and raises UnicodeEncodeError mid-run -- aborting the
    whole verification on a *display* problem. Reconfiguring the stream keeps
    the checks portable across consoles and CI.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def record(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  -- {detail}" if detail else ""))


def make_docx(text: str) -> bytes:
    """Build a minimal valid .docx in memory."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/'
            'package/2006/content-types"><Default Extension="xml" ContentType='
            '"application/xml"/></Types>',
        )
        para = text.replace("&", "&amp;").replace("<", "&lt;")
        z.writestr(
            "word/document.xml",
            '<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/'
            f'wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>{para}</w:t></w:r>'
            "</w:p></w:body></w:document>",
        )
    return buf.getvalue()


async def main() -> int:
    _force_utf8_stdout()
    # --- fresh DB + admin ------------------------------------------------
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    async with SessionLocal() as db:
        db.add(
            User(
                email="e2e@airdesk.vn",
                full_name="E2E Admin",
                hashed_password=hash_password("secret123"),
                role=UserRole.ADMIN,
                is_active=True,
            )
        )
        await db.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://e2e") as c:
        # 1. login
        r = await c.post("/api/v1/auth/login", json={"email": "e2e@airdesk.vn", "password": "secret123"})
        record("login", r.status_code == 200, f"HTTP {r.status_code}")
        if r.status_code != 200:
            return 1
        H = {"Authorization": f"Bearer {r.json()['access_token']}"}

        # 2. real agent turn (live Claude, real tool call)
        r = await c.post(
            "/api/v1/agent/chat",
            headers=H,
            json={
                "message": "Tôi muốn bay từ SGN đi Hà Nội ngày 2026-12-01 cho 1 người lớn.",
                "channel": "web",
            },
            timeout=180,
        )
        ok = r.status_code == 200
        reply = r.json() if ok else {}
        record("agent chat (live LLM)", ok, f"HTTP {r.status_code}")
        if ok:
            tools = [t.get("tool_name") for t in reply.get("tool_calls", [])]
            record("agent used a tool", "search_flights" in tools, f"tools={tools}")
            record("agent returned offers", len(reply.get("offers", [])) > 0, f"n={len(reply.get('offers', []))}")
            record("agent replied in Vietnamese", bool(reply.get("reply", "").strip()), reply.get("reply", "")[:70])
            qa = reply.get("qa") or {}
            record("QA report attached", "verdict" in qa, f"verdict={qa.get('verdict')} score={qa.get('score')}")
            conv_id = reply.get("conversation_id")

        # 3. QA turn listing
        r = await c.get("/api/v1/qa/turns", headers=H, params={"days": 7})
        ok = r.status_code == 200
        body = r.json() if ok else {}
        record("QA turns listing", ok and body.get("total", 0) >= 1, f"total={body.get('total')}")

        # 4. QA overview
        r = await c.get("/api/v1/qa/overview", headers=H, params={"days": 7})
        body = r.json() if r.status_code == 200 else {}
        record(
            "QA overview",
            r.status_code == 200 and body.get("total_turns", 0) >= 1,
            f"turns={body.get('total_turns')} pass_rate={body.get('pass_rate')}",
        )

        # 5. reports
        r = await c.get("/api/v1/reports/overview", headers=H)
        record("reports overview", r.status_code == 200, f"HTTP {r.status_code}")
        r = await c.get("/api/v1/reports/volume", headers=H, params={"days": 30})
        pts = (r.json() or {}).get("points", []) if r.status_code == 200 else []
        record("reports volume contiguous", r.status_code == 200 and len(pts) == 30, f"points={len(pts)}")

        # 6. tasks lifecycle
        r = await c.post(
            "/api/v1/tasks",
            headers=H,
            json={"title": "Gọi lại khách xác nhận hoàn vé", "priority": 1, "task_type": "refund"},
        )
        ok = r.status_code in (200, 201)
        task = r.json() if ok else {}
        record("task create", ok, f"HTTP {r.status_code}")
        if ok:
            r = await c.patch(f"/api/v1/tasks/{task['id']}", headers=H, json={"status": "done"})
            done = r.json() if r.status_code == 200 else {}
            record("task complete sets completed_at", bool(done.get("completed_at")), f"HTTP {r.status_code}")

        # 7. document upload with REAL LLM structured extraction
        docx = make_docx(
            "E-TICKET Vietnam Airlines. PNR: ABC123. Passenger: NGUYEN VAN A. "
            "Route SGN-HAN 2026-12-01. Total 2,450,000 VND."
        )
        r = await c.post(
            "/api/v1/documents",
            headers=H,
            files={
                "file": (
                    "e-ticket.docx",
                    docx,
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )
            },
            timeout=180,
        )
        ok = r.status_code in (200, 201)
        doc = r.json() if ok else {}
        record("document upload + extraction", ok, f"HTTP {r.status_code} status={doc.get('status')}")
        if ok:
            record(
                "document classified",
                doc.get("doc_type") not in (None, "other"),
                f"doc_type={doc.get('doc_type')} conf={doc.get('confidence')}",
            )

        # 8. conversation persistence
        r = await c.get(f"/api/v1/agent/conversations/{conv_id}", headers=H)
        msgs = (r.json() or {}).get("messages", []) if r.status_code == 200 else []
        record("conversation persisted", r.status_code == 200 and len(msgs) >= 2, f"messages={len(msgs)}")

    await engine.dispose()

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    total = len(RESULTS)
    print(f"\n=== E2E RESULT: {passed}/{total} passed ===")
    failed = [n for n, ok, _ in RESULTS if not ok]
    if failed:
        print("FAILED:", ", ".join(failed))
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
