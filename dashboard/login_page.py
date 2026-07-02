"""Streamlit login UI with Google + Discord OAuth buttons."""

import os
from datetime import datetime
from typing import Optional

import httpx
import jwt
import streamlit as st

# Configuration
API_BASE_URL = os.environ.get("OYSTER_API_URL", "http://localhost:8000")
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
DISCORD_CLIENT_ID = os.environ.get("DISCORD_CLIENT_ID", "")


def get_oauth_url(provider: str, redirect_uri: Optional[str] = None) -> str:
    """Get the OAuth login URL for the specified provider."""
    if redirect_uri is None:
        # Use current page as redirect
        redirect_uri = st.query_params.get("redirect", "/")
    
    return f"{API_BASE_URL}/api/auth/{provider}/login?redirect={redirect_uri}"


def render_login_page():
    """Render the login page with Google and Discord buttons."""
    st.set_page_config(
        page_title="Oyster Dashboard - Login",
        page_icon="🦪",
        layout="centered"
    )
    
    # Custom CSS for buttons
    st.markdown("""
        <style>
        .oauth-button {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            width: 100%;
            padding: 12px 24px;
            margin: 8px 0;
            border-radius: 8px;
            font-size: 16px;
            font-weight: 500;
            cursor: pointer;
            border: none;
            text-decoration: none;
        }
        .google-button {
            background-color: #ffffff;
            color: #444444;
            border: 1px solid #dadce0;
        }
        .google-button:hover {
            background-color: #f8f9fa;
        }
        .discord-button {
            background-color: #5865F2;
            color: white;
        }
        .discord-button:hover {
            background-color: #4752C4;
        }
        .button-icon {
            margin-right: 12px;
            font-size: 20px;
        }
        </style>
    """, unsafe_allow_html=True)
    
    # Title
    st.title("🦪 Oyster Dashboard")
    st.subheader("Sign in to continue")
    
    st.markdown("---")
    
    # Check for token in URL params (from OAuth callback)
    query_params = st.query_params
    if "access_token" in query_params:
        handle_oauth_callback(query_params)
        return
    
    # Login buttons
    col1, col2, col3 = st.columns([1, 2, 1])
    
    with col2:
        # Google login button
        google_url = get_oauth_url("google")
        st.markdown(f"""
            <a href="{google_url}" class="oauth-button google-button">
                <span class="button-icon">🔵</span>
                Continue with Google
            </a>
        """, unsafe_allow_html=True)
        
        st.markdown("<br>", unsafe_allow_html=True)
        
        # Discord login button
        discord_url = get_oauth_url("discord")
        st.markdown(f"""
            <a href="{discord_url}" class="oauth-button discord-button">
                <span class="button-icon">💬</span>
                Continue with Discord
            </a>
        """, unsafe_allow_html=True)
    
    st.markdown("---")
    
    # Info section
    with st.expander("ℹ️ About authentication"):
        st.markdown("""
        **Why OAuth?**
        
        We use Google and Discord OAuth for secure authentication without storing passwords.
        Your account is linked to your OAuth provider, and we only store a hashed identifier
        for privacy.
        
        **What we store:**
        - Hashed OAuth ID (not your email)
        - Your role (buyer, contributor, or admin)
        - Encrypted profile information
        
        **Token expiration:**
        - Access tokens expire in 1 hour
        - Refresh tokens expire in 7 days
        """)
    
    # Footer
    st.markdown("""
        <div style="text-align: center; margin-top: 40px; color: #666;">
            <small>
                By signing in, you agree to our Terms of Service and Privacy Policy.
            </small>
        </div>
    """, unsafe_allow_html=True)


def handle_oauth_callback(params: dict):
    """Handle OAuth callback with token in URL."""
    access_token = params.get("access_token", [""])[0] if isinstance(params.get("access_token"), list) else params.get("access_token", "")
    refresh_token = params.get("refresh_token", [""])[0] if isinstance(params.get("refresh_token"), list) else params.get("refresh_token", "")
    
    if not access_token:
        st.error("Authentication failed: No access token received")
        return
    
    try:
        # Decode token to get user info
        payload = jwt.decode(access_token, options={"verify_signature": False})
        
        # Store tokens in session state
        st.session_state["access_token"] = access_token
        st.session_state["refresh_token"] = refresh_token
        st.session_state["user"] = {
            "id": payload.get("sub"),
            "email": payload.get("email"),
            "role": payload.get("role"),
            "provider": payload.get("oauth_provider")
        }
        
        # Clear URL params
        st.query_params.clear()
        
        # Show success message
        st.success(f"Welcome, {payload.get('email', 'User')}!")
        st.balloons()
        
        # Redirect to dashboard
        st.markdown("""
            <script>
                setTimeout(function() {
                    window.location.href = '/';
                }, 1500);
            </script>
        """, unsafe_allow_html=True)
        
        st.info("Redirecting to dashboard...")
        
    except jwt.InvalidTokenError as e:
        st.error(f"Invalid authentication token: {e}")


def render_user_info():
    """Render user info if authenticated."""
    if "user" not in st.session_state:
        return None
    
    user = st.session_state["user"]
    
    with st.sidebar:
        st.markdown(f"**{user.get('email', 'User')}**")
        st.markdown(f"Role: `{user.get('role', 'unknown')}`")
        st.markdown(f"Provider: `{user.get('provider', 'unknown')}`")
        
        if st.button("Logout"):
            logout()
            st.rerun()


def logout():
    """Clear session and logout."""
    # Call logout endpoint
    if "refresh_token" in st.session_state:
        try:
            httpx.post(
                f"{API_BASE_URL}/api/auth/logout",
                json={"refresh_token": st.session_state["refresh_token"]},
                timeout=5.0
            )
        except Exception:
            pass
    
    # Clear session state
    for key in ["access_token", "refresh_token", "user"]:
        if key in st.session_state:
            del st.session_state[key]


def check_authentication():
    """Check if user is authenticated."""
    if "access_token" not in st.session_state:
        return False
    
    token = st.session_state["access_token"]
    try:
        payload = jwt.decode(token, options={"verify_signature": False})
        
        # Check if token is expired
        exp = payload.get("exp", 0)
        if datetime.utcnow().timestamp() > exp:
            # Try to refresh token
            if "refresh_token" in st.session_state:
                return refresh_token()
            return False
        
        return True
        
    except jwt.InvalidTokenError:
        return False


def refresh_token() -> bool:
    """Refresh the access token."""
    if "refresh_token" not in st.session_state:
        return False
    
    try:
        response = httpx.post(
            f"{API_BASE_URL}/api/auth/refresh",
            json={"refresh_token": st.session_state["refresh_token"]},
            timeout=5.0
        )
        
        if response.status_code == 200:
            data = response.json()
            st.session_state["access_token"] = data["access_token"]
            st.session_state["refresh_token"] = data["refresh_token"]
            return True
        
    except Exception:
        pass
    
    return False


def require_auth():
    """Decorator to require authentication for a page."""
    if not check_authentication():
        st.warning("Please sign in to access this page")
        render_login_page()
        st.stop()


# Main entry point
if __name__ == "__main__":
    render_login_page()
