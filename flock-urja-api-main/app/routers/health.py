from datetime import datetime, timezone
from fastapi import APIRouter, Depends
from app.core.cache import store
from app.core.upstream_client import UpstreamPortalClient
from app.models.schemas import HealthResponse

router = APIRouter(tags=["Health"])


def get_upstream_client() -> UpstreamPortalClient:
    from app.main import portal_client
    return portal_client


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Service Health Check",
    description="Check the operational status of the API service, cache sync, and upstream portal connectivity.",
)
async def get_health(client: UpstreamPortalClient = Depends(get_upstream_client)) -> HealthResponse:
    portal_alive = await client.check_health()
    is_cached = store.total_meters > 0

    status = "ok" if (portal_alive and is_cached) else "degraded"

    return HealthResponse(
        status=status,
        service="flock-urja-api",
        version="1.0.0",
        portal_connected=portal_alive,
        cache_synced=is_cached,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )
