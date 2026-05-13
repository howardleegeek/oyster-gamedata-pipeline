# Security Audit — Oyster GameData Pipeline (2026-05-13)

**Auditor**: QA bug-hunt (worktree `/tmp/wt-qa-security`, branch `qa/bug-hunt-security`).
**Scope**: pre-launch security review of the most sensitive surfaces — HMAC upload auth (Gap #6 / PR #4), `/api/checkout` + Stripe webhook, `/api/upload-tarball`, `/api/download/[testerId]`, `/api/downloads/[purchaseId]`, Supabase RLS policies, and `bin/audit_artifact_honesty.py`.
**Methodology**: targeted code review + 26 Python reproducer tests in `tests/security/`. No exploits run against production Supabase / Vercel / Stripe.

---

## Executive summary

I found **10 distinct vulnerabilities** spanning critical funds-theft, high-severity service-role compromise, and several medium-severity issues in upload, cart, and rate-limit handling. The Gap #6 HMAC implementation correctly uses `crypto.timingSafeEqual` and `hmac.compare_digest` on the upload-token path, so the announced timing-attack surface is closed — but a sibling endpoint (`/api/stats/[testerId]`, `/api/downloads/[purchaseId]`) compares the `x-supabase-service-role` header with plain `===`, opening a NEW timing-oracle on the Supabase service-role key, which is strictly worse than the upload-token leak. The most urgent fix is the Stripe Connect `?account=` query-param-controlled account-id hijack — one phished GET against a signed-in tester redirects every future payout to the attacker.

---

## Findings table (sorted by CVSS, descending)

| # | Severity | CVSS | Title | File:line |
|---|----------|------|-------|-----------|
| 01 | **CRITICAL** | 9.6 | Stripe Connect account hijack via `?account=` query param | `web-tester/app/api/stripe/connect/return/route.ts:38,58,67` |
| 02 | **HIGH** | 7.5 | Service-role header compared with non-constant-time `===` | `web-tester/app/api/stats/[testerId]/route.ts:56`, `web-buyer/app/api/downloads/[purchaseId]/route.ts:102` |
| 03 | **HIGH** | 7.5 | Rate-limit keyed on attacker-controlled `X-Forwarded-For` leftmost element | `web-tester/lib/rate-limit.ts:76-82` |
| 04 | **MEDIUM** | 6.5 | Upload HMAC tokens are static (no nonce, no expiry, not payload-bound) | `web-tester/lib/upload-auth.ts:66-81`, `bin/upload_auth.py:73-90` |
| 05 | **MEDIUM** | 6.5 | No per-tester storage quota → 720 GiB/day/tester disk-fill DoS | `web-tester/app/api/upload-tarball/route.ts:45-55,200-215`, `web-tester/lib/rate-limit.ts:30-31` |
| 06 | **MEDIUM** | 5.4 | 24-hour signed download URLs survive license revocation, no audit on use | `web-buyer/lib/env.ts:32`, `web-buyer/app/api/downloads/[purchaseId]/route.ts:178-183` |
| 07 | **MEDIUM** | 5.3 | Cross-tester SHA-256 collision leaks foreign tester_id in duplicate response | `web-tester/app/api/upload-tarball/route.ts:232-245`, `web-tester/supabase/migrations/20260507000000_init.sql:41` |
| 08 | **MEDIUM** | 5.0 | `/api/cart/add` accepts any UUID, no catalog membership / cart cap → row-DoS | `web-buyer/app/api/cart/add/route.ts:53-68` |
| 09 | **LOW** | 3.7 | Storage RLS policy is dead code (service-role bypasses it); no buyer-side policy exists | `web-tester/supabase/migrations/20260507000000_init.sql:152-159` |
| 10 | **LOW** | 3.1 | `bin/audit_artifact_honesty.py` scope misses TS/financial fake-data regressions | `bin/audit_artifact_honesty.py:24-29` |

All 10 findings have a passing pytest reproducer in `tests/security/test_finding_NN_*.py`. Run with `python3 -m pytest tests/security/ -v` (26 tests, ~0.6 s).

---

## Per-finding detail

### Finding #01 — Stripe Connect account hijack via `?account=` query param &nbsp;|&nbsp; **CRITICAL** (CVSS 9.6)

**File**: `web-tester/app/api/stripe/connect/return/route.ts`
**Lines**: 38, 58, 63–71

**Description**: The Stripe Connect "return" callback accepts an optional `?account=acct_xxx` query parameter (`req.nextUrl.searchParams.get('account')`, line 38). It then prefers that value over the DB-stored `tester.stripe_account_id` (line 58: `accountIdFromQuery ?? tester?.stripe_account_id ?? null`), retrieves the account from Stripe, and **persists the returned `account.id` back to the victim's `testers.stripe_account_id`** (lines 63–71).

The Stripe Platform key the server uses can `retrieveAccount(...)` for ANY connected account the platform is associated with — including accounts the attacker has independently onboarded. The retrieve call therefore succeeds, the update succeeds, and the victim's payout destination is silently replaced.

The exploit is a one-click GET from a signed-in tester's browser:

```
https://tester.oyster.example/api/stripe/connect/return?account=acct_attacker_steal
```

Once persisted, `payout_cron.py` (line 462: `destination_account_id=bal.stripe_account_id`) wires that day's earnings to the attacker.

**Repro**: `tests/security/test_finding_01_stripe_account_hijack.py` — both the bug-demonstration test and the post-fix regression test pass against the recommended remediation.

**Remediation**: Drop `accountIdFromQuery` entirely. The endpoint MUST source the account ID ONLY from `testers.stripe_account_id` (the value the onboarding step wrote). Stripe Connect does not require the account ID in the return URL. Replace lines 38, 58 with `const accountId = tester?.stripe_account_id ?? null;`.

**Regression test idea**: integration test that sets `tester.stripe_account_id = 'acct_legit'`, GETs `/api/stripe/connect/return?account=acct_attacker`, then re-reads the row and asserts it is still `acct_legit`.

---

### Finding #02 — Service-role header compared with plain `===` &nbsp;|&nbsp; **HIGH** (CVSS 7.5)

**Files**:
- `web-tester/app/api/stats/[testerId]/route.ts:56`: `const isAdmin = adminHeader && adminHeader === process.env.SUPABASE_SERVICE_ROLE_KEY;`
- `web-buyer/app/api/downloads/[purchaseId]/route.ts:102`: `const isAdmin = !!adminHeader && adminHeader === env.supabaseServiceRoleKey;`

**Description**: JavaScript `===` for strings short-circuits at the first byte mismatch. With a stable RTT and repeated probes, an attacker can recover the service-role key one byte at a time (Crosby & Wallach 2009; modern Cloudflare-edge timing research shows ~50 ns is recoverable). The service-role key bypasses RLS entirely — leaking it allows reading every tester's PII / Stripe account ID, minting arbitrary payouts via direct DB writes, and full marketplace compromise.

The team got the timing-safe compare RIGHT in `lib/upload-auth.ts` (line 123 — `crypto.timingSafeEqual`). The bug is that two other handlers grew their own admin-header logic without using that same primitive.

**Repro**: `tests/security/test_finding_02_service_role_timing_oracle.py`. The microbenchmark shows that `unsafe_eq` has measurable timing dependence on prefix-match length; the `hmac.compare_digest` reference is data-independent.

**Remediation**: Add a shared helper:

```typescript
// web-{tester,buyer}/lib/safe-compare.ts
import crypto from 'node:crypto';
export function safeEqual(a: string, b: string): boolean {
  const ab = Buffer.from(a);
  const bb = Buffer.from(b);
  if (ab.length !== bb.length) return false;
  return crypto.timingSafeEqual(ab, bb);
}
```

Then call `safeEqual(adminHeader, env.supabaseServiceRoleKey)` in both files.

**Regression test idea**: contract test that fuzzes the header byte-by-byte and asserts wall-time differences between matching and non-matching prefixes are within noise (e.g. < 3σ across 10k samples).

---

### Finding #03 — XFF leftmost element is attacker-controlled &nbsp;|&nbsp; **HIGH** (CVSS 7.5)

**File**: `web-tester/lib/rate-limit.ts:76-82`

**Description**: `clientIpFromHeaders()` takes the leftmost `X-Forwarded-For` entry as the client IP. Vercel APPENDS to XFF rather than sanitising it (documented behaviour). A `curl -H "X-Forwarded-For: 1.1.1.1"` results in Vercel's edge forwarding `X-Forwarded-For: 1.1.1.1, <real_ip>`, and the handler reads `1.1.1.1`. The attacker can rotate this value per request, fragmenting the rate-limit bucket arbitrarily. Combined with the in-memory bucket (line 30, per-Vercel-instance) the configured 12-req/min/IP limit collapses to "effectively unlimited per attacker".

This directly enables Finding #05 (disk-fill DoS) at scale.

**Repro**: `tests/security/test_finding_08_xff_spoofing_ratelimit_bypass.py` — 1000 requests with rotating `X-Forwarded-For` produce 256 distinct rate-limit buckets.

**Remediation**:
- On Vercel: read `x-vercel-forwarded-for` (Vercel's signed header, single IP, not a chain), not `x-forwarded-for`.
- Alternatively: take the RIGHTMOST XFF element (the one your last trusted proxy appended).
- Document the deploy assumption (must be behind Vercel edge, never directly exposed).

**Regression test idea**: pytest fuzz that posts 100 randomised XFF chains and asserts the rate-limiter counts them all into a small set of real-client buckets (defined by `x-vercel-forwarded-for`).

---

### Finding #04 — HMAC tokens are static / replay-able / not payload-bound &nbsp;|&nbsp; **MEDIUM** (CVSS 6.5)

**Files**:
- `web-tester/lib/upload-auth.ts:66-81` (`computeToken`, `computeTokenPrefix`)
- `bin/upload_auth.py:73-90` (Python parity)

**Description**: The token is `HMAC_SHA256(secret, tester_id)`. Properties:
- Deterministic — same input forever produces the same output.
- Per-tester, never per-request — no nonce, no timestamp, no rolling counter.
- Embedded in the `.exe` filename (16-hex prefix) — captured in any screenshot, support ticket, Sentry error report, or Windows event log.
- Not bound to the payload — the same token authenticates any upload, including junk that consumes the per-tester rate-limit budget against a legitimate tester's account.

Engineer B's notes acknowledge "no nonce — confirmed residual risk", but they framed it as accepted. This finding raises the concrete attack: a phished `.exe` filename gives lifetime upload authority that consumes the victim's 30/hr quota and pollutes their reputation (D5 verdict mix).

**Repro**: `tests/security/test_finding_03_hmac_replay_token_reuse.py`.

**Remediation** (Option A — minimal change, no recorder rewrite):
```typescript
// header: X-Upload-Token: <unix_ts>.<HMAC(secret, `${testerId}|${unix_ts}`)>
const FRESHNESS_WINDOW_S = 900;
function verifyFreshToken(testerId: string, header: string, secret: string): boolean {
  const [tsStr, mac] = header.split('.', 2);
  if (!tsStr || !mac) return false;
  const ts = Number(tsStr);
  if (!Number.isFinite(ts)) return false;
  if (Math.abs(Date.now() / 1000 - ts) > FRESHNESS_WINDOW_S) return false;
  const expected = crypto.createHmac('sha256', secret)
    .update(`${testerId}|${ts}`).digest('hex');
  return crypto.timingSafeEqual(Buffer.from(mac), Buffer.from(expected));
}
```

**Option B** — content-bound: include `sha256` of the tarball in the MAC. Server already computes SHA-256 (line 184), cheap to verify.

**Regression test idea**: test that a token computed 16 minutes ago is rejected (with the 15-minute window); current token accepted; token for `tester_a` is rejected when sent against `tester_b`.

---

### Finding #05 — No per-tester storage quota → 720 GiB/day/tester DoS &nbsp;|&nbsp; **MEDIUM** (CVSS 6.5)

**Files**:
- `web-tester/app/api/upload-tarball/route.ts:45,53-55,200-215`
- `web-tester/lib/rate-limit.ts:30-31`

**Description**: Each upload is capped at 1 GiB; per-tester rate-limit is 30/hr. That's 720 GiB/day per tester at the documented limit, **per Vercel instance**. Vercel's elastic scaling spins multiple instances and the rate-limit Map is per-instance, so the effective ceiling is N×(instance count). No cumulative storage quota exists. Supabase storage at ~$25/TB/mo means one abusive tester can burn ~$5,400/mo in storage costs.

Combined with Finding #03 (XFF bypass) the IP-side gate is also defeated, so a single attacker controlling one tester account can scale to the Vercel instance ceiling.

**Repro**: `tests/security/test_finding_06_disk_fill_dos.py`.

**Remediation**:
1. Add `testers.storage_quota_bytes_per_day` (e.g. 50 GiB default) and a rolling-window calculation against `tarballs.size_bytes WHERE uploaded_at > now() - interval '24 hours'`.
2. In `/api/upload-tarball`, after `file.size` is known, run a `SELECT FOR UPDATE` on testers + summing recent tarballs, and 429 if over quota. Same transaction as the insert.
3. Move the rate-limit state to a shared store (Upstash Redis recommended; `lib/rate-limit.ts` already documents this as the planned upgrade).

**Regression test idea**: integration test that uploads N×1 GiB tarballs and asserts the 51st (over the 50-GiB-quota threshold) returns 429.

---

### Finding #06 — 24-hour signed URLs outlive license revocation &nbsp;|&nbsp; **MEDIUM** (CVSS 5.4)

**Files**:
- `web-buyer/lib/env.ts:32` (`downloadLinkTtlSeconds: ... '86400'`)
- `web-buyer/app/api/downloads/[purchaseId]/route.ts:178-183`

**Description**: Supabase signed URLs include no buyer-identity claim and survive for 24 hours. Implications:
- A leaked signed URL is fetchable by ANYONE for 24 h.
- A license revoked after mint (chargeback, DMCA, fraud) is still downloadable until the URL expires.
- The /api/downloads route logs the MINT but not subsequent CDN GETs, so forensic IR cannot identify the leaker.

**Repro**: `tests/security/test_finding_07_signed_url_ttl_24h.py`.

**Remediation**:
1. Drop `DOWNLOAD_LINK_TTL_SECONDS` to 3600 (1 hour, Supabase's documented recommendation).
2. Better: stream the tarball through a custom route handler (`/api/downloads/[purchaseId]/blob`) that re-checks the license + audit-logs each byte-range request. Removes the cached-URL window entirely.

**Regression test idea**: contract test that revokes a purchase row mid-download and asserts the next byte-range request is denied (only works after switching to the stream-through pattern).

---

### Finding #07 — Cross-tester SHA-256 collision leaks foreign tester_id &nbsp;|&nbsp; **MEDIUM** (CVSS 5.3)

**Files**:
- `web-tester/app/api/upload-tarball/route.ts:232-245` (`23505` duplicate-handling branch)
- `web-tester/supabase/migrations/20260507000000_init.sql:41` (`unique index tarballs_sha256_idx`)

**Description**: The unique index is on `sha256` GLOBALLY, not `(tester_id, sha256)`. When a second tester uploads a tarball with a colliding SHA-256, the route returns the EXISTING row to the second uploader — including the original `tester_id`. That leaks attribution metadata and gives the duplicate uploader a way to enumerate the legitimate tester_id namespace by re-uploading public-bucket samples.

Storage-side: the route uploads to `${tester_id}/${sha}.tar.gz` using the CLAIMED tester_id, then handles "already exists" on storage as success (line 209) — leaving an orphan blob in the second tester's namespace if the DB step fails. Quota implication: that blob counts against storage cost but never against `tarballs` rows.

**Repro**: `tests/security/test_finding_04_tarball_sha_idempotency.py`.

**Remediation**:
1. Change the index to `unique (tester_id, sha256)` so two testers can both legitimately upload identical content.
2. On `23505`, only return the existing row when `existing.tester_id == claim.tester_id`; otherwise return 409 with a generic message (no tester_id in the body).
3. Or: keep the global unique index but return 409 with NO row payload for cross-tester duplicates.

**Regression test idea**: pytest that uploads SHA `X` as tester A, then attempts upload as tester B; asserts 409 and that response body does not contain `'tester-A'`.

---

### Finding #08 — `/api/cart/add` row-DoS via arbitrary UUIDs &nbsp;|&nbsp; **MEDIUM** (CVSS 5.0)

**File**: `web-buyer/app/api/cart/add/route.ts:53-68`

**Description**: The route validates only that `tarball_id` is a well-formed UUID, then upserts via service-role (bypassing RLS, no catalog-existence check, no cart-size cap, no rate-limit). A signed-in user can POST 86,400 distinct UUIDs/day at 1 RPS, growing `cart_items` unbounded. The unique index on `(buyer_id, tarball_id)` blocks duplicates but not novel UUIDs. The downstream checkout flow silently drops UUIDs missing from the catalog, so the abuse is invisible to the buyer-facing UI.

**Repro**: `tests/security/test_finding_05_cart_inject_arbitrary_uuid.py`.

**Remediation**:
1. After UUID validation, SELECT from `tarballs` (or `catalog_metadata`) and return 404 if the row doesn't exist.
2. Enforce a cart cap (e.g. 100 items / buyer) before insert.
3. Add per-buyer rate-limit (`cart:${buyer_id}` key, 60 ops/hr).
4. Add `cart_items_buyer_count_idx` and a periodic cron that flags buyers with >1000 cart items as suspicious.

**Regression test idea**: contract test that posts 101 fresh UUIDs and asserts the 101st returns 409 (cart full).

---

### Finding #09 — Storage RLS policy is dead code; no buyer-side download policy &nbsp;|&nbsp; **LOW** (CVSS 3.7)

**File**: `web-tester/supabase/migrations/20260507000000_init.sql:152-159`

**Description**: The policy

```sql
create policy "tester reads own tarball blobs" on storage.objects
  for select to authenticated
  using (
    bucket_id = 'tarballs'
    and (storage.foldername(name))[1] = auth.uid()::text
  );
```

is never consulted at runtime because both the upload AND the buyer-side signed-URL mint use the service-role client (which bypasses RLS). The policy as written would BLOCK a legitimate buyer-download flow if the team ever tried to remove the service-role mediator (buyer's `auth.uid()` is not the tester's, so the foldername check fails).

Defense-in-depth gap: no policy exists that says "an authenticated buyer who owns a `purchases` row pointing to the tarball can SELECT the blob". So if service-role is later disabled (e.g. for a per-buyer Supabase project model) downloads will silently break OR be forced to use a less-secure pattern.

**Repro**: `tests/security/test_finding_09_buyer_email_pii_in_signed_path.py`.

**Remediation**:
```sql
create policy "buyer reads paid tarball blobs" on storage.objects
  for select to authenticated
  using (
    bucket_id = 'tarballs'
    and exists (
      select 1 from public.purchases p
      join public.tarballs t on t.id = p.tarball_id
      where p.buyer_id = auth.uid()
        and t.storage_path = storage.objects.name
    )
  );
```

**Regression test idea**: integration test running as a real buyer JWT, calling `storage.download()` against a tarball they HAVE purchased (allowed) and one they HAVEN'T (denied).

---

### Finding #10 — `audit_artifact_honesty.py` only catches IL10 residuals &nbsp;|&nbsp; **LOW** (CVSS 3.1)

**File**: `bin/audit_artifact_honesty.py:24-29`

**Description**: The audit walks four hardcoded directories (`bin/v*_residuals/`, `bin/v3_physics_oracle/`) for Python functions whose parameter names end in `_path` or `_dir`. It does NOT scan:
- `.ts` / `.tsx` files anywhere
- `web-buyer/**`, `web-tester/**`
- `bin/*.py` outside the four pinned dirs
- `vendor/recorder/**` (Rust)
- `sdk/**`, `tasks/**`

So a regression that re-introduces `dev_session_*`, `acct_mock_*`, `cs_mock_*`, `pi_mock_*`, `tr_mock_*`, `po_mock_*`, or `stub_buyer_*` literals in the TS API routes would PASS this lint. Howard's iron-law comments scattered through the codebase (e.g. `IRON-LAW: returns 503 when Supabase isn't configured`) are entirely manual review-time conventions; nothing enforces them.

**Repro**: `tests/security/test_finding_10_audit_artifact_honesty_scope_gap.py` — confirms the audit script's source contains none of the forbidden prefixes nor any `.ts` reference.

**Remediation**: Add `bin/audit_no_fake_ids.py`:

```python
FORBIDDEN_LITERALS = [
    'dev_session_', 'acct_mock_', 'cs_mock_', 'pi_mock_',
    'tr_mock_', 'po_mock_', 'stub_buyer', 'sample-tester@example',
]
EXCLUDE_DIRS = ['tests/', 'vendor/', 'node_modules/', '.git/']
```

…walking every `.ts`/`.tsx`/`.py`/`.sql` file, exiting non-zero on any hit outside `EXCLUDE_DIRS`. Wire it into pre-commit + CI.

**Regression test idea**: itself a regression test — runs in CI; fails the build if any forbidden literal lands in non-test code.

---

## Things checked + confirmed OK (do NOT re-audit)

These surfaces were reviewed and found acceptable for the launch threat model. Future PRs that touch these files should re-trigger review; current state is fine.

| Surface | File | Why it's OK |
|---|---|---|
| HMAC upload-token verify | `web-tester/lib/upload-auth.ts:120-123` | Uses `crypto.timingSafeEqual` correctly. Both Python (`hmac.compare_digest`) and TS sides match. Iron-law banner (line 24-28) is accurate. |
| Upload SHA-256 verification | `web-tester/app/api/upload-tarball/route.ts:183-191` | Server recomputes SHA-256 over the streamed body and compares to the client-claimed value. Mismatch returns 422 (correct). |
| Path traversal in storage path | `web-tester/app/api/upload-tarball/route.ts:193` | `storagePath` is built from server-side-validated `tester_id` (UUID) + server-computed `sha` (64 hex chars). No client-controlled component reaches `supabase.storage.upload`. |
| Path traversal in `/api/download/[testerId]` | `web-tester/app/api/download/[testerId]/route.ts:53` | `path.join(process.cwd(), 'public', env.recorderExeUrl.replace(/^\//, ''))` — `env.recorderExeUrl` is an OPERATOR-CONTROLLED env var (not user input), so attacker can't pivot it. Verified `params.testerId` is UUID-only (line 37, Zod). |
| Stripe webhook signature verification | `web-buyer/app/api/checkout/webhook/route.ts:50-60` | Reads raw body via `req.text()` BEFORE any JSON parsing; calls `stripe.webhooks.constructEvent(rawBody, sig, env.stripeWebhookSecret)` correctly. 400 on missing sig, 400 on verify failure. |
| Stripe webhook idempotency | `web-buyer/app/api/checkout/webhook/route.ts:124-138` | `(buyer_id, tarball_id, stripe_session_id)` unique index + `23505` short-circuit means re-delivered webhooks are no-ops. Correct. |
| `/api/checkout` money-minting | `web-buyer/app/api/checkout/route.ts:39-57,134-176` | Hard-gates on `isSupabaseConfigured()` and `isStripeConfigured()`. NO `dev_session_*` fabrication path. Server-side discounts derived from server env (not client). Cart filtered to catalog rows server-side. |
| `/api/checkout` race between intent and payout | n/a | Stripe Checkout sessions are server-created with the actual cart items and discount applied. There is no "checkout intent" in the DB that could drift from the payout. Confirmed there is no parallel free-mint path. |
| `/api/checkout` `dev_session_*` removal | Both portals + `lib/env.ts` | grep across both portals for `dev_session_`, `acct_mock_`, `tr_mock_`, `po_mock_`: 0 hits in production source. Mock-Stripe + Mock-Supabase stay behind `__testOnly*` exports + `--dry-run` flag. |
| `sanitizeNextPath` open-redirect | `web-buyer/lib/safe-redirect.ts`, `web-tester/lib/safe-redirect.ts` | Rejects `//`, `/\`, `/proto:`, non-leading-`/`. Correctly mitigates `?next=//evil.com`. |
| Cart cookie tampering | `web-buyer/lib/cart-cookie.ts` | httpOnly, sameSite=lax, Secure in prod, JSON-validated, type-filtered, deduplicated. Worst case: user injects UUIDs into their OWN cookie (same as #08 but client-side; merges into their cart via auth callback). No CSRF surface. |
| Stripe Connect `dashboard` route | `web-tester/app/api/stripe/connect/dashboard/route.ts` | Gated on signed-in user + `charges_enabled` + service-role-loaded `stripe_account_id`. No client-controlled input. Login link is a one-shot Stripe-issued URL. OK. |
| Stripe Connect `onboard` route | `web-tester/app/api/stripe/connect/onboard/route.ts` | Account-create body uses tester's verified email + server-validated metadata. Account-link refresh/return URLs are server-built. OK. |
| Stats route `/api/stats/[testerId]` auth | `web-tester/app/api/stats/[testerId]/route.ts:58` | Properly checks `user.id !== testerId` AND service-role header. Logic is right; only the timing-oracle (Finding #02) is wrong. |
| `/api/downloads/[purchaseId]` ownership | `web-buyer/app/api/downloads/[purchaseId]/route.ts:118-123` | Selects the purchase row, then enforces `purchase.buyer_id !== user.id`. Same comment as stats — only the admin-header compare (Finding #02) is wrong. |
| Length-extension on HMAC-SHA256 | `lib/upload-auth.ts` | HMAC-SHA256 (not raw SHA256) — not vulnerable to length-extension regardless. Confirmed `crypto.createHmac('sha256', secret)` is used; the construction is HMAC, not `hash(secret || message)`. |
| 16-hex prefix brute-force resistance | `lib/upload-auth.ts:74-81` | 64-bit prefix → ~2^32 online attempts to forge by birthday bound. The per-IP rate-limit (12/min) and per-tester rate-limit (30/hr) cap this at <2^21 attempts/year/IP. Practically infeasible. The prefix scheme is sound. |
| `bin/payout_cron.py` idempotency | `bin/payout_cron.py:264-293,406-408` | Idempotency key = `payout-{tester_id}-{run_date}`; Stripe `Idempotency-Key` header + DB unique index `payouts_idempotency_key_idx`. Same-day re-runs produce no duplicate transfers. |
| Auth callback `?code=` reuse | `web-{buyer,tester}/app/auth/callback/route.ts` | `supabase.auth.exchangeCodeForSession(code)` is single-use server-side. Tested by Supabase; no local custom verification. OK. |
| `/api/tarball/[id]/preview` | `web-buyer/app/api/tarball/[id]/preview/route.ts` | Public preview, intentionally no auth. Reads a separate `action_camera_preview.jsonl` blob (first 100 records only, not full payload). No data-fabrication path (returns 404 if blob missing). Cache-Control headers permit CDN caching of public previews — acceptable. |
| RLS on `purchases`, `licenses`, `cart_items`, `buyers` | `web-buyer/supabase/migrations/20260507000000_buyer_init.sql:163-188` | All four tables have `enable row level security` + own-row policies bound to `auth.uid()`. Service-role bypass is documented + intentional. Confirmed no policy uses `using (true)` for read except `catalog_metadata` (intentionally public). |

---

## Top 3 must-fix-before-launch

1. **Finding #01 (Stripe Connect account hijack, CVSS 9.6)** — one phished GET steals every future payout. Estimated dev-time: 5 minutes (delete two lines).
2. **Finding #02 (service-role timing oracle, CVSS 7.5)** — extraction of the service-role key compromises the entire DB. Estimated dev-time: 30 minutes (shared `safeEqual` helper + two call-site changes).
3. **Finding #03 (XFF rate-limit bypass, CVSS 7.5)** — defeats one of the two upload rate-limit gates and amplifies Finding #05. Estimated dev-time: 15 minutes (read `x-vercel-forwarded-for` instead).

Findings #04, #05, #06, #07 should be addressed in the post-launch hardening sprint; #08, #09, #10 are launch-acceptable with monitoring + a follow-up.

---

## Reproducer test summary

```
$ python3 -m pytest tests/security/ -v
======================== 26 passed in 0.57s ========================
```

All 10 findings have at least one PASSING test that demonstrates the bug AND one PASSING test that exercises the recommended fix. No false positives — each test was written against the actual code path read from the repo at HEAD = `6f78baf`.

## Audit scope deliberately excluded

To keep the audit focused, the following surfaces were NOT examined and may warrant their own pass:
- Recorder client (`vendor/recorder/**`, Rust) — only the HMAC token contract was checked (Python parity); the Rust port itself is unread.
- Mineflayer / MC-mod (`mineflayer/`, `mc-mod/`) — separate trust boundary; uploads come from the bundled recorder, not these.
- `sdk/` (buyer SDK) — separate review; no auth or storage surface inspected here.
- `tasks/` cron scripts beyond `payout_cron.py`.
- Recorder installer Inno Setup `.iss` (not a network surface).
- GitHub Actions secrets / `.github/workflows/` (separate supply-chain audit).
