"""ORM model registry.

Importing this package registers every model on ``Base.metadata`` so that
Alembic autogenerate and the migration runner see the full schema.
"""

from lode.db.models.alert import Alert  # noqa: F401
from lode.db.models.ai_model import AiModelConfig  # noqa: F401
from lode.db.models.application import (  # noqa: F401
    Application,
    ApplicationDescription,
    ApplicationIngestionOffset,
    ApplicationIngestionRuntime,
    ApplicationKafka,
    ApplicationRepo,
    DbSource,
)
from lode.db.models.integration import ApplicationIntegration  # noqa: F401
from lode.db.models.intake import AuditEvent, DeadLetter, Incident, IngestionEvent  # noqa: F401
from lode.db.models.investigation import (  # noqa: F401
    EvidenceArtifact,
    EvidenceCollection,
    EvidenceConnector,
    Hypothesis,
    InvestigationAiInvocation,
    InvestigationEvidenceLink,
    InvestigationFindingEdge,
    InvestigationFinding,
    Investigation,
    InvestigationExecutionEvent,
    InvestigationJob,
    InvestigationPlanNode,
    InvestigationPlanNodeDependency,
    InvestigationPlanRevision,
    InvestigationStage,
    RemediationPlan,
    SourceRevision,
)
from lode.db.models.git import GitCredential, GitRepo  # noqa: F401
from lode.db.models.permission import UserApplicationPerm  # noqa: F401
from lode.db.models.platform_setting import PlatformSetting  # noqa: F401
from lode.db.models.user import Invite, User  # noqa: F401

__all__ = [
    "Alert",
    "AiModelConfig",
    "Application",
    "ApplicationDescription",
    "ApplicationIngestionOffset",
    "ApplicationIngestionRuntime",
    "ApplicationKafka",
    "ApplicationRepo",
    "DbSource",
    "ApplicationIntegration",
    "AuditEvent",
    "DeadLetter",
    "EvidenceArtifact",
    "EvidenceCollection",
    "EvidenceConnector",
    "Hypothesis",
    "InvestigationAiInvocation",
    "InvestigationEvidenceLink",
    "InvestigationFindingEdge",
    "InvestigationFinding",
    "Investigation",
    "InvestigationExecutionEvent",
    "InvestigationJob",
    "InvestigationPlanNode",
    "InvestigationPlanNodeDependency",
    "InvestigationPlanRevision",
    "InvestigationStage",
    "RemediationPlan",
    "SourceRevision",
    "Incident",
    "IngestionEvent",
    "GitCredential",
    "GitRepo",
    "UserApplicationPerm",
    "PlatformSetting",
    "Invite",
    "User",
]
