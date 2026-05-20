# Tester FAQ

> Frequently asked questions for game data testers.

---

## Table of Contents

- [Getting Started](#getting-started)
- [OAuth & Authentication](#oauth--authentication)
- [Recording & Capture](#recording--capture)
- [Data Upload](#data-upload)
- [Troubleshooting](#troubleshooting)

---

## Getting Started

### Q1: What hardware do I need?

You need a machine capable of running the target game at **1920×1080, 30 fps minimum**.

- **GPU**: NVIDIA GTX 1060 or better (RTX 3060 recommended)
- **RAM**: 16 GB minimum
- **Storage**: 50 GB free SSD space
- **OS**: Windows 10/11, Ubuntu 22.04+, or macOS 13+

---

### Q2: How do I install the recorder?

1. Download the latest installer from the dashboard
2. Run the installer (Windows: `.exe`, macOS: `.dmg`, Linux: `.deb`)
3. Launch the recorder from your system tray
4. Sign in with your tester credentials

---

## OAuth & Authentication

### Q3: How does OAuth login work?

The recorder uses **OAuth 2.0 Authorization Code flow**:

1. Click "Sign In" in the tray menu
2. Your browser opens to the auth page
3. After granting permission, the browser redirects to `http://localhost:8766/callback`
4. The local server exchanges the code for an access token
5. The token is stored securely in your OS keychain

> **Note**: The local callback server only listens on `localhost` — no credentials leave your machine.

---

### Q4: My OAuth token expired. What do I do?

Tokens expire after **24 hours**. Simply click "Sign In" again — the refresh flow is automatic.

If you see `oauth: invalid_grant`, sign out and sign back in to re-authorize.

---

### Q5: Can I use the recorder without an account?

No. All recordings are tied to a tester account for **provenance tracking** and **payment processing**.

---

## Recording & Capture

### Q6: What gets recorded?

The recorder captures:

- **Screen video** (H.264, 30 fps, 1080p)
- **Depth frames** (OpenEXR, if depth buffer is available)
- **Game state events** (via game plugin or overlay)
- **Audio** (optional, requires consent)

---

### Q7: How much disk space does a session use?

Approximately **2-4 GB per hour** of recording, depending on game complexity and depth data.

---

### Q8: Can I pause and resume recording?

Yes. Use the tray menu **Pause / Resume** toggle. Paused time is not counted toward your session quota.

---

## Data Upload

### Q9: When does upload happen?

Upload is **automatic** after each session ends. You can also trigger manual upload from the tray menu.

---

### Q10: What if my internet drops during upload?

The recorder **resumes from where it left off**. Partial uploads are tracked and retried automatically.

---

### Q11: Is my data encrypted in transit?

Yes. All uploads use **HTTPS (TLS 1.3)**. Data is encrypted at rest on our servers as well.

---

## Troubleshooting

### Q12: The recorder won't start

Try these steps:

1. Check that no other instance is running (`ps aux | grep recorder`)
2. Restart the tray daemon: `python3 bin/daemon_control.py restart`
3. Check logs: `cat logs/recorder.log`

See the [Troubleshooting Guide](./TESTER_TROUBLESHOOTING.md) for more details.

---

### Q13: I see a black screen in recordings

This usually means the recorder cannot capture the game window. Ensure:

- The game is running in **windowed** or **borderless fullscreen** mode
- No other screen capture software is active (OBS, ShadowPlay, etc.)
- GPU drivers are up to date

---

### Q14: How do I contact support?

Email **tester-support@gamedata-pipeline.example.com** or open an issue on the internal tracker.
