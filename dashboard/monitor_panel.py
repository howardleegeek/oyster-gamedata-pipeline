#!/usr/bin/env python3
"""
monitor_panel.py — Streamlit "System Health" dashboard panel.

Adds a "System Health" page showing:
  - All component health (green/yellow/red)
  - Last 24h uptime per component
  - Recent alerts (with ack button)
  - Live metrics graphs (matplotlib)
"""

import json
import os
import time
from datetime import datetime, timedelta, timezone

import matplotlib
import streamlit as st

matplotlib.use("Agg")
import matplotlib.pyplot as plt

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

METRICS_FILE = os.path.expanduser("~/.oyster/monitor_metrics.jsonl")
ALERTS_FILE = os.path.expanduser("~/.oyster/monitor_alerts.jsonl")
CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "config", "monitor_thresholds.yaml")

# ---------------------------------------------------------------------------
# Data Loading
# ---------------------------------------------------------------------------

def load_metrics(hours: int = 24) -> list[dict]:
    """Load metrics from JSONL file for the last N hours."""
    if not os.path.exists(METRICS_FILE):
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    metrics = []

    with open(METRICS_FILE, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                ts_str = entry.get("poll_timestamp", "")
                if ts_str:
                    ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                    if ts >= cutoff:
                        metrics.append(entry)
            except (json.JSONDecodeError, ValueError):
                continue

    return metrics


def load_alerts(hours: int = 24) -> list[dict]:
    """Load alert state entries from JSONL file."""
    if not os.path.exists(ALERTS_FILE):
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    alerts = []

    with open(ALERTS_FILE, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                ts_str = entry.get("timestamp", "")
                if ts_str:
                    ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                    if ts >= cutoff:
                        alerts.append(entry)
            except (json.JSONDecodeError, ValueError):
                continue

    return alerts


def load_thresholds() -> dict:
    """Load threshold config."""
    if os.path.exists(CONFIG_PATH):
        import yaml
        with open(CONFIG_PATH, "r") as f:
            return yaml.safe_load(f)
    return {}


# ---------------------------------------------------------------------------
# Health Status Computation
# ---------------------------------------------------------------------------

def compute_component_health(metrics: list[dict]) -> dict[str, dict]:
    """Compute current health status for each component."""
    if not metrics:
        return {}

    latest = metrics[-1]
    health = latest.get("health", {})
    components = {}

    for name, result in health.items():
        status = result.get("status", "unknown")
        if status == "healthy":
            color = "green"
            icon = "✅"
        elif status == "unhealthy":
            color = "red"
            icon = "❌"
        else:
            color = "yellow"
            icon = "⚠️"

        components[name] = {
            "status": status,
            "color": color,
            "icon": icon,
            "last_check": result.get("timestamp", "N/A"),
            "error": result.get("error"),
            "response_time_ms": result.get("response_time_ms"),
        }

    # Add daemon state
    daemon = latest.get("daemon_state", {})
    components["recorder_daemon"] = {
        "status": daemon.get("status", "unknown"),
        "color": "green" if daemon.get("status") == "ok" else "red",
        "icon": "✅" if daemon.get("status") == "ok" else "❌",
        "daemon_state": daemon.get("daemon_state", "unknown"),
        "last_updated": daemon.get("last_updated", "N/A"),
    }

    # Add upload backlog
    backlog = latest.get("upload_backlog", {})
    components["upload_backlog"] = {
        "status": "ok" if backlog.get("backlog_gb", 0) < 100 else "warning",
        "color": "green" if backlog.get("backlog_gb", 0) < 100 else "yellow",
        "icon": "✅" if backlog.get("backlog_gb", 0) < 100 else "⚠️",
        "backlog_gb": backlog.get("backlog_gb", 0),
        "file_count": backlog.get("file_count", 0),
    }

    # Add disk
    disk = latest.get("disk", {})
    free_gb = disk.get("free_disk_gb")
    if free_gb is not None:
        components["minipc1_disk"] = {
            "status": "ok" if free_gb >= 5 else "warning",
            "color": "green" if free_gb >= 5 else "red",
            "icon": "✅" if free_gb >= 5 else "❌",
            "free_gb": free_gb,
        }
    else:
        components["minipc1_disk"] = {
            "status": "unknown",
            "color": "yellow",
            "icon": "⚠️",
            "free_gb": None,
        }

    # Add error rate
    error_rate = latest.get("error_rate", {})
    rate = error_rate.get("error_rate_per_min", 0)
    components["error_rate"] = {
        "status": "ok" if rate < 10 else "warning",
        "color": "green" if rate < 10 else "yellow",
        "icon": "✅" if rate < 10 else "⚠️",
        "rate_per_min": rate,
        "error_count": error_rate.get("error_count", 0),
    }

    return components


def compute_uptime(metrics: list[dict]) -> dict[str, float]:
    """Compute uptime percentage per component over the metrics window."""
    if not metrics:
        return {}

    health_endpoints = set()
    for m in metrics:
        for name in m.get("health", {}):
            health_endpoints.add(name)

    uptime = {}
    for name in health_endpoints:
        total = 0
        healthy = 0
        for m in metrics:
            result = m.get("health", {}).get(name)
            if result:
                total += 1
                if result.get("status") == "healthy":
                    healthy += 1
        uptime[name] = (healthy / total * 100) if total > 0 else 0.0

    return uptime


# ---------------------------------------------------------------------------
# Streamlit UI
# ---------------------------------------------------------------------------

def render_health_cards(components: dict[str, dict]):
    """Render health status cards for each component."""
    cols = st.columns(min(len(components), 4))
    for i, (name, info) in enumerate(components.items()):
        col = cols[i % len(cols)]
        with col:
            st.markdown(f"### {info['icon']} {name.replace('_', ' ').title()}")
            st.markdown(f"**Status:** <span style='color:{info['color']}'>{info['status'].upper()}</span>", unsafe_allow_html=True)

            if info.get("error"):
                st.error(f"Error: {info['error']}")
            if info.get("response_time_ms") is not None:
                st.caption(f"Response: {info['response_time_ms']:.0f}ms")
            if info.get("backlog_gb") is not None:
                st.caption(f"Backlog: {info['backlog_gb']} GB ({info.get('file_count', 0)} files)")
            if info.get("free_gb") is not None:
                st.caption(f"Free: {info['free_gb']} GB")
            if info.get("rate_per_min") is not None:
                st.caption(f"Error rate: {info['rate_per_min']}/min")


def render_uptime_chart(uptime: dict[str, float]):
    """Render uptime bar chart."""
    if not uptime:
        st.info("No uptime data available. Run the monitor for at least one cycle.")
        return

    fig, ax = plt.subplots(figsize=(10, 4))
    names = list(uptime.keys())
    values = list(uptime.values())
    colors = ["#2ecc71" if v >= 99 else "#f39c12" if v >= 95 else "#e74c3c" for v in values]

    bars = ax.barh(names, values, color=colors, edgecolor="white")
    ax.set_xlim(0, 100)
    ax.set_xlabel("Uptime %")
    ax.set_title("Last 24h Uptime per Component")

    for bar, val in zip(bars, values):
        ax.text(bar.get_width() + 0.5, bar.get_y() + bar.get_height() / 2,
                f"{val:.1f}%", va="center", fontsize=10)

    st.pyplot(fig)
    plt.close(fig)


def render_metrics_graphs(metrics: list[dict]):
    """Render live metrics graphs."""
    if len(metrics) < 2:
        st.info("Need at least 2 data points for graphs.")
        return

    # Extract timestamps
    timestamps = []
    response_times = {}
    backlog_sizes = []
    error_rates = []

    for m in metrics:
        ts_str = m.get("poll_timestamp", "")
        if ts_str:
            try:
                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                timestamps.append(ts)
            except ValueError:
                timestamps.append(None)
        else:
            timestamps.append(None)

        health = m.get("health", {})
        for name, result in health.items():
            rt = result.get("response_time_ms")
            if rt is not None:
                if name not in response_times:
                    response_times[name] = []
                response_times[name].append(rt)
            else:
                if name not in response_times:
                    response_times[name] = []
                response_times[name].append(None)

        backlog = m.get("upload_backlog", {})
        backlog_sizes.append(backlog.get("backlog_gb", 0))

        error_rate = m.get("error_rate", {})
        error_rates.append(error_rate.get("error_rate_per_min", 0))

    # Response time graph
    if response_times:
        fig, ax = plt.subplots(figsize=(10, 4))
        for name, rts in response_times.items():
            valid_ts = [t for t, rt in zip(timestamps, rts) if rt is not None and t is not None]
            valid_rts = [rt for rt in rts if rt is not None]
            if valid_ts and valid_rts:
                ax.plot(valid_ts, valid_rts, marker="o", label=name, linewidth=1.5)

        ax.set_ylabel("Response Time (ms)")
        ax.set_title("API Response Times")
        ax.legend()
        ax.grid(True, alpha=0.3)
        fig.autofmt_xdate()
        st.pyplot(fig)
        plt.close(fig)

    # Backlog + Error rate graph
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 4))

    valid_ts_backlog = [t for t, b in zip(timestamps, backlog_sizes) if t is not None]
    valid_backlog = [b for b in backlog_sizes]
    if valid_ts_backlog:
        ax1.plot(valid_ts_backlog, valid_backlog, marker="o", color="#3498db", linewidth=1.5)
        ax1.axhline(y=100, color="red", linestyle="--", alpha=0.5, label="Threshold (100 GB)")
        ax1.set_ylabel("Backlog (GB)")
        ax1.set_title("Upload Backlog")
        ax1.legend()
        ax1.grid(True, alpha=0.3)

    valid_ts_errors = [t for t, e in zip(timestamps, error_rates) if t is not None]
    valid_errors = [e for e in error_rates]
    if valid_ts_errors:
        ax2.plot(valid_ts_errors, valid_errors, marker="o", color="#e74c3c", linewidth=1.5)
        ax2.axhline(y=10, color="red", linestyle="--", alpha=0.5, label="Threshold (10/min)")
        ax2.set_ylabel("Errors/min")
        ax2.set_title("Error Rate")
        ax2.legend()
        ax2.grid(True, alpha=0.3)

    fig.autofmt_xdate()
    st.pyplot(fig)
    plt.close(fig)


