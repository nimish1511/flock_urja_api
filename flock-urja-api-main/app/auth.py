import hmac
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import settings

bearer_scheme = HTTPBearer(auto_error=False)


async def require_api_token(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
) -> None:
    if not settings.api_access_token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "API_AUTH_NOT_CONFIGURED",
                "message": "Configure API_ACCESS_TOKEN before using protected API endpoints.",
            },
        )
    if credentials is None or not hmac.compare_digest(
        credentials.credentials, settings.api_access_token
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "API_AUTH_REQUIRED", "message": "A valid bearer token is required."},
            headers={"WWW-Authenticate": "Bearer"},
        )
