#!/usr/bin/env python3
"""
vendor_portal_static_site.py — Static SPA generator for vendor portal.

Generates a self-contained HTML + JS single-page application that vendors
use to sign in via OAuth and view their earnings, uploaded clips, and
a recorder download link.  No server-side runtime is required after
generation; the output is pure static assets.

Usage:
    python3 bin/vendor_portal_static_site.py --output-dir ./dist \
        --oauth-client-id <id> --oauth-issuer <url> \
        --api-base-url <url> --brand-name "Vendor Portal"

Author: G192 automation
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
import textwrap
from pathlib import Path
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Configuration data-class (pure stdlib, no pydantic)
# ---------------------------------------------------------------------------

class SiteConfig:
    """Holds all parameters needed to render the static SPA."""

    def __init__(
        self,
        output_dir: str,
        oauth_client_id: str,
        oauth_issuer: str,
        oauth_redirect_uri: str,
        api_base_url: str,
        brand_name: str,
        recorder_download_url: str,
        primary_color: str,
        favicon_data_url: str,
    ) -> None:
        self.output_dir = output_dir
        self.oauth_client_id = oauth_client_id
        self.oauth_issuer = oauth_issuer.rstrip("/")
        self.oauth_redirect_uri = oauth_redirect_uri
        self.api_base_url = api_base_url.rstrip("/")
        self.brand_name = brand_name
        self.recorder_download_url = recorder_download_url
        self.primary_color = primary_color
        self.favicon_data_url = favicon_data_url

    def to_dict(self) -> Dict[str, Any]:
        """Serialise config for embedding into the generated JS bundle."""
        return {
            "oauthClientId": self.oauth_client_id,
            "oauthIssuer": self.oauth_issuer,
            "oauthRedirectUri": self.oauth_redirect_uri,
            "apiBaseUrl": self.api_base_url,
            "brandName": self.brand_name,
            "recorderDownloadUrl": self.recorder_download_url,
            "primaryColor": self.primary_color,
        }


# ---------------------------------------------------------------------------
# HTML template
# ---------------------------------------------------------------------------

def _build_html(config: SiteConfig) -> str:
    """Return the complete HTML document string."""
    return textwrap.dedent("""\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>{brand_name}</title>
