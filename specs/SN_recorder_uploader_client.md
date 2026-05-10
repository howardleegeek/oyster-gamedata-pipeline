---
spec_id: SN_recorder_uploader_client
project: gamedata-recorder
priority: P0
estimated_minutes: 90
depends_on: [SM_backend_mvp, SF_terminator_manifest]
modifies: ["recorder/uploader/", "recorder/auth/", "recorder/config.py"]
executor: glm
---

# SN — Recorder-Side Upload Client

## Goal
Replace manual `~/Documents/OysterClips/` SSH-pull with an autonomous in-recorder upload client that streams every clean session to the SM backend via tus.io chunked upload, retries on flaky networks, respects Wi-Fi-only mode, and refreshes JWT silently. Must run alongside the existing recorder loop on Windows without blocking capture.

## Constraints
- **Runtime**: Python 3.11 inside the existing PyInstaller .exe bundle (recorder is rc12). No new compiled deps unless they're already in `requirements.txt`.
- **Threading**: uploader runs in a background thread/process; never blocks the OBS recording loop. Capture latency budget = 0ms regression.
- **Storage**: persistent retry queue at `%APPDATA%/OysterRecorder/upload_queue.sqlite` so reboots don't lose pending sessions.
- **Bandwidth respect**: Wi-Fi-only is default. Tester can opt into cellular/metered via setting.
- **Iron law**: production-grade error handling. No mock backends. End-state = a real session uploaded to a real Fly.io URL.

## Architecture

```
recorder main loop
    ↓ (writes clip-{ts}.tar.gz + terminator.json)
finalize_session()
    ↓ enqueue
upload_queue.sqlite ──→ uploader thread ──→ tus client ──→ SM backend
                              ↓
                        retry with backoff
                              ↓
                       drop to dead-letter dir if 5x failures
```

## Modules

### 1. `recorder/uploader/queue.py`
SQLite-backed FIFO with these columns:
```sql
CREATE TABLE upload_queue (
    session_id TEXT PRIMARY KEY,
    tarball_path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    bytes INTEGER NOT NULL,
    enqueued_at REAL NOT NULL,
    attempts INTEGER DEFAULT 0,
    last_error TEXT,
    last_attempt_at REAL,
    upload_id TEXT,         -- tus location (NULL until first PATCH)
    upload_offset INTEGER DEFAULT 0,
    state TEXT DEFAULT 'pending'  -- pending|uploading|done|dead
);
```

API: `enqueue(session_id, path)`, `next_pending()`, `mark_uploading(id)`, `update_offset(id, offset)`, `mark_done(id)`, `mark_dead(id, reason)`.

### 2. `recorder/uploader/tus_client.py`
Thin wrapper over `tuspy` (or hand-rolled, since recorder already has `requests`):
- `create_upload(session_id, path, sha256)` → POSTs metadata, returns upload location.
- `upload_chunks(location, path, start_offset)` → PATCH loop with **10 MiB chunks** (configurable, must be ≥10MB per SM spec).
- Honor `Upload-Offset` from server on every chunk response (true offset, ignore client guess).
- Compute and send SHA-256 trailer for tus checksum extension.

### 3. `recorder/uploader/retry.py`
Exponential backoff with jitter:
- attempt 1: immediate
- attempt 2: 30s + jitter(0-10s)
- attempt 3: 2min + jitter
- attempt 4: 10min + jitter
- attempt 5: 1h + jitter
- After attempt 5: move to `%APPDATA%/OysterRecorder/dead_letter/` and log to `errors.log`.

Distinguish error classes:
- **Transient** (retry): connection reset, 5xx, timeout, DNS fail.
- **Permanent** (immediate dead-letter): 401 after refresh fails, 413 (file too big), 422 (manifest invalid).

### 4. `recorder/auth/jwt_manager.py`
- Stores token + refresh-after timestamp in `%APPDATA%/OysterRecorder/auth.json` (chmod 600 equivalent on Windows).
- Provides `get_token()` — returns cached token if `now < refresh_after`, otherwise calls `POST /v1/auth/refresh`.
- On 401 from any request, force-refresh once; if still 401, raise `AuthRevoked` (uploader pauses queue and prompts tester to re-login).
- Refresh runs lazily on demand AND eagerly via daily timer (so a recorder idle for 6 days still has a fresh token when a session finishes).

