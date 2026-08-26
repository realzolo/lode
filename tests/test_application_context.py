from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select

from lode.db.models.application import (
    Application,
    ApplicationArchitectureContext,
    ApplicationServiceBinding,
    Service,
)
from lode.db.models.git import GitRepo
from lode.db.models.investigation import EvidenceArtifact
from lode.db.session import AsyncSessionLocal
from lode.engine.investigation_intake import create_investigation


async def test_investigation_freezes_masked_application_context() -> None:
    suffix = uuid.uuid4().hex
    async with AsyncSessionLocal() as session:
        application = Application(
            name=f"context-{suffix}", ingestion_topic=f"context-{suffix}"
        )
        session.add(application)
        await session.flush()
        repo = GitRepo(
            name=f"repo-{suffix}",
            repo_url=f"https://git.example.com/{suffix}.git",
            default_branch="main",
            scope="global",
            repo_type="other",
        )
        session.add(repo)
        await session.flush()
        service = Service(service_name=f"service-{suffix}", repo_id=repo.id)
        session.add(service)
        await session.flush()
        session.add(
            ApplicationServiceBinding(
                application_id=application.id,
                service_id=service.id,
                role="primary",
            )
        )
        session.add(
            ApplicationArchitectureContext(
                application_id=application.id,
                content="Publishes after commit; password=super-secret-value",
            )
        )
        await session.commit()

        investigation, _job = await create_investigation(
            session,
            application_id=application.id,
            trigger_signature=f"manual:{suffix}",
            source_type="manual",
            title="Checkout failed",
            severity="WARNING",
            occurred_at=datetime.now(UTC),
            output_language="en",
            error_name="CheckoutError",
            error_message="checkout failed",
            service_name=service.service_name,
        )
        await session.commit()

        context = (
            await session.execute(
                select(EvidenceArtifact).where(
                    EvidenceArtifact.investigation_id == investigation.id,
                    EvidenceArtifact.artifact_type == "application_context",
                )
            )
        ).scalar_one()
        assert "Publishes after commit" in context.redacted_excerpt
        assert "super-secret-value" not in context.redacted_excerpt
        assert context.metadata_["entry_count"] == 1
        assert context.metadata_["trust"] == "untrusted_background"

        await session.delete(application)
        await session.flush()
        await session.delete(service)
        await session.delete(repo)
        await session.commit()
