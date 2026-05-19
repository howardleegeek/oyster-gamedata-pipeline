# Consumer Trust & Transparency

> **Last updated:** 2025-01-01 · **Version:** 1.0.0

---

## What Is This Software?

This application is a **desktop utility** designed to assist with productivity and system management tasks. It runs locally on your machine and is accessible from the system tray menu. We believe in full transparency about what the software does, what data it handles, and how your privacy is protected.

---

## What We Record

The following data may be collected during normal operation. All collection is purposeful, minimal, and disclosed here.

### 1. Usage Telemetry (Opt-In)

| Data Point | Purpose | Retention |
|---|---|---|
| Feature interaction counts | Understand which features are used most | 90 days, aggregated |
| Session duration | Measure stability and performance trends | 90 days, aggregated |
| Error stack traces (anonymized) | Diagnose and fix crashes | Until resolved |
| OS version and app version | Ensure compatibility across environments | 90 days, aggregated |

- Telemetry is **opt-in** and can be disabled at any time via Settings → Privacy.
- No personally identifiable information (PII) is included in telemetry payloads.
- All telemetry is transmitted over HTTPS (TLS 1.2+).

### 2. Local Configuration

| Data Point | Purpose | Storage |
|---|---|---|
| User preferences (theme, hotkeys, etc.) | Persist your settings between sessions | Local config file only |
| License / activation state | Verify legitimate use | Local config file only |
| Last-update timestamp | Check for available updates | Local config file only |

- Configuration files are stored in the standard OS application-data directory.
- No configuration data is uploaded to any remote server.

### 3. Crash Reports (Opt-In)

- When the application crashes, a **minidump** may be generated containing:
  - Memory state at the time of the crash
  - Loaded module list (DLLs / shared libraries)
  - Thread stack traces
- Crash reports are **opt-in** and require explicit user consent.
- All crash data is anonymized before transmission.

---

## What We Do NOT Record

We want to be explicit about what this software **never** collects, stores, or transmits:

- **Keystrokes** — We do not log, capture, or monitor any keyboard input.
- **Screen content** — We do not take screenshots, record your display, or capture window contents.
- **Clipboard data** — We do not read or store clipboard contents.
- **Microphone / camera** — We do not access audio or video input devices.
- **File contents** — We do not read, index, or upload the contents of your personal files.
- **Browsing history** — We do not monitor or record web browser activity.
- **Network traffic** — We do not intercept, log, or analyze your network packets.
- **Location data** — We do not collect GPS, Wi-Fi triangulation, or IP-based geolocation.
- **Contacts / address book** — We do not access or enumerate your contacts.
- **Credentials / passwords** — We do not store, capture, or transmit any passwords, tokens, or secrets.

If you discover any behavior that contradicts the above, please report it immediately via the contact channels listed below.

---

## Anti-Cheat & Integrity Statement

This software is designed for **legitimate productivity use only**. We maintain the following integrity commitments:

### No Game Interference

- This application does **not** interact with, modify, or inject code into video games.
- It does **not** hook into game processes, read game memory, or alter game state.
- It does **not** provide any unfair advantage in multiplayer or competitive environments.

### Process Isolation

- The application runs in its own process space with standard user-level permissions.
- It does **not** require elevated (administrator / root) privileges for normal operation.
- It does **not** install kernel-mode drivers or system-level hooks.

### Transparency

- The source code is available for review on GitHub (see link below).
- Binary builds are reproducible and can be verified against source.
- We welcome independent security audits and community review.

### If You Use Anti-Cheat Software

- This application is designed to be compatible with common anti-cheat systems.
- It does **not** trigger false-positive detections under normal operation.
- If you experience conflicts with anti-cheat software, please report the issue so we can investigate and resolve it.

---

## Privacy Summary

### Data Processing Principles

1. **Minimization** — We collect only what is necessary for the software to function and improve.
2. **Transparency** — All data collection is documented here and in the application settings.
3. **User Control** — You can disable telemetry and crash reporting at any time.
4. **Security** — All data in transit is encrypted with TLS 1.2 or higher.
5. **No Sale of Data** — We do not sell, rent, or trade your data to third parties.
6. **No Advertising** — This software contains no ads, trackers, or ad-related SDKs.

### Data Retention

| Data Type | Retention Period | Deletion Method |
|---|---|---|
| Telemetry (aggregated) | 90 days | Automatic purge |
| Crash reports | Until issue resolved | Manual review then deletion |
| Local configuration | Until user uninstalls | Removed on uninstall |
| Update check logs | 30 days | Automatic purge |

### Your Rights

Depending on your jurisdiction, you may have the right to:

- **Access** — Request a copy of any data we hold about you.
- **Rectify** — Request correction of inaccurate data.
- **Erase** — Request deletion of your data ("right to be forgotten").
- **Port** — Request your data in a machine-readable format.
- **Object** — Object to processing of your data for certain purposes.
- **Restrict** — Request limitation of processing of your data.

To exercise any of these rights, contact us using the information below.

### Third-Party Services

This software may interact with the following third-party services:

| Service | Purpose | Privacy Policy |
|---|---|---|
| GitHub | Source code hosting, issue tracking | <https://docs.github.com/en/site-policy/privacy-policies/github-privacy-statement> |
| (None currently) | — | — |

We do not embed any third-party analytics, advertising, or tracking SDKs.

---

## Open Source

This project is open source and available on GitHub:

- **Repository:** <https://github.com/your-org/your-repo>
- **License:** MIT License
- **Contributing:** See `CONTRIBUTING.md` in the repository
- **Security disclosures:** See `SECURITY.md` in the repository

We welcome community contributions, bug reports, and security disclosures. All contributions are reviewed by maintainers before merging.

---

## Contact

If you have questions, concerns, or feedback about this trust page or the software's data practices:

| Channel | Details |
|---|---|
| **Email** | privacy@your-org.example.com |
| **GitHub Issues** | <https://github.com/your-org/your-repo/issues> |
| **Security** | security@your-org.example.com (for vulnerability reports) |
| **General Support** | support@your-org.example.com |

We aim to respond to all inquiries within **5 business days**.

---

## Changelog

| Date | Version | Changes |
|---|---|---|
| 2025-01-01 | 1.0.0 | Initial trust page publication |

---

*This document is part of our commitment to transparency. It will be updated as the software evolves. Significant changes will be noted in the changelog above and communicated through the application's update mechanism.*
