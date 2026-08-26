"""Seed a fresh V1 database with one application and queued investigations."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import UTC, datetime

from sqlalchemy import select

from lode.db.models.application import Application, ApplicationServiceBinding, Service
from lode.db.models.git import GitRepo
from lode.db.models.investigation import Investigation
from lode.db.models.user import User
from lode.db.session import AsyncSessionLocal
from lode.engine.investigation_intake import create_investigation
from lode.security import hash_password

SEED_ADMIN_PASSWORD = "lode"
APP_NAME = "checkout-service"
TOPIC = "alert.checkout"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("lode.seed")


async def main() -> None:
    async with AsyncSessionLocal() as session:
        app = (
            await session.execute(select(Application).where(Application.name == APP_NAME))
        ).scalars().first()
        if app is None:
            user = User(
                email="admin@lode.local",
                name="Seed Admin",
                role="admin",
                status="active",
                password_hash=hash_password(SEED_ADMIN_PASSWORD),
            )
            session.add(user)
            await session.flush()
            app = Application(name=APP_NAME, ingestion_topic=TOPIC, created_by=user.id)
            session.add(app)
            await session.flush()
            repo = GitRepo(
                name="checkout-service",
                repo_url="https://example.com/checkout-service.git",
                default_branch="main",
                repo_type="other",
            )
            session.add(repo)
            await session.flush()
            service = Service(service_name="checkout-service", repo_id=repo.id)
            session.add(service)
            await session.flush()
            session.add(
                ApplicationServiceBinding(
                    application_id=app.id, service_id=service.id, role="primary"
                )
            )
            await session.commit()
            logger.info("created application id=%s topic=%s", app.id, TOPIC)

        existing = (
            await session.execute(
                select(Investigation.id).where(Investigation.application_id == app.id).limit(1)
            )
        ).scalar_one_or_none()
        if existing is not None:
            logger.info("V1 investigations already exist; skipping")
            return

        samples = [
            {
                "title": "Payment order creation failed",
                "severity": "CRITICAL",
                "name": "PAYMENT_FAILED",
                "message": "Payment creation failed",
                "fields": {
                    "providerCode": "Payssion",
                    "methodCode": "enets_sg",
                    "gatewayCode": "PAYMENT_FAILED",
                    "httpStatus": 200,
                },
            },
            {
                "title": "Gateway response latency exceeded",
                "severity": "WARNING",
                "name": "GatewayTimeout",
                "message": "Provider response exceeded 2000ms",
                "fields": {"providerCode": "Adyen", "httpStatus": 504},
            },
        ]
        for sample in samples:
            signature = hashlib.sha256(
                f"{sample['name']}\n{sample['message']}".encode()
            ).hexdigest()
            await create_investigation(
                session,
                application_id=app.id,
                trigger_signature=signature,
                source_type="manual",
                title=sample["title"],
                severity=sample["severity"],
                occurred_at=datetime.now(UTC),
                output_language="zh",
                error_name=sample["name"],
                error_message=sample["message"],
                error_properties={"contract": sample["fields"]},
                fields=sample["fields"],
                source_metadata={"seed": True},
            )
        await session.commit()
        logger.info("created %d queued V1 investigations", len(samples))


if __name__ == "__main__":
    asyncio.run(main())