def render_alerts(alerts: list[dict]):
    """Render recent alerts with ack button."""
    if not alerts:
        st.info("No alerts in the last 24 hours.")
        return

    st.subheader("Recent Alerts")

    # Group by alert_id to show latest state
    latest_alerts = {}
    for a in alerts:
        aid = a.get("alert_id", "unknown")
        latest_alerts[aid] = a

    for aid, alert in latest_alerts.items():
        fire_type = alert.get("fire_type", "unknown")
        severity_color = {
            "initial": "red",
            "escalation": "darkred",
            "cleared": "green",
        }.get(fire_type, "gray")

        col1, col2 = st.columns([4, 1])
        with col1:
            st.markdown(
                f"<span style='color:{severity_color};font-weight:bold'>[{fire_type.upper()}]</span> "
                f"Alert ID: `{aid[:8]}` — {alert.get('timestamp', 'N/A')}",
                unsafe_allow_html=True,
            )
        with col2:
            if st.button("Ack", key=f"ack_{aid}"):
                st.session_state[f"acked_{aid}"] = True
                st.rerun()

        if st.session_state.get(f"acked_{aid}"):
            st.success("✅ Acknowledged")
        else:
            st.caption(f"Fire count: {alert.get('fire_count', 1)}")


# ---------------------------------------------------------------------------
# Main Page
# ---------------------------------------------------------------------------

