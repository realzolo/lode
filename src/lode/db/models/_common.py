"""Shared SQLAlchemy primitives for the final schema."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Text, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column


JSON_OBJECT_DEFAULT = text("'{}'::jsonb")
JSON_ARRAY_DEFAULT = text("'[]'::jsonb")
TEXT_ARRAY_DEFAULT = text("'{}'::text[]")


def snowflake_pk() -> Mapped[int]:
    return mapped_column(
        BigInteger,
        primary_key=True,
        server_default=text("next_lode_id()"),
    )


def json_object(*, nullable: bool = False) -> Mapped[dict]:
    return mapped_column(JSONB, nullable=nullable, server_default=JSON_OBJECT_DEFAULT if not nullable else None)


def json_array() -> Mapped[list]:
    return mapped_column(JSONB, nullable=False, server_default=JSON_ARRAY_DEFAULT)


def text_array() -> Mapped[list[str]]:
    return mapped_column(ARRAY(Text), nullable=False, server_default=TEXT_ARRAY_DEFAULT)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class CreatedAtMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
