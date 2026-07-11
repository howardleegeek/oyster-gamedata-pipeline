#!/usr/bin/env python3
"""
Batch Dashboard - Streamlit dashboard for batch progress visualization.

Usage:
    streamlit run bin/batch_dashboard.py
"""

import json
import logging
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict

try:
    import pandas as pd
    import plotly.express as px
    import plotly.graph_objects as go
    import streamlit as st
except ImportError:
    print("Required packages not installed. Run: pip install streamlit pandas plotly")
    sys.exit(1)

log = logging.getLogger(__name__)

# Base directory for batch files
BATCH_DIR = Path(__file__).parent.parent


def load_batch_manifest() -> Dict[str, Any]:
    """Load the batch manifest file."""
    manifest_path = BATCH_DIR / "batch_manifest.json"

    if manifest_path.exists():
        with open(manifest_path, 'r') as f:
            return json.load(f)

    return {
        "batch_id": "no-batch",
        "scene": "unknown",
        "operator_id": "unknown",
        "quota": {"1": 10, "2": 10, "3": 10, "4": 5},
        "sessions": []
    }


def load_session_grade(session_id: str) -> Dict[str, Any]:
    """Load session grade file if it exists."""
    # Try multiple possible locations
    possible_paths = [
        BATCH_DIR / "sessions" / session_id / "session_grade.json",
        BATCH_DIR / session_id / "session_grade.json",
        BATCH_DIR / f"{session_id}_grade.json",
    ]

    for path in possible_paths:
        if path.exists():
            with open(path, 'r') as f:
                return json.load(f)

    return {}


def get_route_type_description(route_type: int) -> str:
    """Get human-readable description for route type."""
    descriptions = {
        1: "Normal Exploration A",
        2: "Normal Exploration B",
        3: "Special/Loop Pattern",
        4: "Rare Special Pattern"
    }
    return descriptions.get(route_type, "Unknown")


def calculate_statistics(manifest: Dict[str, Any]) -> Dict[str, Any]:
    """Calculate various statistics from the manifest."""
    sessions = manifest.get("sessions", [])
    quota = manifest.get("quota", {"1": 10, "2": 10, "3": 10, "4": 5})

    # Count by route_type
    route_counts = defaultdict(lambda: {"total": 0, "pass": 0, "fail": 0, "pending": 0})

    for session in sessions:
        route_type = str(session.get("route_type", 0))
        grade = session.get("grade", "PENDING")

        route_counts[route_type]["total"] += 1
        if grade == "PASS":
            route_counts[route_type]["pass"] += 1
        elif grade == "FAIL":
            route_counts[route_type]["fail"] += 1
        else:
            route_counts[route_type]["pending"] += 1

    # Calculate quota status
    quota_status = {}
    for rt, target in quota.items():
        current = route_counts.get(rt, {}).get("total", 0)
        quota_status[rt] = {
            "current": current,
            "target": target,
            "remaining": max(0, target - current),
            "met": current >= target
        }

    # Extract audit scores
    audit_scores = []
    for session in sessions:
        score_str = session.get("audit_score", "0/0")
        try:
            achieved, total = score_str.split("/")
            audit_scores.append(int(achieved))
        except (ValueError, AttributeError) as exc:
            log.debug("malformed audit_score %r for session: %s", score_str, exc)

    # Get failed session reasons
    failed_sessions = []
    for session in sessions:
        if session.get("grade") == "FAIL":
            grade_info = load_session_grade(session.get("id", ""))
            failed_sessions.append({
                "session_id": session.get("id", "unknown"),
                "route_type": session.get("route_type", 0),
                "reason": grade_info.get("failure_reason", "Unknown"),
                "details": grade_info.get("failure_details", "")
            })

    return {
        "total_sessions": len(sessions),
        "route_counts": dict(route_counts),
        "quota_status": quota_status,
        "audit_scores": audit_scores,
        "failed_sessions": failed_sessions,
        "pass_rate": (
            sum(1 for s in sessions if s.get("grade") == "PASS")
            / max(len(sessions), 1)
            * 100
        )
    }


