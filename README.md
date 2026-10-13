# AirDesk

AI operations desk for an airline ticketing & logistics agency. AirDesk runs an
**LLM-powered agent** — any model served through a LiteLLM gateway — that handles
customer care end to end: answering flight
enquiries, processing email and documents, tracking follow-up work, producing
operations reports, and **escalating to a human operator whenever it is not
confident or a quality check fails**.

It is built to be run, demoed, and reviewed — not just read.

---

## What it actually does

| Capability | Where it lives | Status |
|---|---|---|
| Conversational flight booking agent (Vietnamese/English) | `backend/app/agents/runner.py` | Working, live LLM |
| Tool calling: flight search, booking, escalation | `backend/app/agents/tools.py` | Working, live LLM |
| **Automated quality checks on every agent reply** | `backend/app/agents/qa.py` | Working |
| **Human hand-off on low confidence or failed QA** | `runner.py`, `api/v1/agent.py` | Working |
| Email ingestion from a real IMAP mailbox | `app/integrations/imap.py`, `app/services/email_ingest.py` | Working |
| Document parsing + LLM structured extraction | `app/services/documents.py` | Working, live LLM |
| Ask-a-question over an uploaded document | `api/v1/documents.py` | Working, live LLM |
| Task tracking with follow-up and completion | `api/v1/tasks.py` | Working |
| Operations reporting & aggregation | `api/v1/reports.py` | Working |
| QA review dashboard API | `api/v1/qa.py` | Working |
| Zalo OA + Facebook Messenger webhooks | `app/integrations/messaging.py` | Working, signature-verified |
| React operations UI (12 pages) | `frontend/src` | Working, builds clean |

## Verification status (measured, not claimed)

```
backend:   188 tests passed, 0 failed, 0 errors, 0 skipped   (~80s)
           ruff check app tests -> All checks passed!
           OpenAPI: 42 paths across 12 routers
frontend:  tsc --noEmit   -> clean
           eslint .       -> clean
           vite build     -> clean, code-split, dist/index.html produced
end-to-end: 15/15 checks passed against the LIVE model gateway
```

The end-to-end suite (`backend/scripts/e2e_check.py`) is a real integration run:
it logs in over HTTP, sends a Vietnamese flight request to the live model,
asserts a real `search_flights` tool call came back with priced offers, confirms
the QA report was attached, uploads a `.docx` and checks live structured
extraction classified it as an airline ticket, and walks the task lifecycle.

**The test suite is hermetic.** `tests/conftest.py` installs autouse guards that
make constructing a real LLM gateway, or opening a non-loopback socket, fail
immediately. These guards were added after they exposed **16 tests that were
silently making billed live API calls** — see *Engineering notes* below.

---

## Quick start

### 1. Backend

```powershell
cd backend
python -m venv .venv                 # or: uv venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"              # or: uv pip install -e ".[dev]"

Copy-Item .env.example .env          # then fill in the values below
python -m alembic upgrade head       # create the schema
python scripts/seed.py               # demo users + sample data
uvicorn app.main:app --reload        # http://localhost:8000/docs
```

Minimum required in `.env`:

- `SECRET_KEY` — generate with `python -c "import secrets;print(secrets.token_urlsafe(48))"`
- `DATABASE_URL` — Postgres in production; SQLite is fine for a laptop demo
- `LLM_API_KEY` and `LLM_MODEL` — see *Model configuration*

Everything else is optional. Email, Zalo, Messenger, Amadeus and Duffel all
degrade gracefully when unconfigured — the app starts and the rest keeps working.

### 2. Frontend

```powershell
cd frontend
npm install
npm run dev                          # http://localhost:5173
```

### 3. Demo logins

Seeded by `backend/scripts/seed.py`:

| Email | Password | Role | Can do |
|---|---|---|---|
| admin@airdesk.vn | admin12345 | ADMIN | everything, incl. user management |
| operator@airdesk.vn | operator12345 | OPERATOR | resolve escalations, QA review, upload docs |
| agent@airdesk.vn | agent12345 | AGENT | chat, own tasks |
| viewer@airdesk.vn | viewer12345 | VIEWER | read-only |

Change or delete these before any real deployment.

### 4. Docker (Postgres + Redis + API + UI)

```powershell
docker compose up --build
```

---

## Model configuration

Every model call goes through **one** choke point: `app/core/llm.py`. Nothing
else talks to a provider, so retries, fallbacks, timeouts, token counting and
cost accounting are all in one place.

```bash
LLM_MODEL=openai/your-model-name
LLM_FALLBACK_MODELS=openai/your-cheaper-model
LLM_API_KEY=sk-...
LLM_API_BASE=http://your-gateway:4000/v1   # LiteLLM proxy; keys stay server-side
LLM_TIMEOUT_SECONDS=60
LLM_MAX_RETRIES=2
LLM_TEMPERATURE=0.2
```

