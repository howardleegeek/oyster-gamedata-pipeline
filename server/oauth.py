"""OAuth login router for Google and Discord with JWT authentication."""

import hashlib
import os
import secrets
import time
from datetime import datetime, timedelta
from typing import Optional

import httpx
import jwt
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

router = APIRouter(prefix="/api/auth", tags=["auth"])

# Configuration
JWT_SECRET = os.environ.get("JWT_SECRET", secrets.token_hex(32))
JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 1
REFRESH_TOKEN_EXPIRE_DAYS = 7

# OAuth configurations (should be in env vars in production)
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
GOOGLE_REDIRECT_URI = os.environ.get("GOOGLE_REDIRECT_URI", "http://localhost:8000/api/auth/google/callback")

DISCORD_CLIENT_ID = os.environ.get("DISCORD_CLIENT_ID", "")
DISCORD_CLIENT_SECRET = os.environ.get("DISCORD_CLIENT_SECRET", "")
DISCORD_REDIRECT_URI = os.environ.get("DISCORD_REDIRECT_URI", "http://localhost:8000/api/auth/discord/callback")

# In-memory store for OAuth states (use Redis in production)
oauth_states: dict[str, dict] = {}

# In-memory user database (use real DB in production)
# Only stores hashed oauth_id, not email
users_db: dict[str, dict] = {}

# Refresh token store
refresh_tokens: dict[str, dict] = {}


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class RefreshRequest(BaseModel):
    refresh_token: str


def hash_oauth_id(provider: str, oauth_id: str) -> str:
    """Create a hash of provider+id for privacy-preserving storage."""
    combined = f"{provider}:{oauth_id}"
    return hashlib.sha256(combined.encode()).hexdigest()


def create_jwt_token(user_id: str, email: str, role: str, provider: str, oauth_id: str, expires_hours: int = ACCESS_TOKEN_EXPIRE_HOURS) -> str:
    """Create a JWT token with specified claims."""
    now = datetime.utcnow()
    payload = {
        "sub": user_id,
        "email": email,
        "role": role,
        "oauth_provider": provider,
        "oauth_id": oauth_id,
        "iat": now,
        "exp": now + timedelta(hours=expires_hours),
        "type": "access"
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def create_refresh_token(user_id: str) -> str:
    """Create a refresh token."""
    token = secrets.token_urlsafe(32)
    refresh_tokens[token] = {
        "user_id": user_id,
        "created_at": time.time(),
        "expires_at": time.time() + (REFRESH_TOKEN_EXPIRE_DAYS * 24 * 3600)
    }
    return token


def verify_jwt_token(token: str) -> dict:
    """Verify and decode a JWT token."""
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        if payload.get("type") != "access":
            raise HTTPException(status_code=401, detail="Invalid token type")
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token has expired")
    except jwt.InvalidTokenError as e:
        raise HTTPException(status_code=401, detail=f"Invalid token: {str(e)}")


def get_or_create_user(provider: str, oauth_id: str, email: str, name: Optional[str] = None) -> dict:
    """Get existing user or create new one."""
    user_hash = hash_oauth_id(provider, oauth_id)
    
    if user_hash in users_db:
        return users_db[user_hash]
    
    # Determine role (first user is admin, others are buyers by default)
    role = "admin" if len(users_db) == 0 else "buyer"
    
    user = {
        "id": user_hash,
        "oauth_provider": provider,
        "oauth_id_hash": user_hash,
        "email_encrypted": email,  # In production, encrypt this
        "name": name,
        "role": role,
        "created_at": time.time()
    }
    users_db[user_hash] = user
    return user


# Google OAuth endpoints
@router.get("/google/login")
async def google_login(redirect: Optional[str] = None):
    """Kick off Google OAuth flow."""
    if not GOOGLE_CLIENT_ID:
        raise HTTPException(status_code=500, detail="Google OAuth not configured")
    
    state = secrets.token_urlsafe(16)
    oauth_states[state] = {
        "provider": "google",
        "redirect": redirect,
        "created_at": time.time()
    }
    
    auth_url = (
        f"https://accounts.google.com/o/oauth2/v2/auth"
        f"?client_id={GOOGLE_CLIENT_ID}"
        f"&redirect_uri={GOOGLE_REDIRECT_URI}"
        f"&response_type=code"
        f"&scope=openid email profile"
        f"&state={state}"
    )
    
    return RedirectResponse(url=auth_url)


@router.get("/google/callback")
async def google_callback(code: str, state: str, request: Request):
    """Handle Google OAuth callback and exchange code for JWT."""
    # Verify state
    state_data = oauth_states.pop(state, None)
    if not state_data:
        raise HTTPException(status_code=400, detail="Invalid state")
    
    if time.time() - state_data["created_at"] > 600:  # 10 min expiry
        raise HTTPException(status_code=400, detail="State expired")
    
    # Exchange code for tokens
    async with httpx.AsyncClient() as client:
        token_response = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code": code,
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "redirect_uri": GOOGLE_REDIRECT_URI,
                "grant_type": "authorization_code"
            }
        )
        
        if token_response.status_code != 200:
            raise HTTPException(status_code=400, detail="Failed to exchange code")
        
        tokens = token_response.json()
        
        # Get user info
        user_response = await client.get(
            "https://www.googleapis.com/oauth2/v2/userinfo",
            headers={"Authorization": f"Bearer {tokens['access_token']}"}
        )
        
        if user_response.status_code != 200:
            raise HTTPException(status_code=400, detail="Failed to get user info")
        
        user_info = user_response.json()
    
    # Create or get user
    user = get_or_create_user(
        provider="google",
        oauth_id=user_info["id"],
        email=user_info.get("email", ""),
        name=user_info.get("name")
    )
    
    # Create JWT tokens
    access_token = create_jwt_token(
        user_id=user["id"],
        email=user["email_encrypted"],
        role=user["role"],
        provider="google",
        oauth_id=user_info["id"]
    )
    refresh_token = create_refresh_token(user["id"])
    
    # Handle redirect for desktop apps
    redirect_uri = state_data.get("redirect")
    if redirect_uri and redirect_uri.startswith(("oyster://", "http://127.0.0.1:", "http://localhost:")):
        return RedirectResponse(url=f"{redirect_uri}?access_token={access_token}&refresh_token={refresh_token}")
    
    # Return tokens for web apps
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=ACCESS_TOKEN_EXPIRE_HOURS * 3600
    )


