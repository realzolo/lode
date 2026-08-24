"""Pure state-machine tests for application ingestion status projection."""

from datetime import UTC, datetime, timedelta

from lode.api.routes.applications import _runtime_status
from lode.db.models.application import Application, ApplicationIngestionRuntime


def _app(state: str, version: int = 1) -> Application:
    app = Application(name="test")
    app.ingestion_state = state
    app.ingestion_version = version
    return app


def test_draft_and_paused_states_override_runtime_observation():
    runtime = ApplicationIngestionRuntime(
        application_id=1,
        observed_state="listening",
        observed_version=1,
        last_heartbeat_at=datetime.now(UTC),
    )
    assert _runtime_status(_app("draft"), runtime) == "draft"
    assert _runtime_status(_app("paused"), runtime) == "paused"


def test_active_runtime_requires_matching_fresh_heartbeat():
    app = _app("active", version=2)
    runtime = ApplicationIngestionRuntime(
        application_id=1,
        observed_state="listening",
        observed_version=1,
        last_heartbeat_at=datetime.now(UTC),
    )
    assert _runtime_status(app, runtime) == "starting"
    runtime.observed_version = 2
    runtime.last_heartbeat_at = datetime.now(UTC) - timedelta(seconds=60)
    assert _runtime_status(app, runtime) == "error"
