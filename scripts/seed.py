"""Seed a fresh database with only current control-plane objects."""

from __future__ import annotations

import asyncio
import logging
from decimal import Decimal

from sqlalchemy import select

from lode.db.models import (
    AIProviderAccount,
    ContextPolicyRevision,
    GitRepository,
    ProviderAccountModel,
    ModelPolicyRevision,
    InvestigationPolicyRevision,
    User,
    Workspace,
    WorkspaceModelBinding,
    WorkspaceRepositoryBinding,
)
from lode.model_catalog import require_model
from lode.application.investigation_policy import investigation_policy_columns
from lode.db.session import AsyncSessionLocal
WORKSPACE_NAME = "Checkout"
INGESTION_TOPIC = "incident.checkout.v1"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("lode.seed")


async def main() -> None:
    async with AsyncSessionLocal() as session:
        existing = (
            await session.execute(
                select(Workspace).where(Workspace.ingestion_topic == INGESTION_TOPIC)
            )
        ).scalar_one_or_none()
        if existing is not None:
            logger.info("V1 Workspace already exists: id=%s", existing.id)
            return

        admin = await session.scalar(select(User).where(User.username == "admin"))
        if admin is None or not admin.is_system_admin:
            raise RuntimeError("the initial migration did not create the system administrator")

        workspace = Workspace(
            name=WORKSPACE_NAME,
            ingestion_topic=INGESTION_TOPIC,
            created_by=admin.id,
        )
        session.add(workspace)
        await session.flush()
        investigation_policy = InvestigationPolicyRevision(
            workspace_id=workspace.id,
            profile="balanced",
            **investigation_policy_columns("balanced"),
            revision=1,
            created_by=admin.id,
        )
        session.add(investigation_policy)
        await session.flush()
        workspace.investigation_policy_revision_id = investigation_policy.id

        repository = GitRepository(
            name="checkout",
            repo_url="https://example.com/checkout.git",
            repo_type="other",
            default_branch="main",
            scope="global",
        )
        session.add(repository)
        await session.flush()
        session.add(
            WorkspaceRepositoryBinding(
                workspace_id=workspace.id,
                repository_id=repository.id,
                role="runtime_source",
                priority=0,
                description="Seed runtime source",
            )
        )

        provider = AIProviderAccount(
            name="seed-openai-compatible",
            protocol_id="openai.responses.v1",
            base_url="https://example.invalid/v1",
            credential_ciphertext="seed-disabled-ciphertext",
            state="disabled",
        )
        session.add(provider)
        await session.flush()
        profile = require_model("openai", "openai.responses.v1", "gpt-5.6-sol")
        deployment = ProviderAccountModel(
            provider_account_id=provider.id,
            provider_model_id=profile.model_id,
            catalog_revision=profile.catalog_revision,
            catalog_profile_hash=profile.profile_hash,
            state="disabled",
        )
        session.add(deployment)
        await session.flush()
        binding = WorkspaceModelBinding(
            workspace_id=workspace.id,
            provider_account_model_id=deployment.id,
            execution_classes=["latency_optimized"],
            allowed_roles=["planner", "synthesizer", "verifier"],
            max_calls=10,
            max_cost_per_call=Decimal(0),
            timeout_ms=120000,
            allowed_data_classes=["masked_incident", "source_code"],
            max_context_utilization=Decimal("0.75"),
            state="disabled",
        )
        session.add(binding)
        await session.flush()

        context_policy = ContextPolicyRevision(
            workspace_id=workspace.id,
            pinned_evidence_kinds=["normalized_input", "counter_evidence"],
            compression_levels=["full", "summary", "reference"],
            minimum_output_tokens=4096,
            provider_safety_margin_tokens=1024,
            revision=1,
        )
        session.add(context_policy)
        await session.flush()
        model_policy = ModelPolicyRevision(
            workspace_id=workspace.id,
            eligible_bindings=[{"binding_id": binding.id, "revision": binding.revision}],
            role_policies={
                "planner": {"execution_class": "latency_optimized"},
                "synthesizer": {"execution_class": "latency_optimized"},
                "verifier": {"execution_class": "latency_optimized", "independent": True},
            },
            context_policy_revision_id=context_policy.id,
            verifier_policy={"required_for_confirmed": True},
            revision=1,
        )
        session.add(model_policy)
        await session.flush()
        workspace.model_policy_revision_id = model_policy.id

        await session.commit()
        logger.info("created V1 Workspace id=%s topic=%s", workspace.id, INGESTION_TOPIC)


if __name__ == "__main__":
    asyncio.run(main())
