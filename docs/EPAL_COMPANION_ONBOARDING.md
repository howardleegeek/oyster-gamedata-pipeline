# EPal Companion Onboarding Guide

> **Document ID:** G258-EPAL-COMP-ONBOARD  
> **Version:** 1.0.0  
> **Last Updated:** 2025-01-01  
> **Audience:** EPal Companions (service providers)

---

## Table of Contents

1. [Welcome](#1-welcome)
2. [What Is Session Recording?](#2-what-is-session-recording)
3. [How to Enable Recording](#3-how-to-enable-recording)
4. [What Gets Recorded](#4-what-gets-recorded)
5. [What Is NOT Recorded](#5-what-is-not-recorded)
6. [Anti-Cheat FAQ](#6-anti-cheat-faq)
7. [Bonus Calculation](#7-bonus-calculation)
8. [Opt-Out Anytime](#8-opt-out-anytime)
9. [Troubleshooting](#9-troubleshooting)
10. [Support & Contact](#10-support--contact)

---

## 1. Welcome

Welcome to the EPal Companion platform! This guide walks you through the **session recording** feature — an optional tool that helps ensure fair play, accurate bonus payouts, and a better experience for both companions and buyers.

**Key things to know upfront:**

- Recording is **100% optional** — you can opt out at any time.
- Only **gameplay and voice audio** are captured; personal files are never accessed.
- Recordings are used solely for **quality assurance, dispute resolution, and bonus verification**.
- You retain full control over your recording settings.

---

## 2. What Is Session Recording?

Session recording captures a lightweight snapshot of your EPal gaming sessions. When enabled, the companion app records:

- **Screen activity** within the game window only.
- **Voice chat audio** between you and the buyer during the session.
- **Session metadata** such as start time, end time, game title, and session ID.

These recordings are encrypted at rest and stored securely on EPal servers. They are automatically deleted after **30 days** unless flagged for dispute review.

---

## 3. How to Enable Recording

Follow these steps to turn on session recording in the EPal companion app:

### Step 1 — Open Settings

1. Launch the **EPal Companion App** on your device.
2. Tap the **gear icon** (⚙️) in the top-right corner to open **Settings**.

### Step 2 — Navigate to Recording

1. In the left sidebar, select **Privacy & Recording**.
2. Toggle the switch labeled **"Enable Session Recording"** to **ON** (green).

### Step 3 — Configure Preferences

You can customize the following options:

| Setting | Description | Default |
|---|---|---|
| **Record Screen** | Capture in-game video | ON |
| **Record Audio** | Capture voice chat | ON |
| **Quality** | Low / Medium / High | Medium |
| **Auto-Start** | Begin recording when session starts | ON |
| **Notify Buyer** | Show buyer a recording indicator | ON |

### Step 4 — Confirm

1. Tap **Save** at the bottom of the page.
2. A confirmation banner will appear: *"Recording enabled for future sessions."*

> **Note:** Recording only applies to sessions that start **after** you enable the setting. Active sessions are not retroactively recorded.

---

## 4. What Gets Recorded

When session recording is enabled, the following data is captured:

| Data Type | Details |
|---|---|
| **Game Window Video** | Only the active game window is recorded. Desktop, overlays, and other apps are excluded. |
| **Voice Audio** | Two-way voice communication between companion and buyer via EPal's built-in voice channel. |
| **Session Metadata** | Session ID, start/end timestamps, game title, buyer ID (hashed), companion ID (hashed). |
| **Chat Logs** | In-app text messages exchanged during the session. |
| **Performance Metrics** | Frame rate, latency, and connection quality indicators. |

All recordings are **AES-256 encrypted** during upload and storage.

---

## 5. What Is NOT Recorded

We take your privacy seriously. The following are **never** captured:

- ❌ **Desktop outside the game window** — other applications, browser tabs, or notifications are not recorded.
- ❌ **System audio** — music, podcasts, or other background audio playing on your device.
- ❌ **Webcam / face camera** — no video of you is captured unless you explicitly enable a separate face-cam feature.
- ❌ **File system access** — the recorder does not scan, read, or index any files on your device.
- ❌ **Keystrokes or passwords** — input events are not logged.
- ❌ **Personal identifiers** — your real name, email, or payment details are never embedded in recordings.

---

## 6. Anti-Cheat FAQ

### Q: Does recording affect game anti-cheat systems?

**A:** No. The EPal recorder operates at the **application layer** and does not inject code, modify game memory, or hook into game processes. It is fully compatible with all major anti-cheat systems including:

- Easy Anti-Cheat (EAC)
- BattlEye
- Vanguard
- Ricochet

### Q: Will I get banned for using the recorder?

**A:** Absolutely not. The recorder is a **passive screen/audio capture tool** — it behaves identically to built-in tools like NVIDIA ShadowPlay or OBS. Game publishers recognize it as a legitimate overlay.

### Q: Does the recorder run with elevated/admin privileges?

**A:** No. The recorder runs under your **standard user account** with no elevated permissions. It only accesses the game window surface and EPal's own audio channel.

### Q: What if a game's anti-cheat flags the recorder?

**A:** In the rare event this occurs:

1. The recorder will **auto-pause** and notify you.
2. Your session continues normally without recording.
3. You can report the incident via **Settings → Help → Report Issue**.
4. Our engineering team will investigate and release a compatibility patch if needed.

### Q: Can game developers access my recordings?

**A:** No. Recordings are **EPal-internal only**. Game publishers have zero access to recorded content.

---

## 7. Bonus Calculation

Session recordings play a role in our **performance bonus program**. Here's how it works:

### Bonus Tiers

| Tier | Criteria | Bonus Multiplier |
|---|---|---|
| **Bronze** | ≥ 10 sessions/month, ≥ 4.0 avg rating | 1.05× |
| **Silver** | ≥ 25 sessions/month, ≥ 4.3 avg rating | 1.10× |
| **Gold** | ≥ 50 sessions/month, ≥ 4.5 avg rating | 1.20× |
| **Platinum** | ≥ 100 sessions/month, ≥ 4.7 avg rating | 1.35× |

### How Recordings Factor In

- **Verification:** Recordings are used to verify session authenticity and prevent fraudulent claims.
- **Quality Scoring:** AI-assisted analysis of recordings contributes to your **quality score** (engagement, responsiveness, session completion rate).
- **Dispute Protection:** If a buyer disputes a session, recordings serve as objective evidence to protect your earnings.

### Payout Schedule

- Bonuses are calculated on the **1st of each month** for the prior month's activity.
- Payouts are issued within **5 business days** via your registered payment method.
- You can view your bonus progress in **Dashboard → Earnings → Bonus Tracker**.

> **Important:** You do **not** need recording enabled to receive base session payments. Recording only affects **bonus eligibility** and **dispute resolution**.

---

## 8. Opt-Out Anytime

You can disable session recording at any point — no questions asked, no penalties applied.

### How to Opt Out

1. Open **Settings → Privacy & Recording**.
2. Toggle **"Enable Session Recording"** to **OFF** (gray).
3. Tap **Save**.

### What Happens When You Opt Out

- ✅ **Future sessions** will not be recorded.
- ✅ **Existing recordings** are retained for 30 days (or until any open dispute is resolved), then permanently deleted.
- ✅ **Base payments** are unaffected — you continue earning normally.
- ⚠️ **Bonus eligibility** may be impacted if recordings are required for quality verification in your tier.
- ⚠️ **Dispute protection** is reduced — without recordings, disputes are resolved based on available metadata only.

### Permanent Data Deletion

If you wish to have all your recordings deleted immediately:

1. Go to **Settings → Privacy & Recording → Manage Data**.
2. Select **"Delete All Recordings"**.
3. Confirm with your account password.
4. Deletion is processed within **24 hours** and is irreversible.

---

## 9. Troubleshooting

### Recording Won't Start

| Symptom | Solution |
|---|---|
| Toggle is grayed out | Ensure app is updated to v2.4+. Restart the app. |
| "Recording failed" error | Check disk space (need ≥ 500 MB free). Verify game is running in windowed or borderless mode. |
| No audio in recording | Confirm microphone permissions are granted in OS settings. |

### High CPU Usage During Recording

- Lower the **Quality** setting to **Low** in recording preferences.
- Close unnecessary background applications.
- Ensure your GPU drivers are up to date.

### Recording File Too Large

- Switch quality from **High** to **Medium** or **Low**.
- Enable **hardware encoding** (Settings → Recording → Use GPU Encoder).
- Recordings are automatically compressed; typical size is ~150 MB/hour at Medium quality.

### Buyer Cannot See Recording Indicator

- Ensure **"Notify Buyer"** is enabled in your recording settings.
- The indicator appears as a small red dot in the buyer's session view.

---

## 10. Support & Contact

If you have questions or need assistance:

| Channel | Details |
|---|---|
| **In-App Help** | Settings → Help → Contact Support |
| **Email** | companion-support@epal.gg |
| **Discord** | discord.gg/epal-companions |
| **Knowledge Base** | help.epal.gg/companion-recording |
| **Response Time** | Within 24 hours (business days) |

---

*This document is maintained by the EPal Companion Experience team. For version history and changelog, visit the internal wiki.*

*© 2025 EPal. All rights reserved.*