# Discord OAuth endpoints
@router.get("/discord/login")
async def discord_login(redirect: Optional[str] = None):
    """Kick off Discord OAuth flow."""
    if not DISCORD_CLIENT_ID:
        raise HTTPException(status_code=500, detail="Discord OAuth not configured")
    
    state = secrets.token_urlsafe(16)
    oauth_states[state] = {
        "provider": "discord",
        "redirect": redirect,
        "created_at": time.time()
    }
    
    auth_url = (
        f"https://discord.com/api/oauth2/authorize"
        f"?client_id={DISCORD_CLIENT_ID}"
        f"&redirect_uri={DISCORD_REDIRECT_URI}"
        f"&response_type=code"
        f"&scope=identify email"
        f"&state={state}"
    )
    
    return RedirectResponse(url=auth_url)


@router.get("/discord/callback")
async def discord_callback(code: str, state: str, request: Request):
    """Handle Discord OAuth callback and exchange code for JWT."""
    # Verify state
    state_data = oauth_states.pop(state, None)
    if not state_data:
        raise HTTPException(status_code=400, detail="Invalid state")
    
    if time.time() - state_data["created_at"] > 600:
        raise HTTPException(status_code=400, detail="State expired")
    
    # Exchange code for tokens
    async with httpx.AsyncClient() as client:
        token_response = await client.post(
            "https://discord.com/api/oauth2/token",
            data={
                "code": code,
                "client_id": DISCORD_CLIENT_ID,
                "client_secret": DISCORD_CLIENT_SECRET,
                "redirect_uri": DISCORD_REDIRECT_URI,
                "grant_type": "authorization_code"
            }
        )
        
        if token_response.status_code != 200:
            raise HTTPException(status_code=400, detail="Failed to exchange code")
        
        tokens = token_response.json()
        
        # Get user info
        user_response = await client.get(
            "https://discord.com/api/users/@me",
            headers={"Authorization": f"Bearer {tokens['access_token']}"}
        )
        
        if user_response.status_code != 200:
            raise HTTPException(status_code=400, detail="Failed to get user info")
        
        user_info = user_response.json()
    
    # Create or get user
    user = get_or_create_user(
        provider="discord",
        oauth_id=user_info["id"],
        email=user_info.get("email", ""),
        name=user_info.get("username")
    )
    
    # Create JWT tokens
    access_token = create_jwt_token(
        user_id=user["id"],
        email=user["email_encrypted"],
        role=user["role"],
        provider="discord",
        oauth_id=user_info["id"]
    )
    refresh_token = create_refresh_token(user["id"])
    
    # Handle redirect for desktop apps
    redirect_uri = state_data.get("redirect")
    if redirect_uri and redirect_uri.startswith(("oyster://", "http://127.0.0.1:", "http://localhost:")):
        return RedirectResponse(url=f"{redirect_uri}?access_token={access_token}&refresh_token={refresh_token}")
    
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=ACCESS_TOKEN_EXPIRE_HOURS * 3600
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(request: RefreshRequest):
    """Refresh an access token using a refresh token."""
    token_data = refresh_tokens.get(request.refresh_token)
    
    if not token_data:
        raise HTTPException(status_code=401, detail="Invalid refresh token")
    
    if time.time() > token_data["expires_at"]:
        del refresh_tokens[request.refresh_token]
        raise HTTPException(status_code=401, detail="Refresh token expired")
    
    # Get user
    user = users_db.get(token_data["user_id"])
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    
    # Create new access token
    access_token = create_jwt_token(
        user_id=user["id"],
        email=user["email_encrypted"],
        role=user["role"],
        provider=user["oauth_provider"],
        oauth_id=user["oauth_id_hash"]
    )
    
    # Create new refresh token (rotate)
    new_refresh_token = create_refresh_token(user["id"])
    del refresh_tokens[request.refresh_token]
    
    return TokenResponse(
        access_token=access_token,
        refresh_token=new_refresh_token,
        expires_in=ACCESS_TOKEN_EXPIRE_HOURS * 3600
    )


@router.post("/logout")
async def logout(request: RefreshRequest):
    """Logout and invalidate refresh token."""
    if request.refresh_token in refresh_tokens:
        del refresh_tokens[request.refresh_token]
    return {"message": "Logged out successfully"}


# Export verify function for middleware
__all__ = ["router", "verify_jwt_token", "hash_oauth_id"]
