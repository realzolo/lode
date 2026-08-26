"""Read-only metadata for dynamically rendered application integrations."""

from fastapi import APIRouter

from lode.api.schemas import IntegrationKindOut
from lode.integration_policy import integration_kinds

router = APIRouter(prefix="/integration-kinds", tags=["integration-kinds"])


@router.get("", response_model=list[IntegrationKindOut])
async def list_integration_kinds() -> list[IntegrationKindOut]:
    return [
        IntegrationKindOut(
            kind=item.key,
            version=item.version,
            label=item.label,
            capabilities=sorted(item.capabilities),
            form=list(item.form),
        )
        for item in integration_kinds()
    ]
