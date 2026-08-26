"""Provider adapter registry for the capability-level log search contract."""

from collections.abc import Awaitable, Callable

from lode.db.models.integration import ApplicationIntegration
from lode.db.models.investigation import Investigation, InvestigationStep
from lode.engine.loki_investigation import collect_loki_evidence

LogCollector = Callable[..., Awaitable[list[int]]]

_LOG_COLLECTORS: dict[str, LogCollector] = {
    "loki": collect_loki_evidence,
}


async def collect_log_evidence(
    session,
    *,
    investigation: Investigation,
    step: InvestigationStep,
    integration: ApplicationIntegration,
) -> list[int]:
    try:
        collector = _LOG_COLLECTORS[integration.kind]
    except KeyError as exc:
        raise ValueError(
            f"log_search adapter is not registered for kind '{integration.kind}'"
        ) from exc
    return await collector(
        session,
        investigation=investigation,
        step=step,
        connector=integration,
    )
