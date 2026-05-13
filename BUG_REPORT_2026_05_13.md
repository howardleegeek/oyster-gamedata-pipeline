# Bug Hunt Report — 2026-05-13

**Worktree:** `/private/tmp/wt-qa-portals` (branch `qa/bug-hunt-portals`, off `main` @ `6f78baf`)
**Sister branch reviewed:** `cluster/gap6-upload-auth` @ `a8ca47d` (worktree `/private/tmp/wt-gap6`) — for the Gap #6 HMAC code that's not yet in `main`.
**Methodology:** Static read of the code, targeted Python/Node reproducers under `/tmp/qa-repros`, regression-style pytest at `tests/test_qa_bug_hunt_2026_05_13.py` (5 hard fails + 4 xfails confirm bugs; rest pass).
**Scope ground rules:** no fixes, no destructive ops, no live HTTP against deployed portals — static analysis + targeted unit-level repros only.

---

## Summary by severity

| Severity | Count |
|----------|-------|
| 🔴 Critical | 6 |
| 🟡 Medium | 13 |
| 🟢 Minor / cosmetic | 4 |
| **Total** | **23** |

### Top 5 most urgent

1. **🔴 BUG-09** — `web-tester/app/api/stripe/connect/return/route.ts:38,58,63-70` — Victim's `stripe_account_id` can be hijacked by a crafted `?account=acct_attacker` URL. Future payouts redirect to attacker.
2. **🔴 BUG-15** — `web-tester/app/api/upload-tarball/route.ts:138,141 + lib/upload-auth.ts:144-145` — Gap #6 HMAC gate is **opt-in by default**. Fresh deploys with `UPLOAD_REQUIRE_TOKEN` unset still accept anonymous tarball uploads. Gap #6's stated purpose is unfulfilled out of the box.
3. **🔴 BUG-10** — `web-buyer/app/api/checkout/route.ts:34,159` — Body schema allows 20 tarballs/checkout but joined UUIDs exceed Stripe's 500-char metadata limit at >13 items. Stripe API will 400 the create call; the buyer can't check out a large cart.
4. **🔴 BUG-19** — `web-tester/app/api/upload-tarball/route.ts:194-216` — Duplicate-by-sha256 path returns the first uploader's `tester_id` to the second uploader. Information disclosure of tester UUIDs, plus credit-theft: the second uploader gets no billing for an identical tarball.
5. **🔴 BUG-21** — `web-tester/app/api/download/[testerId]/route.ts:46-49` — External `RECORDER_EXE_URL` redirect does **not** rewrite `Content-Disposition`, so the downloaded `.exe` keeps its origin filename (typically `OysterRecorder.exe`) — Gap #6's filename-embedded token (16 hex prefix) never reaches the recorder. The HMAC flow breaks on the default deploy.

---

## 🔴 Critical bugs

### BUG-01 — Lint passes 22/24 on a zero-byte placeholder package

**File:** `bin/lint_v3_prd_grounded.py:67-75` (`_check_video_specs`), 80-89 (`_check_image_specs`), 91-99 (`_check_audio_specs`)
**Severity:** 🔴 critical (false-positive in the only thing standing between buyers and bad data)

**Reproducer:**

```bash
mkdir -p /tmp/repro/lint && cd /tmp/repro/lint
touch video.mp4 systeminfo.json action_camera.json gameinfo.xlsx
mkdir depth
python3 bin/lint_v3_prd_grounded.py .
```

**Output (current):**
```
Passed: 22/24, Failed: 2  (only depth-related fail)
```

A directory of zero-byte placeholders passes `Video Resolution` (cr-1) with message `"1920x1080 required"` and `passed=true`, plus `Video Duration` (cr-2) with `"5-6 min required"` and `passed=true`. The check is `bool(vids)` — i.e. "list is non-empty".

**Suggested fix sketch:** Open each `.mp4` with `cv2` or `ffprobe`, verify `width==1920, height==1080`, frame count covers 5–6 min × 60 fps. Fail if size < 1MB (a real 5-min 1080p H.264 is ~150 MB).

**Regression test:** `tests/test_qa_bug_hunt_2026_05_13.py::test_zero_byte_package_fails_lint`

---

### BUG-02 — `LintReport.to_dict()` crashes with `ZeroDivisionError` when total=0

**File:** `bin/lint_v3_prd_grounded.py:60-65`
**Severity:** 🔴 critical (DOS via empty report)

```python
"pass_rate": f"{100*self.passed_count/self.total_checks:.1f}%"
```

