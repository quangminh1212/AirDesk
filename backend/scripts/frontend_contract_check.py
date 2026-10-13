"""Verify the frontend<->backend contract for the airline-ticket screens.

Hermetic (mock flight provider, SQLite, no LLM). Asserts every field the
frontend types read actually exists in the live response, so a renamed
schema field cannot silently render as `undefined` in the UI.

Run: .\\.venv\\Scripts\\python.exe scripts/frontend_contract_check.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./_contract.db")
os.environ.setdefault("FLIGHT_PROVIDERS", "mock")
os.environ.setdefault("SQL_ECHO", "false")

from httpx import ASGITransport, AsyncClient  # noqa: E402

import app.models  # noqa: F401, E402
from app.core.security import hash_password  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models.user import User, UserRole  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def _force_utf8_stdout() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def record(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  -- {detail}" if detail else ""))


def has_keys(obj: object, keys: list[str], label: str) -> None:
    """Assert `obj` is a dict exposing every key (present, even if null)."""
    if not isinstance(obj, dict):
        record(f"{label} is object", False, f"got {type(obj).__name__}")
        return
    missing = [k for k in keys if k not in obj]
    detail = f"missing={missing}" if missing else "ok"
    record(f"{label} exposes frontend fields", not missing, detail)


async def main() -> int:
    _force_utf8_stdout()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    async with SessionLocal() as db:
        db.add(
            User(
                email="contract@airdesk.vn",
                full_name="Contract Admin",
                hashed_password=hash_password("secret123"),
                role=UserRole.ADMIN,
                is_active=True,
            )
        )
        await db.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://contract") as c:
        r = await c.post(
            "/api/v1/auth/login",
            json={"email": "contract@airdesk.vn", "password": "secret123"},
        )
        record("login", r.status_code == 200, f"HTTP {r.status_code}")
        if r.status_code != 200:
            return 1
        headers = {"Authorization": f"Bearer {r.json()['access_token']}"}

        # --- providers (frontend flightApi.providers -> ProviderList) --------
        r = await c.get("/api/v1/flights/providers", headers=headers)
        body = r.json() if r.status_code == 200 else {}
        record("GET /flights/providers", r.status_code == 200, f"HTTP {r.status_code}")
        has_keys(body, ["providers"], "ProviderList")
        if isinstance(body, dict):
            provs = body.get("providers")
            record("providers is list", isinstance(provs, list), f"{provs}")

        # --- search (frontend FlightSearchRequest -> FlightOffer[]) ---------
        search_payload = {
            "origin": "SGN",
            "destination": "HAN",
            "departure_date": "2026-12-01",
            "adults": 1,
            "children": 0,
            "infants": 0,
            "cabin": "economy",
            "currency": "VND",
            "non_stop": False,
            "max_results": 10,
            "provider": "mock",
        }
        r = await c.post("/api/v1/flights/search", headers=headers, json=search_payload)
        ok = r.status_code == 200
        offers = r.json() if ok else []
        record(
            "POST /flights/search",
            ok,
            f"HTTP {r.status_code} body={json.dumps(r.json())[:400] if not ok else ''}",
        )
        n_offers = len(offers) if isinstance(offers, list) else "n/a"
        got = isinstance(offers, list) and len(offers) > 0
        record("search returned offers", got, f"n={n_offers}")
        if isinstance(offers, list) and offers:
            offer = offers[0]
            has_keys(
                offer,
                [
                    "offer_id",
                    "provider",
                    "origin",
                    "destination",
                    "departure_at",
                    "return_at",
                    "airline_code",
                    "airline_name",
                    "total_amount",
                    "currency",
                    "stops",
                    "total_duration_minutes",
                    "seats_available",
                    "segments",
                ],
                "FlightOffer",
            )
            segs = offer.get("segments") or []
            if segs:
                has_keys(
                    segs[0],
                    [
                        "origin",
                        "destination",
                        "departure_at",
                        "arrival_at",
                        "carrier_code",
                        "carrier_name",
                        "flight_number",
                        "duration_minutes",
                        "aircraft",
                        "cabin",
                    ],
                    "Segment",
                )
            else:
                record("Segment present", False, "segments empty")

            # --- price the shopped offer (frontend selectOffer) ------------
            r = await c.post(
                f"/api/v1/flights/offers/{offer['provider']}/{offer['offer_id']}/price",
                headers=headers,
            )
            ok = r.status_code == 200
            priced = r.json() if ok else {}
            record(
                "POST /flights/offers/{p}/{id}/price",
                ok,
                f"HTTP {r.status_code} body={json.dumps(priced)[:200] if not ok else ''}",
            )
            if ok:
                has_keys(
                    priced,
                    ["offer_id", "provider", "is_bookable", "kind", "total_amount"],
                    "Priced offer",
                )
                record(
                    "priced offer is bookable",
                    priced.get("is_bookable") is True,
                    f"kind={priced.get('kind')} is_bookable={priced.get('is_bookable')}",
                )
                offer = priced

            # --- create booking (frontend BookingCreateRequest) -------------
            booking_payload = {
                "offer_id": offer["offer_id"],
                "provider": offer["provider"],
                "passengers": [
                    {
                        "passenger_type": "adult",
                        "first_name": "VAN A",
                        "last_name": "NGUYEN",
                        "date_of_birth": None,
                        "nationality": "VN",
                        "document_type": "PASSPORT",
                        "document_number": "B1234567",
                        "email": None,
                        "phone": None,
                    }
                ],
                "contact_email": "khach@example.com",
                "contact_phone": "0900000000",
                "cabin": "economy",
            }
            r = await c.post("/api/v1/bookings", headers=headers, json=booking_payload)
            ok = r.status_code in (200, 201)
            booking = r.json() if ok else {}
            detail = f"HTTP {r.status_code} body={json.dumps(booking)[:160]}" if not ok else ""
            record("POST /bookings", ok, detail)
            if ok:
                has_keys(
                    booking,
                    ["id", "reference", "provider", "status", "currency",
                     "total_amount", "created_at"],
                    "Booking (create)",
                )
                bid = booking["id"]

                # --- list (frontend bookingApi.list -> Page|Booking[]) ------
                list_params = {"page": 1, "page_size": 20}
                r = await c.get("/api/v1/bookings", headers=headers, params=list_params)
                ok = r.status_code == 200
                listed = r.json() if ok else None
                record("GET /bookings", ok, f"HTTP {r.status_code}")
                items = listed.get("items") if isinstance(listed, dict) else listed
                record("bookings list is array", isinstance(items, list), f"{type(items).__name__}")
                if isinstance(items, list) and items:
                    has_keys(
                        items[0],
                        ["id", "reference", "status", "provider", "total_amount",
                         "currency", "contact_email", "created_at"],
                        "Booking (list row)",
                    )
                    # Presence of the key is not enough — POST /bookings accepts
                    # the contact but used to drop it, so the column rendered
                    # empty. Assert the value actually survived the round trip.
                    record(
                        "Booking (list row) persists contact",
                        items[0].get("contact_email") == "khach@example.com",
                        f"contact_email={items[0].get('contact_email')!r}",
                    )

                # --- detail (frontend bookingApi.get -> BookingDetail) ------
                r = await c.get(f"/api/v1/bookings/{bid}", headers=headers)
                ok = r.status_code == 200
                detail = r.json() if ok else {}
                record("GET /bookings/{id}", ok, f"HTTP {r.status_code}")
                if ok:
                    has_keys(detail, ["passengers", "events", "tickets"], "BookingDetail")
                    if isinstance(detail.get("passengers"), list) and detail["passengers"]:
                        has_keys(
                            detail["passengers"][0],
                            [
                                "id",
                                "passenger_type",
                                "first_name",
                                "last_name",
                                "document_number",
                                "email",
                                "phone",
                            ],
                            "PassengerRead",
                        )
                    else:
                        record("passengers populated", False, "empty")
                    tickets = detail.get("tickets") or []
                    if tickets:
                        has_keys(
                            tickets[0],
                            [
                                "id",
                                "ticket_number",
                                "segment_index",
                                "flight_number",
                                "status",
                                "fare_amount",
                                "tax_amount",
                            ],
                            "TicketRead",
                        )
                    else:
                        record("tickets populated", False, "empty")
                    events = detail.get("events") or []
                    if events:
                        ev_keys = ["id", "event_type", "occurred_at", "actor"]
                        has_keys(events[0], ev_keys, "OrderEventRead")
                    else:
                        record("events populated", False, "empty")

        # --- reports used by ReportsPage -------------------------------------
        r = await c.get("/api/v1/reports/overview", headers=headers)
        record("GET /reports/overview", r.status_code == 200, f"HTTP {r.status_code}")
        r = await c.get("/api/v1/reports/volume", headers=headers, params={"days": 30})
        pts = (r.json() or {}).get("points", []) if r.status_code == 200 else []
        record("GET /reports/volume", r.status_code == 200 and len(pts) == 30, f"points={len(pts)}")
        r = await c.get("/api/v1/reports/agent-performance", headers=headers, params={"days": 30})
        record("GET /reports/agent-performance", r.status_code == 200, f"HTTP {r.status_code}")

    await engine.dispose()

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    total = len(RESULTS)
    print(f"\n=== FRONTEND CONTRACT: {passed}/{total} passed ===")
    failed = [n for n, ok, _ in RESULTS if not ok]
    if failed:
        print("FAILED:", ", ".join(failed))
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
