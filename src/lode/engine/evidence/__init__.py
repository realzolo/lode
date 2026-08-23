"""Evidence gateway: turn the analysis into something traceable.

The gateway pulls *real* source evidence for an incident (read-only, pinned to
a fixed git ref) and persists it as :class:`EvidenceArtifact` rows so every
conclusion can cite a file:line locator instead of hand-wave. Secret values in
excerpts are masked by default — there is no "desensitize=false" escape hatch
for source inspection (per the M3 mandate, but applied here too).
"""

from lode.engine.evidence.git import (
    collect_git_evidence,
    derive_query_terms,
    search_tree,
)
from lode.engine.evidence.secret_mask import mask_secrets

__all__ = [
    "collect_git_evidence",
    "derive_query_terms",
    "search_tree",
    "mask_secrets",
]
