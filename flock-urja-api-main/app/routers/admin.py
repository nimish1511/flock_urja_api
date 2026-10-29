from fastapi import APIRouter, Depends, HTTPException, status

from app.config import settings
from app.core.cache import ForcedSyncCooldownError, store
from app.core.upstream_client import UpstreamPortalClient
from app.models.schemas import SyncStatusResponse

router = APIRouter(prefix="/sync", tags=["System & Cache"])


def get_upstream_client() -> UpstreamPortalClient:
    from app.main import portal_client
    return portal_client


@router.get(
    "/status",
    response_model=SyncStatusResponse,
    summary="Get Cache Synchronization Status",
    description="Check whether in-memory cache of meters and transformers is fresh or stale.",
)
async def get_sync_status() -> SyncStatusResponse:
    last_synced = store.last_synced_at.isoformat() if store.last_synced_at else None
    return SyncStatusResponse(
        status="synced" if store.total_meters > 0 else "empty",
        last_synced_at=last_synced,
        meters_cached=store.total_meters,
        transformers_cached=store.total_transformers,
        cache_ttl_seconds=settings.cache_ttl_seconds,
        is_stale=store.is_stale(),
    )


@router.post(
    "",
    response_model=SyncStatusResponse,
    summary="Force Synchronize Cache",
    description="Trigger an immediate full refresh of all meter metadata and distribution transformers from the upstream portal.",
)
async def trigger_sync(
    client: UpstreamPortalClient = Depends(get_upstream_client),
) -> SyncStatusResponse:
    try:
        await store.force_sync(client)
    except ForcedSyncCooldownError as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "code": "SYNC_COOLDOWN",
                "message": str(exc),
                "details": {"retry_after_seconds": exc.retry_after_seconds},
            },
            headers={"Retry-After": str(exc.retry_after_seconds)},
        ) from exc
    return await get_sync_status()
