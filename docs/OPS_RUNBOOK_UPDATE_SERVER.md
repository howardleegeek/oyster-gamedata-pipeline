# Ops Runbook: Update Server (G250 / G251)

Owner: Cluster ops (rotating)
Severity scale: P0 (recorder fleet stuck on old version) → P3 (cosmetic)
Pages:
- `GET https://buyer.oysterlabs.ai/api/recorder-update?current=<v>` (G250)
- `POST https://tester.oysterlabs.ai/api/recorder-compat` (G251)
- CLI: `python -m bin.update_server_proxy`, `python -m bin.version_compat_checker`

---

## 1. What these services do

### G250 · /api/recorder-update

Recorder clients (PyInstaller `.exe`) call this on startup and once per
day in the background. The endpoint proxies the GitHub Releases API
for `howardleegeek/oyster-gamedata-pipeline` and returns:

```json
{
  "latest":         "v0.28.0-rc19.x",
  "installer_url":  "https://github.com/.../OysterRecorder-setup.exe",
  "release_notes":  "...",
  "force":          false,
  "current":        "v0.28.0-rc19.0.1",
  "update_available": true
}
```

- 5-minute in-process cache per Vercel instance.
- 60 req/hour/IP rate-limit (recorder default poll is 1/day so this is
  abuse-prevention, not a UX wall).
- `force: true` when a release note line starts with `[FORCE]` —
  recorder clients must apply the update before recording resumes.

### G251 · /api/recorder-compat + bin/compat_matrix.json

The buyer-pipeline owns a small matrix of supported recorder versions:

```json
{
  "entries": {
    "v0.28.0-rc19.0.1": {
      "min_pipeline":  "0.1.0-rc8",
      "lint_version":  38,
      "deprecated":    false
    }
  }
}
```

- POST `/api/recorder-compat` is the pre-flight before a tarball
  upload — returns 400 with `upgrade_url` if the recorder is out of
  matrix.
- The same logic also runs server-side inside `/api/upload-tarball`
  (so a malicious recorder cannot skip).
- CLI: `python -m bin.version_compat_checker --tarball clip.tar.gz`
  walks an uploaded tarball, reads `MANIFEST.json`, and verdicts in
  one shot.

---

## 2. Cutting a new recorder release (the happy path)

1. Tag and push the recorder repo:
   ```bash
   git tag v0.28.0-rc20 && git push origin v0.28.0-rc20
   ```
2. CI builds and attaches `OysterRecorder-setup.exe` to the GitHub
   release.
3. Add a new row to `bin/compat_matrix.json` AT THE TOP of the
   `entries` block:
   ```json
   "v0.28.0-rc20": {
       "min_pipeline":  "0.1.0-rc8",
       "lint_version":  39,
       "deprecated":    false
   }
   ```
   **Iron-law: never edit existing rows.** Append, then deprecate the
   old row by setting `deprecated: true` and a `support_window_end`
   date.
4. Re-deploy web-buyer + web-tester (Vercel auto-builds on merge to
   `main` — verify the deploy succeeded in the Vercel dashboard).
5. Verify:
   ```bash
   curl 'https://buyer.oysterlabs.ai/api/recorder-update?current=v0.28.0-rc19.0.1' | jq
   # latest should be v0.28.0-rc20
   curl -X POST https://tester.oysterlabs.ai/api/recorder-compat \
        -H 'content-type: application/json' \
        -d '{"recorder_version":"v0.28.0-rc20"}' | jq
   # accepted: true
   ```

---

## 3. Force-update procedure (security/break-the-glass)

If a recorder version has a data-corruption bug or a security
vulnerability, add `[FORCE]` as the first line of the release body
on GitHub. Recorders will refuse to record until they update.

```bash
gh release edit v0.28.0-rc20 --notes "$(cat <<'EOF'
[FORCE] Critical: fixes incorrect depth EXR alpha channel that
corrupted ~12% of clips between rc18 and rc19.0.1. All testers
must upgrade before continuing.

(remaining release notes follow)
EOF
)"
```

After 5 min the cache expires and clients see `force: true`.

To force the cache to flush immediately (e.g. during incident
response), redeploy web-buyer — every Vercel instance gets a fresh
cache.

---

