from contextlib import asynccontextmanager
import logging
from pathlib import Path
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from app.config import settings
from app.auth import require_api_token
from app.core.cache import store
from app.core.upstream_client import UpstreamPortalClient
from app.models.errors import (
    ErrorDetail,
    ErrorResponse,
    UpstreamAuthError,
    UpstreamError,
    UpstreamNotFoundError,
    UpstreamTimeoutError,
)
from app.routers import admin, health, hierarchy, meters, transformers

# Configure structured logging
logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("flock_urja.main")

# Global singleton client
portal_client = UpstreamPortalClient()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager: initial setup and graceful shutdown."""
    logger.info("Starting Flock Energy Urja Meter Ops API service")

    # Initial sync on startup if enabled
    if settings.auto_sync_on_startup:
        try:
            logger.info("Performing startup synchronization with upstream portal...")
            await store.sync(portal_client)
            logger.info("Startup synchronization complete: %d meters indexed", store.total_meters)
        except Exception as exc:
            logger.warning("Startup synchronization failed (will retry on first request): %s", exc)

    yield

    logger.info("Shutting down API service and closing upstream client...")
    await portal_client.close()


app = FastAPI(
    title="Urja Meter Ops REST API",
    description="""
# Modern REST API Service for Urja Meter Ops

This service provides a clean, normalized, documented interface over the legacy Urja Meter Ops portal used by power distribution utilities.

## Key Capabilities:
- **Smart Meter Search & Filtering**: Query 400+ meters by ID, serial number, make, phase, status, or distribution transformer.
- **Geospatial Proximity**: Query meters within radius of GPS coordinates using the Haversine formula.
- **Historical Consumption Analytics**: Cumulative register readings with consecutive interval deltas and interval-based summaries.
- **Electrical Grid Hierarchy**: Reconstructed 7-tier distribution topology:
  `Zone` &rarr; `Circle` &rarr; `Division` &rarr; `Subdivision` &rarr; `Substation` &rarr; `Feeder` &rarr; `Distribution Transformer (DT)`.
- **Distribution Transformers**: Detailed transformer metadata with connected meters.
- **Modern Console Dashboard**: Interactive browser UI served at `/dashboard`.
- **API security**: Bearer-token authentication is required for API routes.
    """,
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:8000",
        "http://localhost:8000",
    ],
    allow_methods=["GET", "POST"],
    allow_headers=["Accept", "Authorization", "Content-Type"],
)


# Exception Handlers ensuring consistent JSON error envelope
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    if isinstance(exc.detail, dict) and "code" in exc.detail:
        error_detail = ErrorDetail(
            code=exc.detail["code"],
            message=exc.detail.get("message", "An error occurred"),
            details=exc.detail.get("details"),
        )
    else:
        error_detail = ErrorDetail(
            code=f"HTTP_{exc.status_code}",
            message=str(exc.detail),
        )
    return JSONResponse(
        status_code=exc.status_code,
        content=ErrorResponse(error=error_detail).model_dump(),
        headers=exc.headers,
    )


@app.exception_handler(UpstreamNotFoundError)
async def upstream_not_found_handler(request: Request, exc: UpstreamNotFoundError):
    return JSONResponse(
        status_code=status.HTTP_404_NOT_FOUND,
        content=ErrorResponse(
            error=ErrorDetail(code="NOT_FOUND", message=exc.message)
        ).model_dump(),
    )


@app.exception_handler(UpstreamTimeoutError)
async def upstream_timeout_handler(request: Request, exc: UpstreamTimeoutError):
    return JSONResponse(
        status_code=status.HTTP_504_GATEWAY_TIMEOUT,
        content=ErrorResponse(
            error=ErrorDetail(code="UPSTREAM_TIMEOUT", message=exc.message)
        ).model_dump(),
    )


@app.exception_handler(UpstreamAuthError)
async def upstream_auth_handler(request: Request, exc: UpstreamAuthError):
    return JSONResponse(
        status_code=status.HTTP_502_BAD_GATEWAY,
        content=ErrorResponse(
            error=ErrorDetail(code="UPSTREAM_AUTH_ERROR", message=exc.message)
        ).model_dump(),
    )


@app.exception_handler(UpstreamError)
async def upstream_error_handler(request: Request, exc: UpstreamError):
    return JSONResponse(
        status_code=exc.status_code,
        content=ErrorResponse(
            error=ErrorDetail(code="UPSTREAM_ERROR", message=exc.message, details=exc.details)
        ).model_dump(),
    )


@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled server exception: %s", exc)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=ErrorResponse(
            error=ErrorDetail(code="INTERNAL_SERVER_ERROR", message="An unexpected internal server error occurred.")
        ).model_dump(),
    )


# Static dashboard UI
static_dir = Path(__file__).parent / "static"
dashboard_file = static_dir / "dashboard.html"


@app.get("/", include_in_schema=False)
@app.get("/dashboard", include_in_schema=False)
async def get_dashboard():
    """Serve the modern interactive web console."""
    if dashboard_file.exists():
        return FileResponse(dashboard_file)
    return JSONResponse(
        {"message": "Urja Meter Ops API Service is running. Visit /docs for OpenAPI documentation."}
    )


# Include Routers
app.include_router(health.router)
app.include_router(meters.router, prefix=settings.api_prefix, dependencies=[Depends(require_api_token)])
app.include_router(transformers.router, prefix=settings.api_prefix, dependencies=[Depends(require_api_token)])
app.include_router(hierarchy.router, prefix=settings.api_prefix, dependencies=[Depends(require_api_token)])
app.include_router(admin.router, prefix=settings.api_prefix, dependencies=[Depends(require_api_token)])
