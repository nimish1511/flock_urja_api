from typing import Any, Optional
from pydantic import BaseModel, Field


class ErrorDetail(BaseModel):
    """Detailed error object inside error response."""
    code: str = Field(description="Machine-readable error code", examples=["METER_NOT_FOUND"])
    message: str = Field(description="Human-readable explanation of error", examples=["Meter with ID J999999 was not found."])
    details: Optional[Any] = Field(default=None, description="Optional diagnostic details")



class ErrorResponse(BaseModel):
    """Standardized API error envelope."""
    error: ErrorDetail


class UpstreamError(Exception):
    """Base exception for errors communicating with upstream portal."""
    def __init__(self, message: str, status_code: int = 502, details: Any = None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.details = details


class UpstreamAuthError(UpstreamError):
    """Exception when authenticating with upstream portal fails."""
    def __init__(self, message: str = "Failed to authenticate with upstream portal", details: Any = None):
        super().__init__(message, status_code=502, details=details)


class UpstreamNotFoundError(UpstreamError):
    """Exception when a requested resource does not exist in upstream portal."""
    def __init__(self, resource: str, identifier: str):
        super().__init__(f"{resource} '{identifier}' not found in upstream portal", status_code=404)
        self.resource = resource
        self.identifier = identifier


class UpstreamTimeoutError(UpstreamError):
    """Exception when upstream portal request times out."""
    def __init__(self, message: str = "Request to upstream portal timed out"):
        super().__init__(message, status_code=504)