`LLM_MODEL` uses LiteLLM's `provider/model` convention, but read the first
segment carefully: it selects **the wire protocol LiteLLM speaks to the
endpoint, not the model's vendor.** When `LLM_API_BASE` points at a gateway that
fronts many vendors, the prefix must be a protocol that gateway actually serves —
the model name after the slash is whatever that gateway publishes. Changing the
model is an `.env`-only edit; no code changes.

**A wrong prefix fails silently, which is why this is called out.** The gateway
returns a well-formed response whose `tool_calls` are empty, so the agent appears
to answer normally while never calling a single tool. `app/core/llm.py` now
raises a `ProviderError` naming the likely cause when a tool call arrives with a
blank name, and `scripts/e2e_check.py` asserts a real `search_flights` tool call
comes back — so a misconfigured prefix is caught by verification rather than by a
customer.

**Never commit a real `.env`.** It is gitignored, and `.env.example` contains no
secrets.

---

## How the safety net works

This is the part that matters for a business deploying an AI agent: **the agent
is not trusted to police itself.**

### 1. Every reply is checked before it is returned

`app/agents/qa.py` runs a set of deterministic checks over each turn and
produces a `QAReport` with a severity-ranked list of findings:

| Check | Catches |
|---|---|
| `check_empty_reply` | blank or whitespace-only replies |
| `check_tool_failures` | the agent used a tool and the tool failed |
| `check_unverified_booking_claim` | "đã đặt vé" / "đã xuất vé" with no verified booking behind it |
| `check_unsourced_prices` | a price quoted with no price from a tool result |
| `check_unsourced_pnr` | a PNR / ticket number that appears in no tool output |
| `check_missing_escalation` | the agent should have handed off and did not |
| `check_excessive_length` | runaway replies that cost tokens and confuse customers |

Verdicts are `PASS` / `REVIEW` / `FAIL`.

### 2. Failures escalate automatically

`FlightAgent` escalates to a human when **either** the model's self-reported
confidence is below `AGENT_CONFIDENCE_THRESHOLD` **or** QA returns ERROR or
CRITICAL. A QA-blocked turn gets priority 1 so it sorts to the top of the
operator queue. The reply still goes back to the caller — with the QA report
attached — so a human sees exactly what the agent said and why it was flagged.

### 3. The decision is recorded and reviewable

Escalations and QA verdicts are persisted, then surfaced through
`GET /api/v1/qa/turns`, `GET /api/v1/qa/overview` and
`GET /api/v1/agent/escalations`, which is what the UI's QA Review and
Escalations pages read. Nothing about the hand-off is implicit.

---

## Architecture

```
frontend/  React 18 + Vite + TS (strict) + Tailwind + react-query
   |
   v
backend/   FastAPI (async) + SQLAlchemy 2.x async + Pydantic v2
   |
   +-- api/v1/       12 routers, 42 paths
   +-- agents/       runner (ReAct loop) | tools | qa
   +-- services/     documents, email_ingest, booking, offer_cache, audit
   +-- integrations/ imap, smtp, messaging (Zalo/Messenger), signature verification
   +-- providers/    opensky, mock, amadeus, duffel
   +-- core/         llm (LiteLLM gateway), config, security, errors, logging
```

The agent is a **bounded** ReAct loop (`MAX_TOOL_ITERATIONS = 6`). It cannot spin
forever, and token/cost usage is accounted per message so spend is traceable.

### Error contract

The API does **not** use FastAPI's default error shape. Every error is:

```json
{ "error": { "code": "not_found", "message": "...", "details": {}, "request_id": "..." } }
```

The frontend parses exactly this (`src/lib/errors.ts`), so failures surface as
readable messages instead of `[object Object]`.

### Roles

`VIEWER (0) < AGENT (1) < OPERATOR (2) < ADMIN (3)`. Enforced server-side via
FastAPI dependencies — never only in the UI.

---

## Testing

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest -q          # 188 tests
.\.venv\Scripts\python.exe -m ruff check app tests
.\.venv\Scripts\python.exe scripts\e2e_check.py  # live LLM, needs network

