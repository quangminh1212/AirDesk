# AirDesk backend

FastAPI service for the AirDesk AI operations desk. See the
[project README](../README.md) for the full picture — architecture, the safety
net around the agent, and the deployment checklist.

## Layout

```
app/
  api/v1/        12 routers (auth, agent, flights, bookings, tasks,
                 documents, inbox, qa, reports, webhooks, system)
  agents/        runner.py (bounded ReAct loop) | tools.py | qa.py
  core/          llm.py (the single LLM choke point), config, security,
                 errors, logging, rate limiting
  db/            async engine + session
  integrations/  imap, smtp, messaging (Zalo/Messenger), signatures
  models/        SQLAlchemy 2.x models
  providers/     opensky, mock, amadeus, duffel
  schemas/       Pydantic v2 request/response models
  services/      documents, email_ingest, booking, offer_cache, audit
alembic/         migrations
scripts/         seed.py, e2e_check.py
tests/           188 tests, fully hermetic (no network, no Postgres)
```

## Running

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
Copy-Item .env.example .env      # fill in SECRET_KEY and LLM_*
python -m alembic upgrade head
python scripts/seed.py
uvicorn app.main:app --reload
```

Docs at <http://localhost:8000/docs>.

## Testing

```powershell
.\.venv\Scripts\python.exe -m pytest -q            # 188 tests, offline
.\.venv\Scripts\python.exe -m ruff check app tests  # lint
.\.venv\Scripts\python.exe scripts\e2e_check.py     # live LLM, needs network
```

`tests/conftest.py` installs autouse guards that refuse to construct a real
LLM gateway or open a non-loopback socket, so the suite can never bill a live
provider. `scripts/e2e_check.py` is deliberately outside `tests/` because it is
the one thing that *should* talk to the real model.

## Notes for contributors

- Every model call must go through `app/core/llm.py`. Do not import a provider
  SDK anywhere else.
- Endpoints take the gateway via the `Gateway` dependency so tests can
  substitute it. Calling `get_llm_gateway()` directly inside a route makes it
  untestable and is how a live-network leak reached the suite once.
- Build response schemas from explicitly fetched values, not by validating ORM
  objects whose schema declares a relationship — lazy loading outside the
  greenlet context raises `MissingGreenlet` and returns 500.
- Bucket dates in Python, never with SQL `date_trunc`; the tests run on SQLite
  while production runs on Postgres.
