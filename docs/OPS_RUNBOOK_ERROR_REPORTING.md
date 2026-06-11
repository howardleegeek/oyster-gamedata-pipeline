# Ops Runbook: Error Reporting Service (W28 / G231–G240)

Owner: Cluster ops (rotating)
Severity scale: P0 (recorder fleet silently crashing, no telemetry) →
P3 (cosmetic dashboard issue)
Endpoints:
- `POST /api/error-report` (recorder client → us)
- `GET  /api/error-report/summary?since=24h&limit=50` (ops dashboard)

Storage:
- Production: Supabase Postgres, table `public.error_reports`
- Local / tests: SQLite via `bin/error_report_service.py`

CLI: `python -m bin.error_report_service record|summary --db ...`

---

## 1. What this service does

When the recorder crashes, the global `sys.excepthook` (G237 — see
`bin/auto_install_error_handler.py`) ships a redacted stack trace to
this endpoint. The service:

1. validates the body shape and sizes,
2. **scrubs PII** (filesystem paths, usernames, machine names, IPv4,
   email addresses) from the stack trace + context BEFORE persistence,
3. computes a stable **fingerprint** hash from the scrubbed stack +
   OS family + recorder major version,
4. upserts: first sighting inserts a row, subsequent sightings
   increment `count` and bump `last_seen` (same crash from 1000
   testers = 1 row).

Privacy contract:
- `anon_id` is opaque; tied to install, NOT to tester identity (G220
  privacy spec).
- No raw paths, usernames, IPs, or emails are ever persisted.
- The table is RLS-locked: only the Supabase service role can read or
  write. The summary endpoint proxies via service role on the server
  side, so buyer/tester clients never touch raw data.

---

## 2. Request / response shapes

### POST /api/error-report

```json
{
  "recorder_version": "v0.28.0-rc19.0.1",
  "os":               "windows-11-build-22631",
  "stack_trace":      "Traceback ...",
  "context":          { "game": "minecraft", "clip_id": "..." },
  "anon_id":          "abc-123",
  "severity":         "crash"
}
```

Response:
```json
{
  "fingerprint":  "93493458d4b931a316d1a848e1d4c3c0",
  "count":        7,
  "duplicate":    true,
  "last_seen":    "2026-05-13T23:23:32.110Z"
}
```

### GET /api/error-report/summary?since=24h&limit=50

```json
{
  "since": "2026-05-12T16:14:00.000Z",
  "count": 23,
  "rows": [
    {
      "fingerprint":           "...",
      "first_seen":            "...",
      "last_seen":             "...",
      "count":                 142,
      "recorder_version":      "v0.28.0-rc19.0.1",
      "os":                    "windows-11-build-22631",
      "severity":              "crash",
      "stack_trace_preview":   "(first 240 chars, scrubbed)"
    }
  ]
}
```

`since` accepts: `30s`, `15m`, `24h`, `7d`. Omit for all-time.
`limit` accepts: 1..500 (default 50).

---

## 3. Rate limits

| Scope | Limit | Why |
|-------|-------|-----|
| Per IP | 60 / hour | Stops a buggy recorder from DoS'ing the service |
| Per `anon_id` | 10 / hour | Stops one tester's crash-loop from drowning out signal from others |
| Summary endpoint per IP | 120 / hour | Generous; intended for the ops dashboard |

429 responses include `Retry-After`, `X-RateLimit-Limit`, `-Remaining`,
`-Reset` headers so the recorder can back off intelligently.

---

## 4. PII scrubbing — what gets redacted

The Python module `bin/error_report_service.py` and the TS mirror
`web-buyer/lib/error-report.ts` apply identical regexes:

| Pattern | Replacement | Example |
|---------|-------------|---------|
| `C:\Users\<X>\...` | `C:\Users\<USER>\...` | `C:\Users\Howard\AppData\Local\foo.exe` → `C:\Users\<USER>\AppData\Local\foo.exe` |
| `/Users/<x>/...`, `/home/<x>/...` | `/Users/<USER>/...` | `/Users/howard/Downloads` → `/Users/<USER>/Downloads` |
| Other absolute Windows paths | `<PATH>` | `D:\Games\Minecraft\saves` → `<PATH>` |
| `/tmp/...`, `/var/...`, `/opt/...` | `<PATH>` | `/tmp/recorder-abc123` → `<PATH>` |
| IPv4 addresses | `<IP>` | `192.168.1.42` → `<IP>` |
| Email addresses | `<EMAIL>` | `howard.li@berkeley.edu` → `<EMAIL>` |
| AppData casing | normalised | `\appdata\local\` → `\AppData\Local\` |

What is NOT redacted:
- UUIDs (used as `anon_id` — fresh per install, NOT identity-linked).
- Recorder version strings (we want these in the fingerprint).
- Stack frame line numbers / file basenames (no path context).
- Bare process names like `OysterRecorder.exe`.

If a new field is added that could re-identify (e.g. game session ids
tied to tester identity), update both the Python and TS scrubbers and
add a parity test in `tests/test_error_report.py::TestScrubParity`.

---

## 5. Fingerprint algorithm

```
sha256( OS_FAMILY  ||  RECORDER_MAJOR  ||  SCRUBBED_STACK )
        ^                ^
        |                |
  windows/macos/linux    v0.28.0-rc19  (patch suffix .0.0 / .0.1 collapses)
