from pathlib import Path
from typing import Optional
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables and .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    # Urja Meter Ops Upstream Portal
    portal_base_url: str = Field(
        default="https://urja-ops.flockenergy.tech",
        validation_alias="PORTAL_BASE_URL",
        description="Base URL of the upstream Urja Meter Ops portal"
    )
    portal_username: str = Field(
        default="operator@urja.local",
        validation_alias="PORTAL_USERNAME",
        description="Operator username for authenticating with the portal"
    )
    portal_password: str = Field(
        default="urja-ops-2026",
        validation_alias="PORTAL_PASSWORD",
        description="Operator password for authenticating with the portal"
    )

    # API Server Settings
    api_prefix: str = Field(
        default="/api/v1",
        validation_alias="API_PREFIX",
        description="URL prefix for versioned API endpoints"
    )
    host: str = Field(
        default="127.0.0.1",
        validation_alias="HOST",
        description="Host address to bind server to; keep loopback-only for local development"
    )
    port: int = Field(
        default=8000,
        validation_alias="PORT",
        description="Port to bind server to"
    )
    debug: bool = Field(
        default=False,
        validation_alias="DEBUG",
        description="Enable debug mode and verbose logging"
    )

    # Cache & Sync Settings
    cache_ttl_seconds: int = Field(
        default=900,
        validation_alias="CACHE_TTL_SECONDS",
        description="Time-to-live in seconds for cached meter and transformer data (15 minutes default)"
    )
    auto_sync_on_startup: bool = Field(
        default=True,
        validation_alias="AUTO_SYNC_ON_STARTUP",
        description="Whether to perform an initial sync of metadata on application startup"
    )
    forced_sync_cooldown_seconds: int = Field(
        default=60,
        ge=0,
        validation_alias="FORCED_SYNC_COOLDOWN_SECONDS",
        description="Minimum interval between manually forced metadata synchronizations"
    )
    api_access_token: Optional[str] = Field(
        default=None,
        min_length=16,
        validation_alias="API_ACCESS_TOKEN",
        description="Bearer token required for API endpoints; configure a unique secret before starting the service",
    )
    sync_state_db_path: str = Field(
        default=str(Path(__file__).resolve().parents[1] / ".sync_state.sqlite3"),
        validation_alias="SYNC_STATE_DB_PATH",
        description="SQLite file used to coordinate forced-sync cooldown across worker processes",
    )

    # HTTP Client Settings
    upstream_timeout_seconds: float = Field(
        default=15.0,
        validation_alias="UPSTREAM_TIMEOUT_SECONDS",
        description="Timeout for individual requests to the upstream portal"
    )
    upstream_max_retries: int = Field(
        default=3,
        validation_alias="UPSTREAM_MAX_RETRIES",
        description="Maximum retry attempts on transient network or server errors"
    )

    def safe_dict(self) -> dict:
        """Return configuration dictionary with sensitive credentials masked."""
        data = self.model_dump()
        if "portal_password" in data:
            data["portal_password"] = "******"
        if "api_access_token" in data and data["api_access_token"]:
            data["api_access_token"] = "******"
        return data


settings = Settings()
