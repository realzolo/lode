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
    ModelDeployment,
    ModelPolicyRevision,
    User,
    Workspace,
    WorkspaceModelBinding,
    WorkspaceRepositoryBinding,
)
from lode.db.session import AsyncSessionLocal
from lode.security import hash_password

SEED_ADMIN_PASSWORD = "lode"
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

        admin = User(
            email="admin@lode.local",
            name="Seed Admin",
            role="admin",
            status="active",
            password_hash=hash_password(SEED_ADMIN_PASSWORD),
        )
        session.add(admin)
        await session.flush()

        workspace = Workspace(
            name=WORKSPACE_NAME,
            ingestion_topic=INGESTION_TOPIC,
            created_by=admin.id,
        )
        session.add(workspace)
        await session.flush()

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
            provider_kind="openai_compatible",
            base_url="https://example.invalid/v1",
            credential_ciphertext="seed-disabled-ciphertext",
            state="disabled",
            data_processing_policy_revision="seed-v1",
            data_residency="unspecified",
            retention_mode="provider_default",
        )
        session.add(provider)
        await session.flush()
        deployment = ModelDeployment(
            provider_account_id=provider.id,
            provider_model_id="seed-model",
            display_name="Seed model (disabled)",
            max_input_tokens=32768,
            max_output_tokens=4096,
            tokenizer_id="cl100k_base",
            provider_revision="seed-v1",
            quality_baseline_revision="phase0-deterministic-oracle",
            cost_policy_revision="seed-v1",
            rate_limit_policy_revision="seed-v1",
            state="disabled",
        )
        session.add(deployment)
        await session.flush()
        binding = WorkspaceModelBinding(
            workspace_id=workspace.id,
            model_deployment_id=deployment.id,
            execution_classes=["latency_optimized"],
            allowed_roles=["planner", "synthesizer", "verifier"],
            max_calls=10,
            max_input_tokens=24576,
            max_output_tokens=4096,
            max_cost_per_call=Decimal("0"),
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
            eligible_binding_revisions=[binding.id],
            role_policies={
                "planner": {"execution_class": "latency_optimized"},
                "synthesizer": {"execution_class": "latency_optimized"},
                "verifier": {"execution_class": "latency_optimized", "independent": True},
            },
            budget_policy={"max_calls": 10, "max_cost": "0"},
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