```

Truncated to 32 hex chars (more than enough — 128 bits of entropy).

Implications:
- Same crash on Windows-10 and Windows-11 collapses (good — frame
  numbers same).
- Same crash on Windows and macOS does NOT collapse (correct — likely
  different platform code paths).
- rc19.0.0 and rc19.0.1 collapse (almost always same lines).
- rc19 and rc20 do NOT collapse (intentional — new code, new
  fingerprint).

If a release introduces stack reshuffling (e.g. PyInstaller version
bump that changes frame numbers), expect a one-time wave of new
fingerprints; that's normal.

---

## 6. Daily ops loop

```bash
# Top crashes in the last 24h
curl -s "https://buyer.oysterlabs.ai/api/error-report/summary?since=24h&limit=20" \
    | jq '.rows[] | {count, recorder_version, os, preview: .stack_trace_preview}'

# Triage:
#   * count > 50 / 24h on one fingerprint → P1 incident
#   * fingerprint correlated with single recorder_version → likely a
#     regression in that release; consider hotfix + force-update
#   * fingerprint spread across versions → suspect environmental
#     (graphics driver, anti-virus, etc.)
```

To inspect a single fingerprint in detail:

```bash
psql $SUPABASE_DB_URL -c \
    "select * from public.error_reports where fingerprint = '<fp>'"
```

---

## 7. Diagnostics — when things break

### Symptom: dashboard shows 0 crashes for 24h

Either we shipped a perfect recorder (please log this for posterity)
or telemetry is broken. Likely causes:

1. **Service down**: `curl -X POST .../api/error-report` returns 5xx
   → check Vercel logs.
2. **Supabase not configured**: response is 503 with `envVars` listed
   → set `SUPABASE_SERVICE_ROLE_KEY` in Vercel.
3. **Recorder offline**: nothing is being sent. Check the recorder's
   own log (`%LOCALAPPDATA%\OysterRecorder\error_handler.log`).
4. **RLS misconfigured**: insert succeeds in dev but fails in
   production. Verify the policy in
   `web-buyer/supabase/migrations/20260513000000_g231_error_reports.sql`.

### Symptom: same crash creates many rows instead of incrementing count

The scrubber output has changed for the same input. Likely a regex
edit landed only on one side (Python or TS) without the parity test
catching it. Run:

```bash
pytest tests/test_error_report.py::TestScrubParity -v
```

If parity holds in Python, repro from TS side:

```bash
cd web-buyer
node -e "const {scrubPii,fingerprintStack} = require('./lib/error-report'); \
         const s = scrubPii('C:\\\\Users\\\\Howard\\\\foo.py'); \
         console.log({s, fp: fingerprintStack(s,'windows-11','v0.28.0-rc19.0.1')})"
```

The fingerprint must equal the one Python produces for the same
input.

### Symptom: PII slipping into stored rows

P0. Stop write traffic IMMEDIATELY:

1. Set `ERROR_REPORTS_FROZEN=1` env in Vercel and redeploy
   (you'll need to add a one-line guard at the top of
   `web-buyer/app/api/error-report/route.ts` if not already there;
   this is on the post-incident hardening list).
2. Quarantine the affected rows:
   ```sql
   ALTER TABLE public.error_reports DISABLE ROW LEVEL SECURITY;
   COPY (SELECT * FROM public.error_reports WHERE last_seen > NOW() - interval '24 hours')
       TO '/tmp/leaked.csv' WITH CSV HEADER;
   UPDATE public.error_reports SET stack_trace = '<QUARANTINED>'
       WHERE last_seen > NOW() - interval '24 hours';
   ALTER TABLE public.error_reports ENABLE ROW LEVEL SECURITY;
   ```
3. Add a regex unit test in `tests/test_error_report.py::TestScrubPii`
   that captures the leaked pattern, push the scrubber fix, redeploy,
   then resume traffic.

### Symptom: dedup not collapsing identical crashes from different testers

Most common cause: different PII variants surviving the scrub
(different file extensions, locale-localised Windows AppData strings,
etc.). Capture two raw stacks that should collapse, write the
expected scrubbed form in a new test case, then fix the regex. Both
in Python AND TS.

---

## 8. Backups & retention

- Supabase point-in-time recovery covers the last 7 days.
- We do NOT keep raw stacks > 90 days (auto-delete cron — see
  `bin/error_report_retention.py`, queued spec G242).
- The summary endpoint is intentionally bounded to 500 rows / call;
  longer-tail analysis goes through direct Postgres queries by ops.

---

## 9. Local development & testing

```bash
# Validate + record from stdin
echo '{"recorder_version":"v0.28.0-rc19.0.1","os":"windows-11", \
       "stack_trace":"Test crash"}' \
    | python -m bin.error_report_service record --db /tmp/errors.db

# Summary
python -m bin.error_report_service summary --db /tmp/errors.db --limit 10

# Run the full test suite
pytest tests/test_error_report.py -v
```

---

## 10. Security & threat model

- Anonymous POST endpoint — no auth. Threat: someone floods the table
  with bogus crashes. Mitigations:
    - per-IP and per-anon rate limits,
    - 16 KiB hard cap on `stack_trace` byte size,
    - 4 KiB cap on `context`,
    - Postgres unique-index on `fingerprint` prevents row explosion,
    - dashboard ranks by `count` so noise is naturally drowned out.
- Stored data: all PII-scrubbed per section 4 above. RLS policy
  prevents non-service-role reads.
- Summary endpoint: returns scrubbed data only, no auth required at
  v0.2.0. We may add session auth in v0.3.0 if the dashboard goes
  public.
- `anon_id`: opaque, recorder-generated, ephemeral. NOT tied to
  tester record.

---

## 11. Contact

- Slack: `#ops-cluster`
- On-call: see `cluster_oncall_2026.md`
- Privacy concerns: privacy@oysterlabs.ai (escalates to Howard +
  outside counsel).