If a caller (or future regression) constructs a `LintReport` with `total_checks=0` (e.g. all checks short-circuit out), `to_dict()` raises `ZeroDivisionError: division by zero`. Confirmed in `/tmp/qa-repros/div_zero` repro.

**Suggested fix sketch:** Guard with `self.total_checks or 1` or return `'N/A'` when no checks ran.

**Regression test:** `test_lint_report_to_dict_zero_total_does_not_crash` (hard fail).

---

### BUG-09 — Stripe Connect return hijack via `?account=…` query string

**File:** `web-tester/app/api/stripe/connect/return/route.ts:38,58,63-70` (Gap-6 worktree)
**Severity:** 🔴 critical (financial fund redirection)

```ts
const accountIdFromQuery = req.nextUrl.searchParams.get('account');
// ...
const accountId = accountIdFromQuery ?? tester?.stripe_account_id ?? null;
// ...
const account = await stripe.retrieveAccount(accountId);
await service.from('testers').update({
  stripe_account_id: account.id,
  // ...
}).eq('id', user.id);
```

An attacker sends a victim the link `https://tester.example.com/api/stripe/connect/return?account=acct_attacker`. When clicked while signed-in, the handler:
1. Reads `accountIdFromQuery = acct_attacker`.
2. Calls `stripe.retrieveAccount("acct_attacker")` — succeeds because Stripe allows lookup of any account ID the calling key can see.
3. **Overwrites the victim's `stripe_account_id` with the attacker's id.**
4. Future payouts route to the attacker.

There is no verification that `accountIdFromQuery` matches the victim's existing account ID or that the victim ever onboarded that account.

**Suggested fix sketch:** Drop the query-param branch entirely. The user's account ID is already in `testers.stripe_account_id` for the signed-in `user.id`. Stripe's return flow doesn't include `account` in the URL by design (see comment in same file).

**Regression test:** `test_connect_return_does_not_trust_query_account_id` (hard fail; greps the route source for `accountIdFromQuery`).

---

### BUG-10 — Checkout metadata exceeds Stripe 500-char limit at 14+ tarballs

**File:** `web-buyer/app/api/checkout/route.ts:34,159`
**Severity:** 🔴 critical (production checkout failure at large carts)

```ts
const Body = z.object({
  tarball_ids: z.array(z.string().uuid()).min(1).max(20),  // <— up to 20
  // ...
});
// ...
metadata: {
  // ...
  tarball_ids: tarball_ids.join(','),  // 20 × 36 + 19 = 739 chars
},
```

Stripe limits `metadata` *values* to 500 characters. Each UUID is 36 chars + 1 comma = 37. The safe cap is `floor(500/37) = 13`. A buyer with 14+ items in their cart hits a 400 from Stripe at `checkout.sessions.create` time and the response message is forwarded as 502 to the buyer.

Worse: even if Stripe accepts (unlikely — they enforce), the webhook splits on `,` and inserts `purchases` rows from the metadata, so a silently truncated metadata would mean fewer purchases than the buyer paid for.

**Suggested fix sketch:** Either lower `Body.tarball_ids.max(20)` to 13, or stop putting the ID list in metadata — instead persist a `pending_checkouts` row keyed by Stripe session ID at create-time and read from there in the webhook.

**Regression test:** `test_checkout_tarball_ids_metadata_under_500_chars` (hard fail).

---

### BUG-15 — Gap #6 HMAC gate is opt-in (`UPLOAD_REQUIRE_TOKEN=false` by default)