def main():
    st.set_page_config(page_title="Oyster System Health", page_icon="🦪", layout="wide")
    st.title("🦪 Oyster System Health Monitor")

    # Auto-refresh
    st.sidebar.header("Settings")
    refresh_interval = st.sidebar.slider("Auto-refresh (seconds)", 10, 300, 60)
    hours = st.sidebar.slider("Lookback window (hours)", 1, 48, 24)

    # Load data
    metrics = load_metrics(hours=hours)
    alerts = load_alerts(hours=hours)
    thresholds = load_thresholds()

    # Component health
    st.header("Component Health")
    components = compute_component_health(metrics)
    if components:
        render_health_cards(components)
    else:
        st.warning("No metrics data available. Ensure the monitor daemon is running.")

    # Uptime
    st.header("24h Uptime")
    uptime = compute_uptime(metrics)
    render_uptime_chart(uptime)

    # Live metrics graphs
    st.header("Live Metrics")
    render_metrics_graphs(metrics)

    # Recent alerts
    st.header("Alerts")
    render_alerts(alerts)

    # Config info
    with st.sidebar.expander("Threshold Configuration"):
        if thresholds:
            for key, val in thresholds.items():
                if isinstance(val, (int, float)):
                    st.metric(key, val)
        else:
            st.info("No config file found.")

    # Auto-refresh
    time.sleep(refresh_interval)
    st.rerun()


if __name__ == "__main__":
    main()