## 4. Diagnostics — when things break

### Symptom: recorder shows "update check failed"

1. Check `X-Update-Cache: HIT/MISS` response header.
2. If `MISS` repeatedly → GitHub side. Check
   https://www.githubstatus.com.
3. If response is 502 with `Upstream unavailable`:
   - Verify token: `echo $GITHUB_TOKEN | head -c 8`
   - Anonymous rate-limit (60/h/IP) is most likely cause.
     Set `GITHUB_TOKEN` env var in Vercel.
4. If response is 429:
   - A buggy recorder client is hammering us. Check Vercel logs for
     IP. Per-IP soft-cap is 60/h so an honest recorder won't trip it.

### Symptom: every recorder gets `accepted: false` from compat check

1. SSH into a Vercel function instance (`vercel logs --follow`):
   - Look for `compat_matrix.json not found` — file wasn't bundled.
   - Fix: `COMPAT_MATRIX_PATH` env var or check
     `web-tester/next.config.mjs` `outputFileTracingIncludes`.
2. Run `python -m bin.version_compat_checker --version v0.28.0-rc19.0.1`
   locally — should print `ACCEPTED`. If not, the matrix file itself
   is broken (validate with `jq . bin/compat_matrix.json`).

### Symptom: GitHub rate-limit hit (60 req/h anonymous)

1. Generate a fine-grained personal access token (PAT) with
   `repo:public_repo` scope only.
2. Add it to Vercel as `GITHUB_TOKEN` (server-only env).
3. Re-deploy. Authenticated rate-limit is 5000/h.

---

## 5. Deprecation lifecycle

When a recorder version is end-of-life:

1. Edit the matrix entry IN PLACE only to add:
   ```json
   "deprecated": true,
   "deprecation_reason": "missing depth alpha channel; see rc20 hotfix",
   "support_window_end": "2026-06-30"
   ```
   Do NOT remove the row — testers running this version need to see
   the deprecation reason in the rejection message.
2. After `support_window_end`, the compat checker rejects with
   "support ended" in the reason.
3. After 90 days past `support_window_end`, the row may be removed
   in a separate cleanup PR (only after explicit cluster-ops approval
   in an issue).

---

## 6. Local development & testing

```bash
# Compatibility check (no network)
python -m bin.version_compat_checker --version v0.28.0-rc19.0.1
python -m bin.version_compat_checker --tarball samples/clip.tar.gz

# Update server proxy (hits real GitHub once per 5 min)
python -m bin.update_server_proxy --current v0.28.0-rc19.0.0

# Bypass cache during release drills
G250_DISABLE_CACHE=1 python -m bin.update_server_proxy \
    --current v0.28.0-rc19.0.0

# Full test suite
pytest tests/test_update_server.py tests/test_version_compat.py -v
```

---

## 7. Metrics to watch (post-deploy)

These live on the ops dashboard:
- `/api/recorder-update` p50 / p95 latency (target: < 50 ms p95
  with cache hit, < 800 ms p95 with cache miss)
- 502 rate (target: < 0.1%)
- 429 rate (informational — a sudden spike means a misbehaving
  recorder is in the wild)
- `X-Update-Cache: MISS` ratio (target: < 1% in steady state)
- Compat check rejection rate by recorder_version (a recorder version
  with > 5% rejection over 24h is a deprecation candidate)

---

## 8. Security notes (see also: PII Scrubbing section in the
## error-reporting runbook)

- `GITHUB_TOKEN` is server-only — never echoed in responses or logs.
- Output JSON is strictly whitelisted (`latest`, `installer_url`,
  `release_notes`, `force`, `current`, `update_available`). If you
  ever add a field, audit that it doesn't leak labels, branches, or
  private release notes that should be paid-only.
- Response body cap is 1 MiB to defend against a malicious or
  compromised GitHub response.
- The recorder is expected to verify the SHA-256 of the downloaded
  installer against the SHA published in the release manifest — see
  G243 / G241 (signing pipeline) for the signing side of this contract.

---

## 9. Contact

- Slack: `#ops-cluster`
- On-call: see `cluster_oncall_2026.md`
- Escalation: Howard if cluster ops can't unblock in < 1 hour
  (he is also the only one who can rotate `GITHUB_TOKEN`).