**File:** `web-tester/lib/upload-auth.ts:54-57,144-145,159-166` + `app/api/upload-tarball/route.ts:140-166` (gap6 worktree)
**Severity:** 🔴 critical (Gap #6's stated mitigation is bypassed on every fresh deploy)

The Gap #6 PR adds an HMAC `X-Upload-Token` gate. But it's only enforced when **both** `UPLOAD_HMAC_SECRET` (≥1 byte) **and** `UPLOAD_REQUIRE_TOKEN=true` are set. The default in `getUploadAuthConfig()` is `requireToken = false`. The unconfigured/`unauthorized` outcomes only `log.warn` then proceed. So:

* `UPLOAD_HMAC_SECRET` unset → `auth.kind === 'unconfigured'` → upload proceeds without any check.
* `UPLOAD_HMAC_SECRET` set, `UPLOAD_REQUIRE_TOKEN=false` (default) → `auth.kind === 'unauthorized'` → upload still proceeds.

Anyone who learns or guesses a `tester_id` UUID can still post arbitrary tarballs charged to that tester — the exact attack the gap was meant to close. The fix is one env-var flip away, but the **iron-law default-deny** principle the rest of the codebase follows (`isSupabaseConfigured()` returns 503 when unset) is inverted here.

**Suggested fix sketch:** Default `requireToken` to `true`. If `UPLOAD_HMAC_SECRET` is empty, return 503 from `/api/upload-tarball` with `envVars: ['UPLOAD_HMAC_SECRET']` — matching the iron-law pattern. Allow operators to opt-OUT during migration via an explicit `UPLOAD_REQUIRE_TOKEN=false`.

**Regression test:** Add `tests/test_upload_auth_default_deny.py` that simulates `process.env` without either var and asserts `authenticateUpload` returns `{kind: 'unauthorized'}` rather than `'unconfigured'`.

---

### BUG-19 — Duplicate-sha256 path leaks first uploader's tester_id and steals credit

**File:** `web-tester/app/api/upload-tarball/route.ts:194-209` (gap6 worktree)
**Severity:** 🔴 critical (privacy leak + credit theft)

```ts
if (insertErr.code === '23505') {
  const { data: existing } = await supabase
    .from('tarballs')
    .select('id, tester_id, uploaded_at, sha256, size_bytes, d5_verdict')
    .eq('sha256', sha)
    .single();
  // ...
  return NextResponse.json({ ...(existing ?? {}), duplicate: true });
}
```

When two testers happen to upload bit-identical tarballs (e.g. they ran the same deterministic benchmark), the second uploader receives the **first uploader's `tester_id`** in the response. That UUID was supposed to be private. Also: only the first uploader's row exists, so the second uploader's billable hours are never credited.

**Suggested fix sketch:** When returning the duplicate row, strip the `tester_id` field (it's not the caller's data). Optionally, also insert a second row with the *same* `sha256` but unique `tester_id` so both testers are paid — requires schema change (drop the unique constraint on `sha256` and replace with `(tester_id, sha256)` composite key, or split storage and accounting into separate tables).

**Regression test:** Add an integration test that calls the route twice with different `tester_id`s but identical body. Assert the second response's `tester_id` field equals the second tester, not the first.

---

### BUG-21 — External RECORDER_EXE_URL redirect drops Content-Disposition, breaking Gap #6 token embed

**File:** `web-tester/app/api/download/[testerId]/route.ts:46-49`
**Severity:** 🔴 critical (Gap #6 token-from-filename path never works on the default deploy)

```ts
if (/^https?:\/\//i.test(env.recorderExeUrl)) {
  const redirectUrl = new URL(env.recorderExeUrl);
  redirectUrl.searchParams.set('tester_id', testerId);
  return NextResponse.redirect(redirectUrl.toString(), { status: 302 });
}
```

The default `RECORDER_EXE_URL` is a GitHub Release asset URL. When the browser follows the 302, it pulls `OysterRecorder.exe` (whatever the upstream Content-Disposition says) — **not** `OysterRecorder-<short>-<UUID>-<token16>.exe`. The `tester_id` query param is ignored by GitHub Releases. So:

* Gap #6's `parse_token_from_exe_name()` returns `None` for the downloaded file.
* `bin/upload_to_web_tester.py` falls back to env var `OYSTER_UPLOAD_TOKEN`, which a non-technical tester doesn't set.
* The recorder uploads without `X-Upload-Token` and either gets 401 (if `UPLOAD_REQUIRE_TOKEN=true`) or proceeds unauthenticated (see BUG-15).

The local-fallback path (lines 53-64) does set the right `Content-Disposition` — but Vercel deploys always use the external GitHub URL.

**Suggested fix sketch:** Drop the external-URL branch entirely and proxy the .exe via the Next route, streaming the bytes through with a correct `Content-Disposition`. Or have a small `/api/recorder-config?tester_id=...` endpoint that returns `{exe_url, token}` and have the desktop launcher fetch from there before downloading the .exe.

**Regression test:** Browser-level e2e (Playwright) that asserts the downloaded filename embeds the tester UUID. Static check today: assert no external redirect path exists in route source.

---

## 🟡 Medium-severity bugs

### BUG-03 — 7 of 24 lint criteria are hardcoded `True` (no-op checks)

**File:** `bin/lint_v3_prd_grounded.py:91-99, 101-112, 243-247`
**Severity:** 🟡 medium (false sense of completeness)

Criteria 7, 9, 10, 11, 19, 20, 21 all call `rpt.add(LintResult(N, "...", True, "... check passed"))` unconditionally. That's 29% of advertised checks doing nothing.

**Reproducer:** `tests/test_qa_bug_hunt_2026_05_13.py::test_lint_audio_quality_inspects_audio` (xfail).

**Suggested fix sketch:** Either implement the checks (audio loudness via `librosa`, route-distribution coverage check, screen-recognition for UI overlay via OCR) or remove them from the criterion list with a `# unimplemented` skip.

---

### BUG-04 — Lint `keyCode` check only inspects top-level dicts

**File:** `bin/lint_v3_prd_grounded.py:221-241`
**Severity:** 🟡 medium (false negative for the actual data format)

The check loops over `**/*.json` and asks `if isinstance(data, dict) and "keyCode" in data`. But the actual data format is a list of records: `{"events": [{"keyCode": ...}, ...]}` — these are never inspected. A package with 100 records all using string `keyCode` passes the lint.

**Reproducer:** `test_lint_keycode_catches_nested_strings` (xfail).

**Suggested fix sketch:** Recursively walk JSON until a `keyCode` key is found at any depth; verify type at every occurrence.

---

### BUG-05 — Lint sampling caps (15–30 files) trivially gamed

**File:** `bin/lint_v3_prd_grounded.py:82, 134, 142, 164, 199, 232, 253`
**Severity:** 🟡 medium (large datasets can be 99% junk and still pass)

Loops like `for df in depth_files[:15]:` — PRD calls for 1800 .exr files but linter checks the first 15. Worse: `list(d.glob(...))` doesn't sort, so iteration order depends on filesystem. On Linux ext4 (hash order) vs macOS HFS+ (alphabetical) the linter inspects different subsets, leading to non-reproducible results across CI hosts.

**Reproducer:** `test_lint_depth_samples_more_than_15_files` (hard fail; greps source).

**Suggested fix sketch:** Replace `[:15]` with reservoir sampling (`random.sample(depth_files, min(k, len(depth_files)))` with seed=hash(data_dir)) so reproducibility is preserved. Better: scan all files, sample only for resolution checks where reading 1800 EXRs is expensive.

---

### BUG-06 — Lint result message strings inconsistent with `passed` boolean

**File:** `bin/lint_v3_prd_grounded.py:152, 219, 241`
**Severity:** 🟡 medium (operator confusion / wrong-message-in-CI-summary)

When a criterion fails (e.g. cr-13 quaternion length wrong), the **paired** criterion (cr-14 "Quaternion Normalization") gets `passed=False` but `message="Quaternion normalization check passed"`. Same for depth quality (cr-16) and keyCode validation (cr-18).

```python
rpt.add(LintResult(14, "Quaternion Normalization", not issues, "Quaternion normalization check passed"))
```

The boolean updates per `not issues`, but the message string is a static "passed" — so failed criteria emit a message that contradicts the boolean. CI dashboards that filter by message text (not boolean) miss real failures.

**Reproducer:** `test_lint_message_consistent_with_boolean` (xfail).

**Suggested fix sketch:** Use the same pattern as criteria 1/2: `msg_pass if not issues else f"{len(issues)} issues"`.

---

### BUG-07 — `upload_to_web_tester.resolve_token` filename precedence locks stale tokens

**File:** `bin/upload_to_web_tester.py:99-130` (gap6 worktree)
**Severity:** 🟡 medium (operator-friction; not a security bug)

Resolution order is `explicit > env OYSTER_UPLOAD_TOKEN > .exe filename prefix > local UPLOAD_HMAC_SECRET compute`. After a `UPLOAD_HMAC_SECRET` rotation, a tester still using their old `.exe` reads the stale token from the filename, sees the env-secret path is skipped, and uploads a stale token. The server rejects with 401 and the tester has no clue why.

Worse: when devs `export UPLOAD_HMAC_SECRET=...` and run the script against a stale `.exe`, they get a 401 even though the env var was set correctly.

**Reproducer:** `/tmp/precedence_bug.py` confirms `resolve_token` returns the stale prefix even when `UPLOAD_HMAC_SECRET` is set to a fresh value.

**Suggested fix sketch:** Invert the order: explicit > env > **HMAC-secret compute** > filename. The local compute is "the source of truth when you have the secret" and the filename is the fallback. Also: surface a warning when filename-token != computed-token (indicates stale .exe).

**Regression test:** `tests/test_qa_bug_hunt_2026_05_13.py::test_resolve_token_filename_wins_over_hmac_secret` (passes today, will need to flip when fixed).

---

### BUG-08 — `mineflayer/bot.js parseArgs` accepts invalid ports

**File:** `mineflayer/bot.js:43-71`
**Severity:** 🟡 medium (delays the failure to deep inside mineflayer's connect, with cryptic message)

`parseArgs` only validates `parseInt(next, 10) !== NaN`. So `--port 0`, `--port -1`, `--port 65536`, `--port 99999` all pass parse, then crash inside the Node net stack with `RangeError`. A misconfigured Minecraft server (e.g. operator typo) wastes 30s of `_wait_for_hello_ack` before timeout instead of failing fast.

**Reproducer:**
```bash
node -e "const {parseArgs}=require('mineflayer/bot.js'); console.log(parseArgs(['n','b','--port','0']))"
# Prints: { host: 'localhost', port: 0, username: 'oyster_bot', version: false }
```

**Regression test:** `test_botjs_parseargs_rejects_invalid_ports` (hard fail).

**Suggested fix sketch:** After `parseInt`, also reject `port < 1 || port > 65535`.

---

### BUG-11 — Webhook line-item attribution mixes positional + metadata mapping

**File:** `web-buyer/app/api/checkout/webhook/route.ts:99-122`
**Severity:** 🟡 medium (per-tarball revenue/tax reports mis-attribute amount_cents)

```ts
const list = await stripe.checkout.sessions.listLineItems(session.id, {...});
lineItems = list.data;
// ...
purchasesToInsert = tarballIds.map((tid, i) => {
  const li = lineItems[i];                              // POSITIONAL
  const tidFromMeta = li?.price?.product?.metadata?.tarball_id;  // METADATA
  return {
    tarball_id: tidFromMeta ?? tid,                     // tidFromMeta of lineItems[i]
    amount_cents: li?.amount_total ?? evenSplit,        // amount of lineItems[i]
  };
});
```

If Stripe returns line items in a different order than `tarball_ids[]` (Stripe doesn't guarantee creation-order across line items in larger sessions), the `amount_cents` from `lineItems[i]` gets paired with the `tidFromMeta` of `lineItems[i]` — which is correct **for the same i** but the `tid` argument to `.map(...)` is the wrong index. Net effect: `amount_cents` follows `lineItems[i].amount_total`, `tarball_id` follows `lineItems[i].metadata.tarball_id` — both indexed by `i`. So the row's tarball_id and amount agree.

BUT: when `tidFromMeta` is **null** (e.g. older line item without expanded product metadata), we fall back to `tid` — which is `tarball_ids[i]` — and that's a **different** tarball than `lineItems[i]`. So the partial-metadata case mismatches.

**Suggested fix sketch:** Build a `Map<tarball_id, line_item>` from the line items keyed by `lineItem.price.product.metadata.tarball_id`, then iterate `tarballIds` against the map. Fail loudly if any `tarball_id` is missing rather than positional fallback.

---

### BUG-12 — Upload route loads full 1 GiB into memory; serverless OOM at ~250 MiB

**File:** `web-tester/app/api/upload-tarball/route.ts:147,168`
**Severity:** 🟡 medium (degraded UX, intermittent 502s on large tarballs)

```ts
const buf = Buffer.from(await file.arrayBuffer());
// ...
.upload(storagePath, buf, { ... });
```

`form.formData()` first buffers the whole multipart body, then `file.arrayBuffer()` produces another copy, then `Buffer.from()` copies again. Peak memory ≈ 3× file.size + overhead. On Vercel's default 1024 MB function memory, anything over ~250 MiB OOMs. The `MAX_BYTES = 1 GiB` ceiling is cosmetic.

**Suggested fix sketch:** Pipe the multipart stream straight to a Supabase Storage `resumable` upload (TUS). The Supabase JS SDK supports streaming uploads; we just need to feed it the raw `ReadableStream`.

---

### BUG-13 — Upload route has no tar.gz magic-byte check

**File:** `web-tester/app/api/upload-tarball/route.ts:132-144`
**Severity:** 🟡 medium (arbitrary bytes served back as "tarball")

The route checks `file instanceof Blob`, `file.size > 0`, computes sha256, but never validates the file is actually a gzip stream (magic bytes `1f 8b`). A tester (or attacker with valid tester_id) can upload random bytes, an `.exe`, or a fake-tarball payload. Supabase Storage records `contentType: 'application/gzip'` but the bytes don't match. When a buyer later downloads the signed URL, the browser receives garbage labelled as gzip — at best a confused error, at worst a security surface (browser auto-execution of detected file types).

**Suggested fix sketch:** Read the first 2 bytes from `arrayBuffer().slice(0,2)` and reject if not `0x1f 0x8b`. Optionally also validate the inner tar layout by peeking at the gunzipped first 512 bytes for the tar checksum field.

---

### BUG-14 — Upload duration is capped at 12h, allowing $51k/day fraud per compromised tester_id

**File:** `web-tester/app/api/upload-tarball/route.ts:37,49-50`
**Severity:** 🟡 medium (financial exposure if a tester_id leaks)

`duration_seconds` upper bound `60*60*12 = 12 h`. Per-tester rate limit is 30 uploads/hour. At `GAMEDATA_RATE_PER_HOUR_CENTS=600` (default $6/h), a single compromised tester_id can mint 30 × 12 × $6 = $2160/h of billable hours, up to **$51,840/day** before any out-of-band fraud detection triggers.

D5 verdict runs async after upload, so the `tarballs.paid` column may be set before D5 rejects the row. Combined with BUG-15 (no HMAC enforced by default), a leaked tester_id is unbounded financial exposure.

**Suggested fix sketch:** Bound `duration_seconds` × `paid_uploads_per_24h` against a per-tester daily payout cap (e.g. ≤ 8 h/day → ≤ $48/day per tester). Enforce in the payouts cron, not the upload route.

---

### BUG-16 — Buyer cart cookie is unsigned; cross-site form POST can silently add items

**File:** `web-buyer/lib/cart-cookie.ts:28-42` + `app/api/cart/add/route.ts:21-29,33-79`
**Severity:** 🟡 medium (CSRF on anonymous cart; quality-of-life nuisance, not financial)

The cart cookie is `httpOnly + sameSite=Lax` but **not signed**. `/api/cart/add` accepts JSON or `application/x-www-form-urlencoded` body with no CSRF token. SameSite=Lax permits top-level form POSTs, so a malicious page on `attacker.com` can host:

```html
<form method="POST" action="https://buyer.example.com/api/cart/add"
      enctype="application/x-www-form-urlencoded">
  <input name="tarball_id" value="…">
</form>
<script>document.forms[0].submit()</script>
```

When a victim visits attacker.com (signed-in or anonymous), the form auto-submits and the cart gains an item. Not financial (the victim still has to click "Pay"), but annoying and a useful predicate for phishing flows.

**Suggested fix sketch:** Add a CSRF token cookie (double-submit cookie pattern) and require it in the body. Or: gate `cart/add` behind a `same-origin` Sec-Fetch-Site check (`headers.get('sec-fetch-site') === 'same-origin'`).

---

### BUG-17 — Rate-limit Map per-instance; horizontal scale defeats limits

**File:** `web-tester/lib/rate-limit.ts:30-31` + same for buyer (if present)
**Severity:** 🟡 medium (rate limit isn't a real cap under load)

Comment acknowledges this: "each Vercel serverless instance has its own bucket … worst case is N×instance-count effective requests/min". Reality: Vercel scales to hundreds of instances during a spike, so `12/min/IP` becomes `12,000/min/IP`. The cap is theatre. Mitigation noted in the comment ("upgrade to Upstash / Redis later").

**Reproducer:** Static — read `buckets = new Map()` at module scope. No persistence across instances.

**Suggested fix sketch:** Adopt `@upstash/ratelimit` with a Redis-backed sliding window. Alternatively, gate the per-tester limit at the **DB level** via a fast `INSERT INTO uploads (...) WHERE counts_in_window < limit` SQL pattern.

---

### BUG-18 — `clientIpFromHeaders` returns `'unknown'` when XFF missing, collapsing all anon traffic into one bucket

**File:** `web-tester/lib/rate-limit.ts:76-82`
**Severity:** 🟡 medium (lockout on non-Vercel deployments)

When neither `x-forwarded-for` nor `x-real-ip` is present, returns the literal string `'unknown'`. Used as a rate-limit key, that means **all traffic without IP** shares one bucket. On Vercel this is fine (Vercel always sets XFF). On any other deploy without a proxy that sets the header, the 12/min cap applies to the **sum** of all anonymous users.

**Suggested fix sketch:** Refuse to apply the IP bucket when IP is unknown; either fall back to per-Connection / per-session token, or 500 the request as misconfigured (with operator-facing message about required reverse-proxy config).

---

### BUG-20 — Admin-header check uses non-constant-time `===` string compare

**File:** `web-tester/app/api/stats/[testerId]/route.ts:56`, `web-tester/app/api/tester/auth/route.ts:57`, `web-buyer/app/api/downloads/[purchaseId]/route.ts:102`
**Severity:** 🟡 medium (theoretical timing oracle on the service-role key)

```ts
const isAdmin = adminHeader && adminHeader === process.env.SUPABASE_SERVICE_ROLE_KEY;
```

`===` is variable-time on strings. An attacker who can submit arbitrary `x-supabase-service-role` values and measure response timing can extract the key byte-by-byte. Practically very hard against a serverless function (cold-start jitter dominates), but the fix is trivial.

**Suggested fix sketch:** Use `crypto.timingSafeEqual(Buffer.from(adminHeader), Buffer.from(env.supabaseServiceRoleKey))` after a length check.

---

### BUG-22 — `parseInt(env, 10)` for pricing has no NaN or bounds check

**File:** `web-buyer/lib/env.ts:28-32`, `web-tester/lib/env.ts:32-33`
**Severity:** 🟡 medium (operator typo → free downloads or NaN propagation to Stripe)

```ts
pricePerGbCents: parseInt(process.env.GAMEDATA_PRICE_PER_GB_CENTS ?? '2500', 10),
researchDiscountPct: parseInt(process.env.GAMEDATA_RESEARCH_DISCOUNT_PCT ?? '40', 10),
downloadLinkTtlSeconds: parseInt(process.env.DOWNLOAD_LINK_TTL_SECONDS ?? '86400', 10),
```

* `GAMEDATA_RESEARCH_DISCOUNT_PCT=200` → discounted price clamps to 0 (Math.max). Researchers get everything for free.
* `GAMEDATA_PRICE_PER_GB_CENTS="abc"` → `parseInt` returns `NaN`; downstream `Math.floor((base * NaN) / 100)` returns `NaN`; Stripe's `unit_amount: NaN` errors.
* `DOWNLOAD_LINK_TTL_SECONDS="forever"` → `NaN`; Supabase `createSignedUrl(path, NaN)` likely yields a TTL of 0 → instant-expired URL.

**Suggested fix sketch:** Centralise into a Zod-validated env-schema with `.refine(v => !Number.isNaN(v))` and explicit `.min(0).max(100)` for percentages.

---

## 🟢 Minor / cosmetic

### BUG-23 — Lint silently swallows JSON / YAML parse errors with `except Exception: pass`

**File:** `bin/lint_v3_prd_grounded.py:111, 126, 141, 148, 231, 238, 259`
**Severity:** 🟢 minor (false negatives masked as silent passes)

Corrupt YAML or malformed JSON gets eaten by the broad `except Exception: pass`. The criterion then evaluates against `issues == []` because the bad file never produced an issue entry. False pass.

**Suggested fix sketch:** Log corrupt files into `r.details["parse_errors"]` and mark the criterion `passed=False` when any file fails to parse — corrupt files in a deliverable tarball are themselves an acceptance failure.

---

### BUG-24 — `safe-redirect.sanitizeNextPath` allows `/%2fevil.com` through (browser-side decoded)

**File:** `web-buyer/lib/safe-redirect.ts:25-29`, `web-tester/lib/safe-redirect.ts:32-42`
**Severity:** 🟢 minor (Next.js downstream normalises, but defense-in-depth lapse)

Tested in `/tmp/safe_redirect_unicode_bug.py`:
* `/\evil.com` → rejected ✅
* `//evil.com` → rejected ✅
* `/%2fevil.com` → **accepted** (browsers don't decode %2f in path).
* `/javascript:alert(1)` → rejected ✅ (good)

The percent-encoded slash *can* be exploited only if downstream URL handling decodes it (Next.js does not by default). Still: add a `decodeURIComponent` check before returning.

---

### BUG-25 — `mineflayer/bot.js` chat action does not strip `/commands`

**File:** `mineflayer/bot.js:341-352`
**Severity:** 🟢 minor (depends on bot's server permissions; risk only if bot has op)

`bot.chat(message)` sends the message verbatim. If the LLM produces `"/op @s"` or `"/kick someone"` in a `chat` action, the bot issues that as a server command. On a Paper/Spigot server without LuckPerms restrictions, an LLM trained on adversarial content could trivially issue server-admin commands.

**Suggested fix sketch:** Strip leading `/` from chat messages in `bot.js` before calling `bot.chat()`. Or use the Mineflayer Chat API's `chatPacket` with an explicit "message" type instead of "command".

---

### BUG-26 — `ClaudeThinkingProvider` retries ALL APIError types, not just 5xx/429

**File:** `src/oyster_agent_runner/providers/claude_thinking.py:171-173`
**Severity:** 🟢 minor (wasted retry budget on fatal errors)

```python
except self._APIError as exc:
    last_exc = exc
    time.sleep(BASE_BACKOFF_SEC * (2**attempt))
```

A 400 "invalid model" or 401 "auth failed" triggers 5 retries with exponential backoff totaling ~31s of dead time before raising. Worse, the wall budget of one `chat()` call can exceed `DEFAULT_ACTION_TIMEOUT_SEC=30.0` of the bot subprocess, so a stale provider response can land after the bot already gave up — possibly producing a corrupted trajectory.

**Suggested fix sketch:** Only retry on `RateLimitError` and `APIStatusError` with status in `[408, 429, 500..504]`. Re-raise all other `APIError` immediately. Add a per-call wall budget.

---

## Bugs observed but **not** confirmed via repro (filed for engineer review)

* **Preview endpoint DoS surface** — `web-buyer/app/api/tarball/[id]/preview/route.ts:103-108`. JSON.parse on a JSONL line without try/catch raises 500 on any malformed preview blob. Endpoint is unauthenticated and has no rate-limit; corrupt preview causes burst of 500s. Suggest: wrap in try, filter unparseable lines, cap response at 100 records.
* **Webhook clears entire cart even on partial purchase** — `web-buyer/app/api/checkout/webhook/route.ts:155`. The webhook calls `delete cart_items where buyer_id` *after* inserting purchases, regardless of how many purchases succeeded. If the catalog had moved on (some items unavailable at webhook time), the user's *not-purchased* items also vanish.
* **Auth-callback redirects on Supabase-unconfigured to user-supplied path without rate-limit** — `web-buyer/app/auth/callback/route.ts:20-22`, `web-tester/app/auth/callback/route.ts` (same). Even when sanitised, a tight loop of `/auth/callback?next=/some/path` requests is cheap; consider rate-limiting the callback per-IP.
* **`fetchCatalogById` fetches 200 rows + curation join to return 1** — `web-buyer/lib/catalog.ts:225-230`. Will silently miss rows beyond the 200 limit. Also a perf hit. Replace with `service.from('tarballs').select().eq('id', id).single()`.
* **Webhook deduces purchases ignoring potential price mismatch** — if `env.pricePerGbCents` changes between catalog-rendering and webhook delivery, the `purchases.amount_cents` may not equal what the buyer saw at cart time. Lock pricing into `cart_items` at add-time.

---

## Reproducer scripts

All scripts live under `/tmp/qa-repros/` and `/tmp/<topic>_repro.py` (intentionally tmp — not committed). Pytest regression tests committed in `tests/test_qa_bug_hunt_2026_05_13.py`.

```
$ pytest tests/test_qa_bug_hunt_2026_05_13.py -v
============================== test session starts ==============================
SKIPPED [1] (gap6-only path)
XFAIL[4]  test_zero_byte_package_fails_lint           (BUG-01)
          test_lint_audio_quality_inspects_audio      (BUG-03)
          test_lint_keycode_catches_nested_strings    (BUG-04)
          test_lint_message_consistent_with_boolean   (BUG-06)
FAILED[5] test_lint_report_to_dict_zero_total_does_not_crash (BUG-02)
          test_lint_depth_samples_more_than_15_files            (BUG-05)
          test_botjs_parseargs_rejects_invalid_ports            (BUG-08)
          test_connect_return_does_not_trust_query_account_id   (BUG-09)
          test_checkout_tarball_ids_metadata_under_500_chars    (BUG-10)
PASSED[1] test_resolve_token_filename_wins_over_hmac_secret     (BUG-07 lock-in)
```

The 4 xfails confirm the bugs are present today (would flip to xpass when fixed → caught by `strict=True`). The 5 hard fails are direct assertions that the bug is present in current code. The 1 pass is a lock-in test for BUG-07's current behaviour so a silent flip on fix is caught.

---

## Suggested triage order

1. **Now** — BUG-09, BUG-15, BUG-21, BUG-19 (auth / financial)
2. **This week** — BUG-10, BUG-12, BUG-14, BUG-20 (correctness + ops)
3. **Next week** — BUG-01 / -03 / -04 / -05 / -06 (lint quality bundle)
4. **Backlog** — minors + observed-but-not-repro'd above

---

Branch SHA: `6f78baf` (qa/bug-hunt-portals)
Sister branch SHA: `a8ca47d` (cluster/gap6-upload-auth, where the upload-auth code lives until merged)
