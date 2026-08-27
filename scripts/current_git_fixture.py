"""Current-schema Git entitlement fixtures for local verification scripts."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select

from lode.db.models import (
    GitAccount,
    GitAccountCredentialRevision,
    GitAccountRepositoryAccess,
    GitRepository,
    WorkspaceGitAccountGrant,
    WorkspaceGitRepositoryEntitlement,
)
from lode.crypto import encrypt_secret
from lode.git_accounts import (
    GitAccountSecret,
    credential_identity_hash,
    encode_credential_secret,
)


FIXTURE_ADAPTER_ID = "local-fixture"
FIXTURE_ENDPOINT_HASH = "d" * 64


async def ensure_repository_entitlement(session, workspace_id: int, repository: GitRepository) -> int:
    account = await session.scalar(
        select(GitAccount).where(
            GitAccount.adapter_id == FIXTURE_ADAPTER_ID,
            GitAccount.endpoint_identity_hash == FIXTURE_ENDPOINT_HASH,
            GitAccount.external_account_id == str(workspace_id),
        )
    )
    if account is None:
        account = GitAccount(
            adapter_id=FIXTURE_ADAPTER_ID,
            api_url="https://git-fixture.invalid/api",
            endpoint_identity_hash=FIXTURE_ENDPOINT_HASH,
            name=f"fixture-{workspace_id}",
            external_account_id=str(workspace_id),
            external_account_login=f"fixture-{workspace_id}",
            account_url=f"https://git-fixture.invalid/accounts/{workspace_id}",
            verification_status="healthy",
            verified_at=datetime.now(UTC),
        )
        session.add(account)
        await session.flush()
        secret = GitAccountSecret(username="fixture", token=f"fixture-token-{workspace_id}")
        credential = GitAccountCredentialRevision(
            account_connection_id=account.id,
            revision=1,
            secret_ciphertext=encrypt_secret(encode_credential_secret(secret)) or "",
            credential_identity_hash=credential_identity_hash(secret),
        )
        session.add(credential)
        await session.flush()
        account.current_credential_revision_id = credential.id
        account.revision += 1

    access = await session.scalar(
        select(GitAccountRepositoryAccess).where(
            GitAccountRepositoryAccess.account_connection_id == account.id,
            GitAccountRepositoryAccess.repository_id == repository.id,
        )
    )
    if access is None:
        session.add(
            GitAccountRepositoryAccess(
                account_connection_id=account.id,
                repository_id=repository.id,
                access_level="read",
                state="available",
                last_seen_at=datetime.now(UTC),
            )
        )

    grant = await session.scalar(
        select(WorkspaceGitAccountGrant).where(
            WorkspaceGitAccountGrant.workspace_id == workspace_id,
            WorkspaceGitAccountGrant.account_connection_id == account.id,
        )
    )
    if grant is None:
        grant = WorkspaceGitAccountGrant(
            workspace_id=workspace_id,
            account_connection_id=account.id,
            repository_scope="selected",
        )
        session.add(grant)
        await session.flush()

    entitlement = await session.scalar(
        select(WorkspaceGitRepositoryEntitlement).where(
            WorkspaceGitRepositoryEntitlement.grant_id == grant.id,
            WorkspaceGitRepositoryEntitlement.repository_id == repository.id,
        )
    )
    if entitlement is None:
        entitlement = WorkspaceGitRepositoryEntitlement(
            workspace_id=workspace_id,
            grant_id=grant.id,
            repository_id=repository.id,
        )
        session.add(entitlement)
        await session.flush()
    return entitlement.id