def render_dashboard():
    """Render the Streamlit dashboard."""
    st.set_page_config(
        page_title="Batch Dashboard",
        page_icon="📊",
        layout="wide"
    )

    st.title("📊 Batch Progress Dashboard")

    # Load manifest
    manifest = load_batch_manifest()
    stats = calculate_statistics(manifest)

    # Header info
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Batch ID", manifest.get("batch_id", "N/A"))
    with col2:
        st.metric("Scene", manifest.get("scene", "N/A"))
    with col3:
        st.metric("Operator", manifest.get("operator_id", "N/A"))
    with col4:
        st.metric("Total Sessions", stats["total_sessions"])

    st.divider()

    # Quota Progress Section
    st.header("📈 Quota Progress by Route Type")

    quota_data = []
    for rt, status in stats["quota_status"].items():
        quota_data.append({
            "Route Type": f"Type {rt}",
            "Description": get_route_type_description(int(rt)),
            "Current": status["current"],
            "Target": status["target"],
            "Remaining": status["remaining"],
            "Status": "✅ Met" if status["met"] else "⚠️ In Progress"
        })

    quota_df = pd.DataFrame(quota_data)
    st.dataframe(quota_df, use_container_width=True)

    # Visual progress bars
    col1, col2 = st.columns(2)

    with col1:
        st.subheader("Quota Completion")
        fig = go.Figure()
        for rt, status in stats["quota_status"].items():
            pct = min(100, (status["current"] / max(status["target"], 1)) * 100)
            fig.add_trace(go.Bar(
                name=f"Type {rt}",
                x=[f"Type {rt}"],
                y=[pct],
                text=f"{status['current']}/{status['target']}",
                textposition='auto',
            ))
        fig.update_layout(
            yaxis_title="Completion %",
            yaxis=dict(range=[0, 120]),
            showlegend=False,
            height=300
        )
        st.plotly_chart(fig, use_container_width=True)

    with col2:
        st.subheader("Session Distribution")
        labels = [f"Type {rt}" for rt in stats["route_counts"].keys()]
        values = [rc["total"] for rc in stats["route_counts"].values()]
        if sum(values) > 0:
            fig = px.pie(values=values, names=labels, title="Sessions by Route Type")
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("No sessions recorded yet")

    st.divider()

    # Pass Rate Section
    st.header("✅ Pass Rate Analysis")

    col1, col2 = st.columns(2)

    with col1:
        st.metric("Overall Pass Rate", f"{stats['pass_rate']:.1f}%")

    with col2:
        pass_rate_data = []
        for rt, counts in stats["route_counts"].items():
            total = counts["total"]
            if total > 0:
                rate = (counts["pass"] / total) * 100
                pass_rate_data.append({
                    "Route Type": f"Type {rt}",
                    "Pass Rate": rate,
                    "Pass": counts["pass"],
                    "Fail": counts["fail"],
                    "Pending": counts["pending"]
                })

        if pass_rate_data:
            pass_df = pd.DataFrame(pass_rate_data)
            st.dataframe(pass_df, use_container_width=True)
        else:
            st.info("No graded sessions yet")

    st.divider()

    # Audit Score Histogram
    st.header("📊 Audit Score Distribution")

    if stats["audit_scores"]:
        fig = px.histogram(
            x=stats["audit_scores"],
            nbins=20,
            labels={"x": "Audit Score"},
            title="Audit Score Histogram"
        )
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("No audit scores recorded yet")

    st.divider()

    # Failed Sessions
    st.header("❌ Failed Sessions")

    if stats["failed_sessions"]:
        failed_df = pd.DataFrame(stats["failed_sessions"])
        st.dataframe(failed_df, use_container_width=True)
    else:
        st.success("No failed sessions! 🎉")

    st.divider()

    # Re-record Requests
    st.header("🔄 Re-record Requests (Quota Not Met)")

    re_record_needed = []
    for rt, status in stats["quota_status"].items():
        if not status["met"]:
            re_record_needed.append({
                "Route Type": f"Type {rt}",
                "Description": get_route_type_description(int(rt)),
                "Needed": status["remaining"],
                "Current": status["current"],
                "Target": status["target"]
            })

    if re_record_needed:
        st.warning("⚠️ Some route types need more recordings")
        re_record_df = pd.DataFrame(re_record_needed)
        st.dataframe(re_record_df, use_container_width=True)
    else:
        st.success("✅ All quotas met! Batch is complete.")

    st.divider()

    # Sessions Table
    st.header("📋 All Sessions")

    if manifest.get("sessions"):
        sessions_df = pd.DataFrame(manifest["sessions"])
        st.dataframe(sessions_df, use_container_width=True)
    else:
        st.info("No sessions recorded yet")

    # Refresh button
    if st.button("🔄 Refresh Data"):
        st.rerun()


def main():
    """Main entry point."""
    render_dashboard()


if __name__ == "__main__":
    main()