cd ..\frontend
npx tsc --noEmit && npm run lint && npm run build
```

The suite runs hermetically on SQLite — no Postgres, no Redis, no network. All
date bucketing happens in Python rather than SQL `date_trunc`, so aggregations
behave identically on SQLite and Postgres.

---

## Engineering notes

Things worth knowing, including the trade-offs that were deliberately accepted.

**A real production bug was found and fixed.** `GET /agent/conversations/{id}`
returned **HTTP 500 for any conversation where the agent had used a tool**.
Pydantic's `from_attributes` validation walked the `Message.tool_calls` lazy
relationship, and SQLAlchemy raised `MissingGreenlet` because the implicit IO
happened outside the async context. The same defect existed a second time via
`ConversationDetail.messages`. Both endpoints now build their schemas from
explicitly fetched values, and `tests/test_conversation_detail.py` pins the
behaviour — including the failed-tool-call and ordering cases.

**16 tests were making billed live API calls.** An autouse guard was added that
refuses to construct a real `LiteLLMGateway` in tests. It immediately failed 16
tests that had been reaching the real provider through the `Gateway` dependency.
Fixed by giving the ASGI test client a stub gateway override and routing the
webhook handlers through the same injected dependency instead of calling
`get_llm_gateway()` directly. A second guard blocks non-loopback sockets. The
suite is now provably offline.

**Issuing tickets crashed the order flow (`MissingGreenlet`).** Promoted
bookings raised `sqlalchemy.exc.MissingGreenlet` from `_apply_tickets`. The cause
was not the `Ticket` model: a relationship that is never touched is absent from
the instance `__dict__`, so the *first* `booking.tickets.append(...)` triggers a
lazy collection load — and that implicit IO cannot run in an async session's
sync flush context. `passengers` and `events` only appeared to work because
something had appended to them earlier. Fixed by passing `passengers=[]`,
`tickets=[]`, `events=[]` to the `Booking(...)` constructor, so the collections
exist before any flush. The general rule now applied: in an async session, never
rely on an untouched collection being initialised, and after `commit()` never
touch a lazy relationship — re-`select()` with `selectinload()`.

**A ticketed booking must expose its tickets.** `BookingRead` carried no ticket
data, so the API could report `status: "ticketed"` while giving the operator no
way to see the document numbers — the actual artefact the customer flies on.
`BookingDetail` now includes `tickets: list[TicketRead]`, and the order test
asserts the stronger contract: the `mock` provider issues real numbers, so the
order is promoted to `ticketed` with `payment_status: paid`, every passenger has
a ticket with a non-empty `ticket_number` and `issued_at`, and **per-ticket fares
and taxes sum exactly to the order's `base_amount`/`taxes_amount`** (VND has no
minor unit, so this is an integer identity). `_apply_tickets` only counts
tickets carrying a real document number, so a booking cannot be marked `ticketed`
merely because the provider returned rows.

**The PDF text extractor is deliberately shallow.** It is a standard-library
parser, not a PDF interpreter. Two limitations to be honest about:

- It reads only string operands of `Tj`/`TJ`/`'`/`"`. There is **no
  `/ToUnicode` CMap or font-encoding handling** — text is decoded latin-1, so
  **Vietnamese diacritics in real PDFs will be mojibake'd**. Fixing this properly
  needs a real PDF library or a CMap lookup. Now that the demo files use
  text-based formats, this is the highest-value follow-up for Vietnamese input.
- Encrypted PDFs and streams needing filters other than `/FlateDecode` are not
  decoded; they surface as `needs_ocr=True` rather than being silently accepted.

  A raw-bytes fallback was explicitly removed because it made a genuine scanned
  document report `needs_ocr=False` while returning PDF layout commands as
  "text" — which would have silently skipped OCR. A test pins that behaviour.

**Oversize and unsupported uploads return 422**, not 413/415. `app/core/errors.py`
has no `PayloadTooLarge`/`UnsupportedMediaType` class and pins `ValidationError`
to 422. Messages are explicit, and the tests assert tolerant sets, so tightening
this later will not break them.

**Tests share one SQLite file path**, so running pytest twice concurrently in the
same checkout can cause spurious `StaleDataError`/`IntegrityError`. Run the suite
serially. Worth fixing before CI runs shards in parallel.

---

## Deployment checklist

Before going live:

- [ ] Set a strong `SECRET_KEY`; it is not defaulted in production
- [ ] Point `DATABASE_URL` at Postgres and run `alembic upgrade head`
- [ ] Set `WEBHOOK_REQUIRE_SIGNATURE=true` (the default — do not disable it)
- [ ] Configure `ZALO_APP_SECRET` / `FACEBOOK_APP_SECRET` so webhooks verify
- [ ] Set `CORS_ORIGINS` to your real frontend origin, not localhost
- [ ] Replace the seeded demo accounts
- [ ] Set `ENVIRONMENT=production` and `DEBUG=false`
- [ ] Review `BOOKING_AUTO_APPROVE_LIMIT_VND` — bookings above it need a human
- [ ] Configure Postgres and Redis backups
- [ ] Wire up log shipping; logs are structured JSON with request IDs
- [ ] Confirm `/api/v1/health` and `/api/v1/ready` are monitored
- [ ] Add a real PDF parser if Vietnamese-language PDFs will be ingested

## Data handling

- Fare data comes from sources that permit automated access (OpenSky is free and
  needs no login). Amadeus and Duffel are official APIs.
- Displayed fares are treated as **indicative** until re-checked with the
  provider; observed-at timestamps are retained.
- No bot protections are bypassed anywhere in this codebase.

## Licence

Internal project. Not licensed for redistribution.
