# AirDesk architecture

## Request flow: a customer message

```
Zalo / Messenger webhook   or   POST /api/v1/agent/chat
        |
        |  (webhooks: HMAC signature verified first, 401 on mismatch)
        v
   FlightAgent.run(message)
        |
        +--> 1. load/create Conversation, persist the inbound Message
        |
        +--> 2. bounded ReAct loop (MAX_TOOL_ITERATIONS = 6)
        |        |
        |        +--> LiteLLMGateway.complete(messages, tools)
        |        |       single choke point; retries, fallbacks, cost accounting
        |        |
        |        +--> if the model requests a tool:
        |        |       execute -> persist ToolCallRecord (name, args, result,
        |        |       success, error, duration) -> feed result back
        |        |
        |        +--> else: final answer, exit loop
        |
        +--> 3. QAEvaluator.evaluate(subject)  --> QAReport
        |        deterministic checks; verdict PASS | REVIEW | FAIL
        |
        +--> 4. escalation decision
        |        confidence < threshold        -> escalate
        |        QA verdict in {ERROR, CRITICAL} -> escalate (priority 1)
        |
        +--> 5. persist outbound Message with meta={"qa": ...}
        |
        v
   AgentReply { reply, offers, booking, qa, conversation_id }
```

The caller always receives a reply. Escalation is **additive**: it opens a human
task and flags the turn, it does not replace the response. An operator sees
exactly what was said and why it was flagged.

## Why the QA layer exists

An LLM cannot be the sole judge of its own correctness — a confident wrong answer
is the default failure mode. So verification is deterministic and external:

- `check_unverified_booking_claim` catches "đã đặt vé" (ticket booked) with no
  booking record behind it.
- `check_unsourced_prices` catches a quoted fare that appears in no tool result.
- `check_unsourced_pnr` catches a ticket number that appears in no tool output.
- `check_tool_failures` catches a reply built on a failed tool call.
- `check_missing_escalation` catches a case that should have been handed off.

Checks are pure functions over a `QASubject`, so they are unit-testable without
a database or a model. Adding a check is a small, reviewable change.

## Layering rules

| Layer | May depend on | Must not |
|---|---|---|
| `api/v1/*` | schemas, services, agents, core | contain business logic or raw SQL |
| `agents/*` | services, core.llm, integrations | import from `api/` |
| `services/*` | models, integrations, core | import from `api/` or `agents/` |
| `integrations/*` | core, stdlib, provider SDKs | import from `api/` or `services/` |
| `models/*` | db.base | import anything above it |
| `schemas/*` | pydantic only | import models |

Dependencies flow one way. `core/llm.py` is the only module that talks to a model
provider.

## The LLM gateway

`app/core/llm.py` is the single place a provider is contacted.

- `get_llm_gateway()` returns a process-wide `LiteLLMGateway`.
- `complete(...)` returns a uniform `LLMResult` (content, tool calls, model,
  token counts, cost).
- Retries and the `LLM_FALLBACK_MODELS` chain live here, so no caller implements
  its own retry logic.
- FastAPI endpoints receive it via the `Gateway` dependency, which makes it
  substitutable in tests — this is how the suite stays offline.

**Tests must never construct a real gateway.** `tests/conftest.py` installs an
autouse fixture that makes `LiteLLMGateway.__init__` raise, plus a socket guard.
This is not decoration: it caught 16 tests that were silently billing a live
provider.

## Data model

Core tables: `users`, `conversations`, `messages`, `tool_calls`, `escalations`,
`bookings`, `offer_cache`, `tasks`, `documents`, `inbound_emails`, `audit_log`.

- `messages.meta` holds the QA report for a turn as JSON. Querying by verdict
  therefore happens in Python over a bounded scan (`api/v1/qa.py`) rather than in
  SQL, which keeps it portable between SQLite and Postgres.
- `documents.extracted` holds the LLM's structured extraction.
- `tool_calls` is a child of `messages`, one row per invocation.

## Async correctness

The suite runs on SQLite but production runs on Postgres, so aggregations bucket
dates **in Python**, never with `date_trunc`.

Lazy relationship loading is a real hazard here. Loading a relationship outside
SQLAlchemy's greenlet context raises `MissingGreenlet` at serialization time —
which is exactly how `GET /agent/conversations/{id}` was returning 500 whenever a
message had tool calls. Endpoints fetch related rows explicitly and construct
response schemas from plain values rather than validating ORM objects whose
schema declares a relationship.

## Multi-channel inbound

| Channel | Entry | Verified by |
|---|---|---|
| Web | `POST /agent/chat` | JWT |
| Zalo OA | `POST /webhooks/zalo` | `sha256(app_id + raw_body + timestamp + app_secret)` |
| Messenger | `POST /webhooks/messenger` | `X-Hub-Signature-256` HMAC-SHA256 |
| Email | `POST /inbox/poll` | operator auth; pulls via IMAP |

Signature comparison uses `hmac.compare_digest`. With
`WEBHOOK_REQUIRE_SIGNATURE=true` (the default) an unverifiable request is
rejected with 401 and an empty signature can never pass. A bad signature on an
otherwise valid payload is rejected; a failure *inside* handling is swallowed so
the provider still receives HTTP 200 and does not enter a retry storm.

## Testing strategy

Four levels, all offline:

1. **Unit** — QA checks, PDF/DOCX extraction, pricing, provider parsing.
2. **API** — real ASGI requests through the full middleware stack, SQLite,
   stubbed gateway.
3. **Regression** — pinned to specific fixed bugs (`test_conversation_detail.py`,
   `test_offer_order.py`).
4. **End-to-end** (`scripts/e2e_check.py`, excluded from pytest) — a genuine run
   against the live model over HTTP, proving the wiring is not just mocked.

Level 4 matters: mocked tests proved the app worked while
`GET /agent/conversations/{id}` was returning 500 in reality.

## Known gaps

Ordered by value:

1. **No `/ToUnicode` CMap handling** — Vietnamese diacritics in real PDFs will be
   mojibake'd. Needs a real PDF parser or a CMap lookup; the rest of the document
   pipeline is unaffected.
2. **Shared test DB path** — concurrent pytest runs in one checkout race on
   `/tmp/airdesk_test.db`. Fix before parallel CI sharding.
3. **No background scheduler wired** — `EMAIL_POLL_ENABLED` exists and
   `POST /inbox/poll` works, but nothing calls it on a timer yet.
4. **Oversize uploads return 422, not 413** — no `PayloadTooLarge` error class in
   `core/errors.py` yet.
