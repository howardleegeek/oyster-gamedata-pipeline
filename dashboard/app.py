"""
Streamlit frontend for Oyster Dashboard.
Provides buyer and contributor views for session management.
"""

import os

import jwt
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import requests
import streamlit as st

# Configuration
API_BASE = os.getenv("API_BASE", "http://localhost:8000")
JWT_SECRET = os.getenv("JWT_SECRET", "dev-secret-change-in-production")

# Page config
st.set_page_config(
    page_title="Oyster Dashboard",
    page_icon="🦪",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom CSS for mobile responsiveness
st.markdown("""
<style>
    /* Mobile responsive adjustments */
    @media (max-width: 768px) {
        .stDataFrame {
            font-size: 12px;
        }
        .stMetric label {
            font-size: 14px;
        }
        .stMetric value {
            font-size: 20px;
        }
    }
    
    /* Card styling */
    .card {
        background: #f8f9fa;
        border-radius: 10px;
        padding: 20px;
        margin: 10px 0;
        box-shadow: 0 2px 4px rgba(0,0,0,0.1);
    }
    
    /* Status badges */
    .status-approved { color: #28a745; font-weight: bold; }
    .status-rejected { color: #dc3545; font-weight: bold; }
    .status-pending { color: #ffc107; font-weight: bold; }
    
    /* Verification badges */
    .verify-valid { background: #d4edda; color: #155724; padding: 5px 10px; border-radius: 5px; }
    .verify-invalid { background: #f8d7da; color: #721c24; padding: 5px 10px; border-radius: 5px; }
</style>
""", unsafe_allow_html=True)


# Session state initialization
def init_session_state():
    if "token" not in st.session_state:
        st.session_state.token = None
    if "user" not in st.session_state:
        st.session_state.user = None
    if "current_page" not in st.session_state:
        st.session_state.current_page = "Browse"


init_session_state()


# API helpers
def api_headers():
    """Get authorization headers."""
    if st.session_state.token:
        return {"Authorization": f"Bearer {st.session_state.token}"}
    return {}


def api_get(endpoint, params=None):
    """Make GET request to API."""
    try:
        resp = requests.get(f"{API_BASE}{endpoint}", headers=api_headers(), params=params)
        if resp.status_code == 401:
            st.error("Session expired. Please log in again.")
            st.session_state.token = None
            st.session_state.user = None
            return None
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.RequestException as e:
        st.error(f"API Error: {e}")
        return None


def api_post(endpoint, data=None):
    """Make POST request to API."""
    try:
        resp = requests.post(f"{API_BASE}{endpoint}", headers=api_headers(), json=data)
        if resp.status_code == 401:
            st.error("Session expired. Please log in again.")
            st.session_state.token = None
            st.session_state.user = None
            return None
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.RequestException as e:
        st.error(f"API Error: {e}")
        return None


# Login sidebar
def render_login():
    """Render login form in sidebar."""
    st.sidebar.title("🦪 Oyster Dashboard")
    
    if st.session_state.token:
        st.sidebar.success(f"Logged in as: {st.session_state.user['user_id']}")
        st.sidebar.info(f"Role: {st.session_state.user['role']}")
        if st.sidebar.button("Logout"):
            st.session_state.token = None
            st.session_state.user = None
            st.rerun()
        return True
    
    st.sidebar.markdown("### Login")
    
    with st.sidebar.form("login_form"):
        user_id = st.text_input("User ID")
        role = st.selectbox("Role", ["buyer", "contributor"])
        submit = st.form_submit_button("Login")
        
        if submit and user_id:
            # Get test token from API
            try:
                resp = requests.get(f"{API_BASE}/api/auth/token", params={"user_id": user_id, "role": role})
                resp.raise_for_status()
                data = resp.json()
                st.session_state.token = data["access_token"]
                
                # Decode to get user info
                payload = jwt.decode(st.session_state.token, JWT_SECRET, algorithms=["HS256"])
                st.session_state.user = {"user_id": payload["sub"], "role": payload["role"]}
                st.rerun()
            except Exception as e:
                st.error(f"Login failed: {e}")
    
    return False


# Navigation
def render_navigation():
    """Render navigation menu."""
    if st.session_state.user["role"] == "buyer":
        pages = ["Browse", "Verify", "Approve"]
    else:
        pages = ["My Sessions", "Payouts", "Verify"]
    
    st.sidebar.markdown("### Navigation")
    for page in pages:
        if st.sidebar.button(page, key=f"nav_{page}"):
            st.session_state.current_page = page
            st.rerun()


# Browse page (Buyer)
def render_browse_page():
    """Render session browse page with filters and preview."""
    st.title("📊 Browse Sessions")
    
    # Filters
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        game_filter = st.selectbox("Game", ["All", "minecraft", "roblox", "fortnite"])
    with col2:
        route_filter = st.selectbox("Route Type", ["All", "exploration", "task", "combat"])
    with col3:
        min_score = st.slider("Min Audit Score", 0.0, 1.0, 0.0, 0.05)
    with col4:
        status_filter = st.selectbox("Status", ["All", "pending", "approved", "rejected"])
    
    # Build params
    params = {"page": 1, "page_size": 50}
    if game_filter != "All":
        params["game"] = game_filter
    if route_filter != "All":
        params["route_type"] = route_filter
    if min_score > 0:
        params["min_audit_score"] = min_score
    if status_filter != "All":
        params["status"] = status_filter
    
    # Fetch sessions
    data = api_get("/api/sessions", params)
    
    if not data:
        st.warning("No sessions found or API unavailable")
        return
    
    sessions = data["sessions"]
    
    # Display metrics
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Total Sessions", data["total"])
    with col2:
        avg_score = sum(s["audit_score"] for s in sessions) / len(sessions) if sessions else 0
        st.metric("Avg Audit Score", f"{avg_score:.2f}")
    with col3:
        pending = sum(1 for s in sessions if s["status"] == "pending")
        st.metric("Pending Review", pending)
    
    # Sessions table
    if sessions:
        df = pd.DataFrame(sessions)
        
        # Format for display
        df_display = df[["id", "game", "scene", "route_type", "audit_score", "status", "duration_seconds"]]
        df_display["audit_score"] = df_display["audit_score"].apply(lambda x: f"{x:.2f}")
        df_display["duration"] = df_display["duration_seconds"].apply(lambda x: f"{x}s")
        df_display = df_display.drop(columns=["duration_seconds"])
        
        st.dataframe(
            df_display,
            use_container_width=True,
            hide_index=True,
            column_config={
                "id": st.column_config.TextColumn("Session ID"),
                "audit_score": st.column_config.TextColumn("Score"),
            }
        )
        
        # Preview pane
        st.markdown("---")
        st.subheader("🎬 Session Preview")
        
        selected_id = st.selectbox("Select session to preview", [s["id"] for s in sessions])
        
        if selected_id:
            session = next((s for s in sessions if s["id"] == selected_id), None)
            if session:
                col1, col2 = st.columns([2, 1])
                
                with col1:
                    st.markdown("**Video Preview**")
                    st.video(f"{API_BASE}/api/sessions/{selected_id}/preview")
                    
                    # Depth heatmap visualization (mock)
                    st.markdown("**Depth Heatmap**")
                    import numpy as np
                    depth_data = np.random.rand(50, 50)
                    fig = px.imshow(depth_data, color_continuous_scale="Viridis")
                    fig.update_layout(height=300, margin=dict(l=0, r=0, t=0, b=0))
                    st.plotly_chart(fig, use_container_width=True)
                
                with col2:
                    st.markdown("**Session Info**")
                    st.json({
                        "Game": session["game"],
                        "Scene": session["scene"],
                        "Route": session["route_type"],
                        "Score": f"{session['audit_score']:.2f}",
                        "Status": session["status"],
                        "Duration": f"{session['duration_seconds']}s"
                    })
                    
                    # Action camera plot (mock)
                    st.markdown("**Action Timeline**")
                    actions = np.random.randint(0, 10, 50)
                    fig = go.Figure()
                    fig.add_trace(go.Scatter(y=actions, mode="lines+markers"))
                    fig.update_layout(height=200, margin=dict(l=0, r=0, t=0, b=0))
                    st.plotly_chart(fig, use_container_width=True)


# Verify page
def render_verify_page():
    """Render provenance verification page."""
    st.title("🔍 Verify Provenance")
    
    st.markdown("""
    Verify the cryptographic provenance of any session.
    The system checks the hash chain integrity and validates against the stored provenance record.
    """)
    
    # Session ID input
    session_id = st.text_input("Enter Session ID", placeholder="session_001")
    
    if st.button("Verify", type="primary"):
        if not session_id:
            st.error("Please enter a session ID")
            return
        
        with st.spinner("Verifying provenance..."):
            result = api_get(f"/api/sessions/{session_id}/verify")
        
        if result:
            if result["valid"]:
                st.success("✅ Provenance Verified")
                st.markdown('<span class="verify-valid">Valid Hash Chain</span>', unsafe_allow_html=True)
            else:
                st.error("❌ Provenance Verification Failed")
                st.markdown('<span class="verify-invalid">Invalid Hash Chain</span>', unsafe_allow_html=True)
            
            # Details
            col1, col2, col3 = st.columns(3)
            with col1:
                st.metric("Chain Intact", "✅" if result["chain_intact"] else "❌")
            with col2:
                st.metric("Hash Matches", "✅" if result["hash_matches"] else "❌")
            with col3:
                st.metric("Overall Valid", "✅" if result["valid"] else "❌")
            
            with st.expander("View Details"):
                st.json(result["details"])


# Approve page (Buyer)
def render_approve_page():
    """Render approval queue for buyers."""
    st.title("✅ Review Queue")
    
    # Get pending sessions
    data = api_get("/api/sessions", {"status": "pending", "page_size": 20})
    
    if not data or not data["sessions"]:
        st.info("No sessions pending review")
        return
    
    sessions = data["sessions"]
    st.metric("Pending Sessions", data["total"])
    
    for session in sessions:
        with st.container():
            col1, col2, col3 = st.columns([3, 1, 1])
            
            with col1:
                st.markdown(f"**{session['id']}** - {session['game']}")
                st.caption(f"Scene: {session['scene']} | Route: {session['route_type']}")
                st.caption(f"Score: {session['audit_score']:.2f} | Duration: {session['duration_seconds']}s")
            
            with col2:
                # Preview button
                if st.button("👁️ Preview", key=f"preview_{session['id']}"):
                    st.session_state.preview_session = session['id']
            
            with col3:
                # Quick actions
                approve_col, reject_col = st.columns(2)
                with approve_col:
                    if st.button("✓", key=f"approve_{session['id']}", type="primary"):
                        result = api_post(f"/api/sessions/{session['id']}/approve", {"notes": ""})
                        if result:
                            st.success("Approved!")
                            st.rerun()
                with reject_col:
                    if st.button("✗", key=f"reject_{session['id']}"):
                        st.session_state.reject_session = session['id']
        
        # Show preview if selected
        if st.session_state.get("preview_session") == session['id']:
            with st.expander("Preview", expanded=True):
                st.video(f"{API_BASE}/api/sessions/{session['id']}/preview")
        
        # Show rejection form if selected
        if st.session_state.get("reject_session") == session['id']:
            with st.form(f"reject_form_{session['id']}"):
                reason = st.text_area("Rejection Reason")
                notes = st.text_input("Additional Notes")
                submitted = st.form_submit_button("Submit Rejection")
                
                if submitted and reason:
                    result = api_post(f"/api/sessions/{session['id']}/reject", {"reason": reason, "notes": notes})
                    if result:
                        st.success("Rejected!")
                        st.session_state.reject_session = None
                        st.rerun()
        
        st.markdown("---")


# My Sessions page (Contributor)
def render_my_sessions_page():
    """Render contributor's session history."""
    st.title("📁 My Sessions")
    
    # Filters
    status_filter = st.selectbox("Filter by Status", ["All", "pending", "approved", "rejected"])
    
    params = {"page_size": 50}
    if status_filter != "All":
        params["status"] = status_filter
    
    data = api_get("/api/my/sessions", params)
    
    if not data:
        st.warning("No sessions found")
        return
    
    sessions = data["sessions"]
    
    # Metrics
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Total", data["total"])
    with col2:
        approved = sum(1 for s in sessions if s["status"] == "approved")
        st.metric("Approved", approved)
    with col3:
        pending = sum(1 for s in sessions if s["status"] == "pending")
        st.metric("Pending", pending)
    with col4:
        rejected = sum(1 for s in sessions if s["status"] == "rejected")
        st.metric("Rejected", rejected)
    
    # Sessions list
    for session in sessions:
        status_emoji = {"approved": "✅", "pending": "⏳", "rejected": "❌"}[session["status"]]
        
        with st.container():
            col1, col2 = st.columns([4, 1])
            
            with col1:
                st.markdown(f"**{status_emoji} {session['id']}**")
                st.caption(f"{session['game']} | {session['scene']} | Score: {session['audit_score']:.2f}")
            
            with col2:
                if session["status"] == "rejected":
                    if st.button("🔄 Re-record", key=f"rerecord_{session['id']}"):
                        st.session_state.rerecord_session = session['id']
        
        # Re-record request form
        if st.session_state.get("rerecord_session") == session['id']:
            with st.form(f"rerecord_form_{session['id']}"):
                reason = st.text_area("Why do you want to re-record?")
                submitted = st.form_submit_button("Submit Request")
                
                if submitted and reason:
                    result = api_post(f"/api/sessions/{session['id']}/rerecord", {"reason": reason})
                    if result:
                        st.success("Re-record request submitted!")
                        st.session_state.rerecord_session = None
                        st.rerun()
        
        st.markdown("---")


# Payouts page (Contributor)
def render_payouts_page():
    """Render contributor's payout summary."""
    st.title("💰 Payouts")
    
    data = api_get("/api/my/payouts")
    
    if not data:
        st.warning("Unable to fetch payout data")
        return
    
    # Main metrics
    col1, col2, col3 = st.columns(3)
    
    with col1:
        st.metric(
            "Total Earned",
            f"${data['total_payout_usd']:.2f}",
            delta=f"${data['pending_payout_usd']:.2f} pending"
        )
    
    with col2:
        st.metric("Approved Sessions", data['approved_sessions'])
    
    with col3:
        st.metric("Pending Review", data['pending_sessions'])
    
    # Payout chart
    st.markdown("### Payout History")
    
    if data['payout_history']:
        df = pd.DataFrame(data['payout_history'])
        
        fig = px.bar(df, x='session_id', y='amount', title='Payouts by Session')
        fig.update_layout(
            xaxis_title="Session",
            yaxis_title="Amount ($)",
            height=400
        )
        st.plotly_chart(fig, use_container_width=True)
        
        # Detailed history
        with st.expander("View Details"):
            st.dataframe(df, use_container_width=True, hide_index=True)
    else:
        st.info("No payouts yet. Keep recording!")
    
    # Mobile-friendly summary
    st.markdown("### Summary")
    st.markdown(f"""
    <div class="card">
        <p><strong>Total Sessions:</strong> {data['total_sessions']}</p>
        <p><strong>Approved:</strong> {data['approved_sessions']}</p>
        <p><strong>Pending:</strong> {data['pending_sessions']}</p>
        <p><strong>Rejected:</strong> {data['rejected_sessions']}</p>
        <p><strong>Total Payout:</strong> ${data['total_payout_usd']:.2f}</p>
    </div>
    """, unsafe_allow_html=True)


# Main app
def main():
    """Main application entry point."""
    if not render_login():
        st.info("Please log in to access the dashboard")
        return
    
    render_navigation()
    
    # Render current page
    page = st.session_state.current_page
    
    if page == "Browse":
        render_browse_page()
    elif page == "Verify":
        render_verify_page()
    elif page == "Approve":
        render_approve_page()
    elif page == "My Sessions":
        render_my_sessions_page()
    elif page == "Payouts":
        render_payouts_page()


if __name__ == "__main__":
    main()
