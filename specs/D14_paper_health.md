# D14 — Paper server health probe
Implement `bin/paper_health_probe.py --host localhost --port 25565`. Sends a Server List Ping (handshake → status → pong) and reports latency, online-mode, max-players, version. Pure stdlib socket. Tests: unreachable → fail, OK → JSON.
