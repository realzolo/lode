"""AI model configuration (global default or per-application override)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Text,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import select

from lode.crypto import decrypt_secret, encrypt_secret
from lode.db.base import Base


async def reencrypt_plaintext_keys(session: AsyncSession) -> int:
    """One-time migration: encrypt any legacy plaintext literal keys at rest.

    Literal (non-``env://``) ``api_key_ref`` values created before encryption
    was introduced are stored as plaintext. This re-encrypts them in place so
    the read path (``resolve_api_key``) can decrypt strictly without a plaintext
    fallback. ``env://`` references and already-encrypted tokens are left
    untouched. Returns the number of rows re-encrypted. Idempotent.
    """
    rows = (
        await session.execute(select(AiModelConfig))
    ).scalars().all()
    reencrypted = 0
    for row in rows:
        ref = row.api_key_ref
        if not ref or ref.startswith("env://"):
            continue
        try:
            decrypt_secret(ref)  # already an encrypted token
            continue
        except Exception:
            row.api_key_ref = encrypt_secret(ref) or ""
            reencrypted += 1
    if reencrypted:
        await session.commit()
    return reencrypted


class AiModelConfig(Base):
    __tablename__ = "ai_model_configs"

    id: Mapped[int] = mapped_column(
        BigInteger, Identity(always=True), primary_key=True
    )
    scope: Mapped[str] = mapped_column(Text, nullable=False, server_default="global")
    application_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("applications.id", ondelete="CASCADE")
    )
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    base_url: Mapped[str] = mapped_column(Text, nullable=False)
    api_key_ref: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    is_default: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )

    __table_args__ = (
        CheckConstraint("scope IN ('global', 'application')", name="scope"),
        CheckConstraint(
            "provider IN ('openai', 'anthropic')", name="provider"
        ),
        CheckConstraint(
            "scope = 'application' OR application_id IS NULL",
            name="scope_application",
        ),
        # Exactly one default per scope (global, or per application).
        Index(
            "ux_ai_model_configs_global_default",
            "scope",
            unique=True,
            postgresql_where="scope = 'global' AND is_default",
        ),
        Index(
            "ux_ai_model_configs_app_default",
            "application_id",
            unique=True,
            postgresql_where="scope = 'application' AND is_default",
        ),
    )
