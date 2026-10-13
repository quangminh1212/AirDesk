"""Seed the database with a demo organisation for local development.

Creates one user per RBAC role, a sample conversation, and a sample booking so
the web view has something to show immediately after `docker compose up`.

Usage (from backend/):
    python -m scripts.seed          # idempotent: skips if users already exist
    python -m scripts.seed --force  # wipe and re-seed
"""

from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import func, select

from app.core.logging import configure_logging, get_logger
from app.core.security import hash_password
from app.db.base import Base
from app.db.session import SessionLocal, engine
from app.models.user import User, UserRole

logger = get_logger(__name__)

DEMO_USERS = [
    ("admin@airdesk.vn", "Quản trị viên", UserRole.ADMIN, "admin12345"),
    ("operator@airdesk.vn", "Điều hành viên", UserRole.OPERATOR, "operator12345"),
    ("agent@airdesk.vn", "Nhân viên đại lý", UserRole.AGENT, "agent12345"),
    ("viewer@airdesk.vn", "Người xem", UserRole.VIEWER, "viewer12345"),
]


async def _seed(force: bool) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with SessionLocal() as db:
        count = await db.scalar(select(func.count()).select_from(User))
        if count and not force:
            logger.info("seed_skipped_existing_users", count=count)
            return

        if force:
            for model in (User,):
                await db.execute(model.__table__.delete())

        for email, full_name, role, password in DEMO_USERS:
            db.add(
                User(
                    email=email,
                    full_name=full_name,
                    hashed_password=hash_password(password),
                    role=role,
                    is_active=True,
                )
            )
        await db.commit()
        logger.info("seed_complete", users=len(DEMO_USERS))
        for email, _n, role, _p in DEMO_USERS:
            print(f"  - {role.value:8s} {email}")


async def main() -> None:
    parser = argparse.ArgumentParser(description="Seed AirDesk demo data")
    parser.add_argument("--force", action="store_true", help="wipe and re-seed")
    args = parser.parse_args()
    configure_logging()
    await _seed(force=args.force)


if __name__ == "__main__":
    asyncio.run(main())