### 5. `recorder/uploader/network_guard.py`
Network policy enforcement:
- `is_wifi()` — uses `WlanQueryInterface` Win32 API (already pulled in by `pywin32`) to check current connection type.
- `is_metered()` — uses `NetworkInformation.GetInternetConnectionProfile().IsWlanConnectionProfile` and `NetworkCostType` to detect metered links.
- Uploader checks before EACH chunk: if `wifi_only_mode AND (not is_wifi() or is_metered())` → pause queue, log `"network policy paused upload"`, retry check every 60s.

### 6. `recorder/uploader/heartbeat_sender.py`
- Reads `health.json` every 30s (matches recorder's existing heartbeat write cadence).
- POSTs to `/v1/sessions/heartbeat`. Fire-and-forget; no retry queue (next tick will overwrite if the backend missed one).
- Backoff to 5min if 3 consecutive heartbeat POSTs fail (likely offline).

### 7. `recorder/uploader/terminator_sender.py`
- On `terminator.json` write (rc11/12 hook), POST to `/v1/sessions/terminator`.
- Synchronous wait for response (backend returns `quarantined: bool`).
- If `quarantined == true`: emit Windows toast (`win10toast` already bundled): "Session failed: <reason>. Please re-record."
- Even if quarantined, the tarball still uploads (quarantined sessions kept for forensics per SM §3).

## Configuration (`recorder/config.py`)

```python
UPLOAD_BACKEND_URL = "https://gamedata-api.oyster.dev"  # set at build time per env
UPLOAD_CHUNK_SIZE_MB = 10           # ≥10 per SM spec
UPLOAD_WIFI_ONLY = True             # default; tester togglable
UPLOAD_MAX_ATTEMPTS = 5
UPLOAD_PARALLEL_SESSIONS = 1        # serial; don't saturate uplink
HEARTBEAT_INTERVAL_S = 30
JWT_REFRESH_EARLY_S = 3600          # refresh 1h before expiry
```

Settings UI (existing rc11 settings panel) gets two new toggles:
- "Upload over Wi-Fi only" (default ON)
- "Pause uploads" (manual override)

## Verification Criteria

- [ ] Record a clean 6-min session. Tarball appears in R2 within 5min of `terminator.json` write.
- [ ] Disconnect network mid-upload. Reconnect 10min later. Upload resumes from correct offset (verify via R2 multipart parts list — no duplicates, total bytes match SHA-256).
- [ ] Toggle Wi-Fi-only ON, switch to phone hotspot (metered). Uploader pauses; toggle OFF, uploader resumes.
- [ ] Force JWT to expire (set short exp on backend). Recorder upload triggers refresh transparently; no user prompt.
- [ ] Revoke tester via backend `testers.revoked_at`. Recorder receives 401 after refresh attempt → emits toast "Login required" and pauses queue.
- [ ] Kill recorder mid-upload (Task Manager). Restart. Queue resumes pending session from `upload_queue.sqlite`.
- [ ] Crash a session (kill MC). Verify `terminator.json` POSTs with `reason=mc_died`, backend returns `quarantined=true`, tester sees toast.
- [ ] Capture latency: record while uploader is active under 4G throttle. Verify OBS frame drops == 0 (uploader is truly out-of-band).
- [ ] 5 transient failures → session moves to dead-letter dir, recorder keeps running for next session.
- [ ] `pytest recorder/tests/uploader/` all green; coverage ≥80%.
- [ ] Bundle into rc13 .exe via existing PyInstaller pipeline; smoke test on a clean Windows VM.

## Do NOT
- Do not block the OBS capture loop. Uploader is a separate thread/process or the entire spec fails.
- Do not implement custom resumable protocol. Use tus.io.
- Do not store JWT in plaintext outside `%APPDATA%`.
- Do not silently delete tarballs after upload; keep local copy until backend `status=accepted` is confirmed via `GET /v1/sessions/{id}/status`. Reaper job (separate, future spec) handles deletion.
- Do not POST heartbeats while session inactive (no MC window). Skip silently.
- Do not invent a UI redesign — only add the two toggles to existing settings panel.