<link rel="icon" href="{favicon}" type="image/svg+xml"/>
<style>
:root {{
  --primary: {primary_color};
  --bg: #f4f6f9;
  --card: #ffffff;
  --text: #1e293b;
  --muted: #64748b;
  --radius: 12px;
  --shadow: 0 1px 3px rgba(0,0,0,.08);
}}
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  background: var(--bg);
  color: var(--text);
  min-height: 100vh;
  display: flex;
  flex-direction: column;
}}
header {{
  background: var(--primary);
  color: #fff;
  padding: 1rem 1.5rem;
  display: flex;
  align-items: center;
  justify-content: space-between;
  box-shadow: var(--shadow);
}}
header h1 {{ font-size: 1.25rem; font-weight: 600; }}
header .user-info {{ font-size: .85rem; opacity: .9; }}
main {{ flex: 1; padding: 1.5rem; max-width: 960px; margin: 0 auto; width: 100%; }}
.card {{
  background: var(--card);
  border-radius: var(--radius);
  box-shadow: var(--shadow);
  padding: 1.5rem;
  margin-bottom: 1.25rem;
}}
.card h2 {{ font-size: 1rem; color: var(--muted); margin-bottom: .75rem; text-transform: uppercase; letter-spacing: .04em; }}
.card .value {{ font-size: 2rem; font-weight: 700; }}
.card .sub {{ font-size: .85rem; color: var(--muted); margin-top: .25rem; }}
.grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 1.25rem; }}
.btn {{
  display: inline-flex; align-items: center; gap: .5rem;
  padding: .65rem 1.25rem; border: none; border-radius: 8px;
  font-size: .95rem; font-weight: 600; cursor: pointer;
  transition: opacity .15s;
}}
.btn:hover {{ opacity: .85; }}
.btn-primary {{ background: var(--primary); color: #fff; }}
.btn-outline {{ background: transparent; border: 2px solid var(--primary); color: var(--primary); }}
.btn-danger {{ background: #ef4444; color: #fff; }}
table {{ width: 100%; border-collapse: collapse; margin-top: .75rem; }}
th, td {{ text-align: left; padding: .6rem .75rem; border-bottom: 1px solid #e2e8f0; font-size: .9rem; }}
th {{ color: var(--muted); font-weight: 600; }}
#login-screen {{
  display: flex; flex-direction: column; align-items: center;
  justify-content: center; min-height: 100vh; text-align: center;
}}
#login-screen h1 {{ font-size: 1.75rem; margin-bottom: .5rem; }}
#login-screen p {{ color: var(--muted); margin-bottom: 1.5rem; }}
#dashboard {{ display: none; }}
.spinner {{
  width: 24px; height: 24px; border: 3px solid #e2e8f0;
  border-top-color: var(--primary); border-radius: 50%;
  animation: spin .6s linear infinite; display: inline-block;
}}
@keyframes spin {{ to {{ transform: rotate(360deg); }} }}
.toast {{
  position: fixed; bottom: 1.5rem; right: 1.5rem;
  background: #1e293b; color: #fff; padding: .75rem 1.25rem;
  border-radius: 8px; font-size: .9rem; opacity: 0;
  transition: opacity .3s; pointer-events: none;
}}
.toast.show {{ opacity: 1; }}
footer {{ text-align: center; padding: 1rem; color: var(--muted); font-size: .8rem; }}
</style>
</head>
<body>

<!-- Login screen -->
<div id="login-screen">
  <h1>{brand_name}</h1>
  <p>Sign in with your vendor account to view earnings and clips.</p>
  <button class="btn btn-primary" id="btn-login" onclick="App.login()">
    Sign in with OAuth
  </button>
</div>

<!-- Dashboard -->
<div id="dashboard">
  <header>
    <h1>{brand_name}</h1>
    <span class="user-info" id="user-display"></span>
  </header>
  <main>
    <div class="grid">
      <div class="card">
        <h2>Total Earnings</h2>
        <div class="value" id="earnings-value">—</div>
        <div class="sub" id="earnings-period"></div>
      </div>
      <div class="card">
        <h2>Clips Uploaded</h2>
        <div class="value" id="clips-value">—</div>
        <div class="sub" id="clips-period"></div>
      </div>
      <div class="card">
        <h2>Recorder</h2>
        <div class="sub" style="margin-bottom:.75rem">Download the latest recorder build.</div>
        <a class="btn btn-primary" id="recorder-link" href="#" target="_blank" rel="noopener">
          ⬇ Download Recorder
        </a>
      </div>
    </div>

    <div class="card">
      <h2>Recent Clips</h2>
      <div id="clips-loading"><span class="spinner"></span> Loading…</div>
      <table id="clips-table" style="display:none">
        <thead><tr><th>Filename</th><th>Duration</th><th>Uploaded</th><th>Status</th></tr></thead>
        <tbody id="clips-tbody"></tbody>
      </table>
    </div>

    <div class="card">
      <h2>Earnings History</h2>
      <div id="earnings-loading"><span class="spinner"></span> Loading…</div>
      <table id="earnings-table" style="display:none">
        <thead><tr><th>Date</th><th>Amount</th><th>Source</th></tr></thead>
        <tbody id="earnings-tbody"></tbody>
      </table>
    </div>
  </main>
  <footer>&copy; {brand_name} — Static Vendor Portal</footer>
</div>

<div class="toast" id="toast"></div>

<script>
"use strict";
/* ---------- embedded config (generated) ---------- */
const CONFIG = {config_json};

/* ---------- minimal OAuth2 implicit-flow helper ---------- */
const OAuth = {{
  authorizeUrl() {{
    return CONFIG.oauthIssuer + "/authorize?" + new URLSearchParams({{
      response_type: "token",
      client_id: CONFIG.oauthClientId,
      redirect_uri: CONFIG.oauthRedirectUri,
      scope: "openid profile email",
      state: Math.random().toString(36).slice(2),
    }}).toString();
  }},

  parseHash() {{
    const hash = window.location.hash.slice(1);
    if (!hash) return null;
    const params = new URLSearchParams(hash);
    const token = params.get("access_token");
    if (!token) return null;
    return {{
      accessToken: token,
      expiresIn: parseInt(params.get("expires_in") || "3600", 10),
    }};
  }},
}};

/* ---------- API client ---------- */
const API = {{
  async _fetch(path, token) {{
    const res = await fetch(CONFIG.apiBaseUrl + path, {{
      headers: {{ Authorization: "Bearer " + token }},
    }});
    if (!res.ok) throw new Error("API " + res.status);
    return res.json();
  }},
  async getEarnings(token) {{ return this._fetch("/vendor/earnings", token); }},
  async getClips(token)    {{ return this._fetch("/vendor/clips", token); }},
  async getProfile(token)  {{ return this._fetch("/vendor/profile", token); }},
}};

/* ---------- App ---------- */
const App = {{
  token: null,

  login() {{
    window.location.href = OAuth.authorizeUrl();
  }},

  logout() {{
    this.token = null;
    window.location.hash = "";
    window.location.reload();
  }},

  async init() {{
    const creds = OAuth.parseHash();
    if (creds) {{
      this.token = creds.accessToken;
      // Clean hash so token isn't visible in URL bar
      history.replaceState(null, "", window.location.pathname);
      await this.showDashboard();
    }}
  }},

  async showDashboard() {{
    document.getElementById("login-screen").style.display = "none";
    document.getElementById("dashboard").style.display = "block";

    try {{
      const profile = await API.getProfile(this.token);
      document.getElementById("user-display").textContent =
        profile.email || profile.name || "Vendor";
    }} catch (e) {{
      this._toast("Failed to load profile: " + e.message);
    }}

    // Recorder link
    document.getElementById("recorder-link").href = CONFIG.recorderDownloadUrl;

    // Load earnings
    this._loadEarnings();
    // Load clips
    this._loadClips();
  }},

  async _loadEarnings() {{
    try {{
      const data = await API.getEarnings(this.token);
      document.getElementById("earnings-value").textContent =
        "$" + (data.total || 0).toFixed(2);
      document.getElementById("earnings-period").textContent =
        data.period || "All time";
      const tbody = document.getElementById("earnings-tbody");
      tbody.innerHTML = "";
      (data.history || []).forEach((row) => {{
        const tr = document.createElement("tr");
        tr.innerHTML =
          "<td>" + this._esc(row.date) + "</td>" +
          "<td>$" + (row.amount || 0).toFixed(2) + "</td>" +
          "<td>" + this._esc(row.source || "—") + "</td>";
        tbody.appendChild(tr);
      }});
      document.getElementById("earnings-loading").style.display = "none";
      document.getElementById("earnings-table").style.display = "";
    }} catch (e) {{
      this._toast("Earnings load failed: " + e.message);
      document.getElementById("earnings-loading").textContent = "Error loading earnings.";
    }}
  }},

  async _loadClips() {{
    try {{
      const data = await API.getClips(this.token);
      document.getElementById("clips-value").textContent = data.total || 0;
      document.getElementById("clips-period").textContent =
        data.period || "All time";
      const tbody = document.getElementById("clips-tbody");
      tbody.innerHTML = "";
      (data.clips || []).forEach((clip) => {{
        const tr = document.createElement("tr");
        tr.innerHTML =
          "<td>" + this._esc(clip.filename) + "</td>" +
          "<td>" + this._esc(clip.duration || "—") + "</td>" +
          "<td>" + this._esc(clip.uploaded_at || "—") + "</td>" +
          "<td>" + this._esc(clip.status || "—") + "</td>";
        tbody.appendChild(tr);
      }});
      document.getElementById("clips-loading").style.display = "none";
      document.getElementById("clips-table").style.display = "";
    }} catch (e) {{
      this._toast("Clips load failed: " + e.message);
      document.getElementById("clips-loading").textContent = "Error loading clips.";
    }}
  }},

  _esc(s) {{
    const d = document.createElement("div");
    d.textContent = s;
    return d.innerHTML;
  }},

  _toast(msg) {{
    const el = document.getElementById("toast");
    el.textContent = msg;
    el.classList.add("show");
    setTimeout(() => el.classList.remove("show"), 4000);
  }},
}};

/* ---------- boot ---------- */
document.addEventListener("DOMContentLoaded", () => App.init());
</script>
</body>
</html>
""").format(
        brand_name=_html_escape(config.brand_name),
        primary_color=config.primary_color,
        favicon=config.favicon_data_url,
        config_json=json.dumps(config.to_dict(), indent=2),
    )


def _html_escape(text: str) -> str:
    """Minimal HTML entity escaping for template interpolation."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


# ---------------------------------------------------------------------------
# Favicon generator (inline SVG data-URL)
# ---------------------------------------------------------------------------

def _default_favicon_data_url(primary_color: str) -> str:
    """Return a data-URL for a simple SVG favicon matching the brand colour."""
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
        '<rect width="64" height="64" rx="12" fill="{c}"/>'
        '<text x="32" y="44" text-anchor="middle" fill="#fff" '
        'font-size="36" font-family="sans-serif" font-weight="bold">V</text>'
        '</svg>'
    ).format(c=primary_color)
    import base64
    encoded = base64.b64encode(svg.encode()).decode()
    return "data:image/svg+xml;base64," + encoded


# ---------------------------------------------------------------------------
# File writer
# ---------------------------------------------------------------------------

def _write_site(config: SiteConfig) -> Path:
    """Generate the static site into *config.output_dir* and return the path."""
    out = Path(config.output_dir)
    out.mkdir(parents=True, exist_ok=True)

    html_content = _build_html(config)
    index_path = out / "index.html"
    index_path.write_text(html_content, encoding="utf-8")

    # Write a small manifest so downstream tooling knows what was generated
    manifest: Dict[str, Any] = {
        "generated_at": _utc_iso_now(),
        "files": ["index.html"],
        "config_hash": hashlib.sha256(
            json.dumps(config.to_dict(), sort_keys=True).encode()
        ).hexdigest()[:12],
    }
    manifest_path = out / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )

    return index_path


def _utc_iso_now() -> str:
    """Return current UTC time in ISO-8601 without external deps."""
    import datetime
    return datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate a static vendor-portal SPA (HTML + JS).",
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        help="Directory where index.html + manifest.json will be written.",
    )
    parser.add_argument(
        "--oauth-client-id",
        required=True,
        help="OAuth 2.0 client identifier.",
    )
    parser.add_argument(
        "--oauth-issuer",
        required=True,
        help="OAuth issuer base URL (e.g. https://auth.example.com).",
    )
    parser.add_argument(
        "--oauth-redirect-uri",
        required=True,
        help="OAuth redirect URI registered with the IdP.",
    )
    parser.add_argument(
        "--api-base-url",
        required=True,
        help="Base URL for the vendor REST API.",
    )
    parser.add_argument(
        "--brand-name",
        default="Vendor Portal",
        help="Display name for the portal (default: 'Vendor Portal').",
    )
    parser.add_argument(
        "--recorder-download-url",
        default="#",
        help="URL for the recorder download button.",
    )
    parser.add_argument(
        "--primary-color",
        default="#2563eb",
        help="Primary brand colour as hex (default: #2563eb).",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    """Entry-point: parse CLI args, generate the static site, return exit code."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    config = SiteConfig(
        output_dir=args.output_dir,
        oauth_client_id=args.oauth_client_id,
        oauth_issuer=args.oauth_issuer,
        oauth_redirect_uri=args.oauth_redirect_uri,
        api_base_url=args.api_base_url,
        brand_name=args.brand_name,
        recorder_download_url=args.recorder_download_url,
        primary_color=args.primary_color,
        favicon_data_url=_default_favicon_data_url(args.primary_color),
    )

    try:
        index_path = _write_site(config)
    except OSError as exc:
        print(f"Error writing site: {exc}", file=sys.stderr)
        return 1

    print(f"Static site generated → {index_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
