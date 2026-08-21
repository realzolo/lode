"""ORM model registry.

Importing this package registers every model on ``Base.metadata`` so that
Alembic autogenerate and the migration runner see the full schema.
"""

from incident_trace.db.models.alert import Alert  # noqa: F401
from incident_trace.db.models.analysis import Analysis, AnalysisHint, AnalysisStep  # noqa: F401
from incident_trace.db.models.ai_model import AiModelConfig  # noqa: F401
from incident_trace.db.models.application import (  # noqa: F401
    Application,
    ApplicationKafka,
    ApplicationRepo,
    PresetPrompt,
    DbSource,
)
from incident_trace.db.models.git import GitCredential, GitRepo  # noqa: F401
from incident_trace.db.models.memory import Memory  # noqa: F401
from incident_trace.db.models.permission import UserApplicationPerm  # noqa: F401
from incident_trace.db.models.user import Invite, User  # noqa: F401

__all__ = [
    "Alert",
    "Analysis",
    "AnalysisHint",
    "AnalysisStep",
    "AiModelConfig",
    "Application",
    "ApplicationKafka",
    "ApplicationRepo",
    "PresetPrompt",
    "DbSource",
    "GitCredential",
    "GitRepo",
    "Memory",
    "UserApplicationPerm",
    "Invite",
    "User",
]
