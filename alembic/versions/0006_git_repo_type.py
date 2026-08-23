"""add repo_type to git_repos

Revision ID: 0006_git_repo_type
Revises: 0005_db_source_ssl_sensitive
Create Date: 2026-08-23

The repository registry previously only stored a free-form ``repo_url``. To
support GitHub and other providers (GitLab, Gitee, Bitbucket, ...) we tag each
row with a ``repo_type`` so the UI and engine can branch on the host family.

``repo_type`` is a non-nullable Text column defaulting to ``other``; the
settings UI offers a curated dropdown of the common providers but any value is
accepted so new hosts can be onboarded without a migration.

Downgrade drops the column, restoring the 0005 shape.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006_git_repo_type"
down_revision: Union[str, Sequence[str], None] = "0005_db_source_ssl_sensitive"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "git_repos",
        sa.Column("repo_type", sa.Text(), nullable=False, server_default="other"),
    )


def downgrade() -> None:
    op.drop_column("git_repos", "repo_type")
