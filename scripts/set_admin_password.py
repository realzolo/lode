"""One-off operational script: ensure the demo admin user can log in.

Migration 0002 added the ``password_hash`` column, but earlier seed runs stored
the hash as an unmapped Python attribute that was never persisted (the column
stayed NULL). This script idempotently sets the password for the seed admin so
the UI can authenticate. Safe to re-run.

    .venv/bin/python3 scripts/set_admin_password.py
"""

from __future__ import annotations

import asyncio

from sqlalchemy import select

from lode.db.models.user import User
from lode.db.session import AsyncSessionLocal
from lode.security import hash_password

SEED_ADMIN_EMAIL = "admin@lode.local"
SEED_ADMIN_PASSWORD = "lode"


async def main() -> None:
    async with AsyncSessionLocal() as session:
        user = (
            await session.execute(select(User).where(User.email == SEED_ADMIN_EMAIL))
        ).scalars().first()
        if user is None:
            user = User(
                email=SEED_ADMIN_EMAIL,
                name="Seed Admin",
                role="admin",
                status="active",
            )
            session.add(user)
        if user.password_hash is None:
            user.password_hash = hash_password(SEED_ADMIN_PASSWORD)
            await session.commit()
            print(f"set password for {user.email}")
        else:
            print(f"password already set for {user.email}")


if __name__ == "__main__":
    asyncio.run(main())
