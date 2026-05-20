# Tester Troubleshooting Guide

This document covers common errors testers may encounter. Each entry provides symptom, root cause, fix, and reference.

---

## Install Issues

### TS-01: Recorder Won't Launch
**Symptom**: Double-clicking the recorder does nothing
**Root cause**: Missing runtime dependencies or corrupted install
**Fix**: Re-run the installer; ensure .NET 8 runtime is installed (Windows) or libgtk-3 (Linux)
**Reference**: [Getting Started](./TESTER_FAQ.md#getting-started)

### TS-02: Tray Icon Missing
**Symptom**: Recorder is running but no tray icon appears
**Root cause**: Desktop environment doesn't support system tray (common on some Linux DEs)
**Fix**: Install `libappindicator` or use the CLI mode: `python3 bin/recorder.py --no-tray`
**Reference**: [Recording & Capture](./TESTER_FAQ.md#recording--capture)

### TS-03: Python Version Mismatch
**Symptom**: `Python 3.10 required, found 3.8.x`
**Root cause**: System Python version too old
**Fix**: Install Python 3.10+ via pyenv: `pyenv install 3.10.12 && pyenv global 3.10.12`
**Reference**: https://github.com/pyenv/pyenv

---

## Authentication Issues

### TS-04: OAuth Login Fails
**Symptom**: Browser opens but login page shows error
**Root cause**: Network firewall blocking auth server or incorrect system time
**Fix**: Check system clock; ensure port 8766 is not blocked by firewall
**Reference**: [OAuth & Authentication](./TESTER_FAQ.md#oauth--authentication)

### TS-05: Token Expired
**Symptom**: `oauth: invalid_grant` or `401 Unauthorized`
**Root cause**: Access token expired (24h TTL)
**Fix**: Sign out and sign back in; the refresh flow is automatic
**Reference**: [OAuth & Authentication](./TESTER_FAQ.md#oauth--authentication)

---

## Recording Issues

### TS-06: Black Screen in Recordings
**Symptom**: Recorded video is entirely black
**Root cause**: Game running in exclusive fullscreen mode
**Fix**: Switch game to **windowed** or **borderless fullscreen** mode
**Reference**: [Recording & Capture](./TESTER_FAQ.md#recording--capture)

### TS-07: Low Frame Rate
**Symptom**: Recorded video drops below 30 fps
**Root cause**: GPU overloaded by game + recorder
**Fix**: Lower game graphics settings; ensure recorder uses hardware encoding (NVENC/AMF)
**Reference**: [Getting Started](./TESTER_FAQ.md#getting-started)

### TS-08: No Depth Data
**Symptom**: Depth frames are missing from session
**Root cause**: Game doesn't expose depth buffer or DepthAnything not installed
**Fix**: Install DepthAnything V2; verify game supports depth output
**Reference**: https://github.com/DepthAnything/Depth-Anything-V2

---

## Upload Issues

### TS-09: Upload Stuck
**Symptom**: Upload progress bar doesn't move
**Root cause**: Network connectivity issue or server-side rate limiting
**Fix**: Check internet connection; wait for rate limit to reset (usually 5 minutes)
**Reference**: [Data Upload](./TESTER_FAQ.md#data-upload)

### TS-10: Upload Failed
**Symptom**: `Upload failed: connection reset`
**Root cause**: Intermittent network failure
**Fix**: Recorder auto-retries; if persistent, check firewall/proxy settings
**Reference**: [Data Upload](./TESTER_FAQ.md#data-upload)

---

## General

### TS-11: High CPU Usage
**Symptom**: Recorder uses >30% CPU
**Root cause**: Software encoding fallback (no hardware encoder available)
**Fix**: Update GPU drivers; ensure NVENC/AMF is enabled in recorder settings
**Reference**: [Getting Started](./TESTER_FAQ.md#getting-started)

### TS-12: Logs Location
**Symptom**: Where can I find recorder logs?
**Root cause**: N/A
**Fix**: Logs are in `logs/recorder.log` (relative to install directory)
**Reference**: [Troubleshooting](./TESTER_FAQ.md#troubleshooting)
