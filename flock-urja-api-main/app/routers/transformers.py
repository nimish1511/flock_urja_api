import math
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.cache import store
from app.core.upstream_client import UpstreamPortalClient
from app.models.errors import ErrorResponse
from app.models.schemas import (
    TransformerDetail,
    TransformerListResponse,
)

router = APIRouter(prefix="/transformers", tags=["Transformers"])


def get_upstream_client() -> UpstreamPortalClient:
    from app.main import portal_client
    return portal_client


@router.get(
    "",
    response_model=TransformerListResponse,
    summary="List Distribution Transformers",
    description="Retrieve a paginated list of Distribution Transformers (DTs) with optional feeder filtering.",
)
async def list_transformers(
    feeder_code: Optional[str] = Query(None, description="Filter by 11kV feeder code (e.g. 'F-001')"),
    page: int = Query(1, ge=1, description="Page number (1-based)"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    client: UpstreamPortalClient = Depends(get_upstream_client),
) -> TransformerListResponse:
    await store.sync(client)
    items, total = store.query_transformers(
        feeder_code=feeder_code,
        page=page,
        page_size=page_size,
    )

    pages = math.ceil(total / page_size) if total > 0 else 1

    return TransformerListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        pages=pages,
    )


@router.get(
    "/{code}",
    response_model=TransformerDetail,
    summary="Get Distribution Transformer Details",
    description="Retrieve transformer details including capacity, feeder association, and all connected smart meters.",
    responses={
        200: {"description": "Detailed transformer and connected meters."},
        404: {"model": ErrorResponse, "description": "Transformer not found."},
    },
)
async def get_transformer(
    code: str,
    client: UpstreamPortalClient = Depends(get_upstream_client),
) -> TransformerDetail:
    await store.sync(client)
    transformer = store.get_transformer(code)

    if not transformer:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "TRANSFORMER_NOT_FOUND", "message": f"Distribution Transformer '{code}' was not found."},
        )

    return transformer
