"""JWT verification middleware for protected routes."""

import logging
from typing import Optional

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from starlette.middleware.base import BaseHTTPMiddleware

from server.oauth import verify_jwt_token

logger = logging.getLogger(__name__)

security = HTTPBearer()


class AuthMiddleware(BaseHTTPMiddleware):
    """Middleware to verify JWT tokens on protected routes."""

    def __init__(self, app, protected_prefixes: Optional[list[str]] = None):
        super().__init__(app)
        self.protected_prefixes = protected_prefixes or ["/api/protected", "/api/user"]

    async def dispatch(self, request: Request, call_next):
        # Check if path requires authentication
        requires_auth = any(
            request.url.path.startswith(prefix) for prefix in self.protected_prefixes
        )

        if requires_auth:
            auth_header = request.headers.get("Authorization")
            if not auth_header or not auth_header.startswith("Bearer "):
                raise HTTPException(
                    status_code=401,
                    detail="Missing or invalid Authorization header"
                )

            token = auth_header.split(" ")[1]
            try:
                payload = verify_jwt_token(token)
                request.state.user = payload
            except HTTPException:
                raise
            except Exception as e:
                raise HTTPException(status_code=401, detail=f"Invalid token: {str(e)}")

        return await call_next(request)


async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> dict:
    """Dependency to get current user from JWT token."""
    token = credentials.credentials
    return verify_jwt_token(token)


async def get_current_user_optional(request: Request) -> Optional[dict]:
    """Dependency to optionally get current user (doesn't raise if no token)."""
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        return None

    token = auth_header.split(" ")[1]
    try:
        return verify_jwt_token(token)
    except Exception as exc:
        logger.debug("Optional auth verification failed: %s", exc)
        return None


def require_role(*allowed_roles: str):
    """Dependency factory to require specific roles."""
    async def role_checker(user: dict = Depends(get_current_user)) -> dict:
        user_role = user.get("role", "")
        if user_role not in allowed_roles:
            raise HTTPException(
                status_code=403,
                detail=f"Role '{user_role}' not authorized. Required: {allowed_roles}"
            )
        return user
    return role_checker


# Convenience dependencies
require_buyer = require_role("buyer", "contributor", "admin")
require_contributor = require_role("contributor", "admin")
require_admin = require_role("admin")


__all__ = [
    "AuthMiddleware",
    "get_current_user",
    "get_current_user_optional",
    "require_role",
    "require_buyer",
    "require_contributor",
    "require_admin",
]
