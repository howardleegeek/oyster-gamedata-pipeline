# Production Gaps — Howard's Hands Required

**Audit date: 2026-05-08, evening**

These are the items that block "production" claim and that I cannot
close autonomously — each needs a credential, a payment, or a Howard
decision. Ordered by blast radius.

---

## 🔴 1. Vercel deploy credentials (BLOCKS PORTAL ACCESS)

**Symptom:** GitHub Actions lane "Deploy Web Buyer (Vercel)" fails with:
> `No deploy credentials. Configure either VERCEL_TOKEN+VERCEL_ORG_ID+VERCEL_PROJECT_ID_BUYER or VERCEL_BUYER_DEPLOY_HOOK in repo secrets.`

**Cost:** $0 (free Vercel hobby tier covers two portals).
**Time:** 5 minutes.

**Fix (option A — CLI tokens, recommended):**
```bash
# 1. Create Vercel projects locally
cd ~/Downloads/oyster-agent-runner/web-tester && vercel link
cd ~/Downloads/oyster-agent-runner/web-buyer  && vercel link

# 2. Generate a token at https://vercel.com/account/tokens
#    Set scope to your team / personal account; expiration: never (rotate later)

# 3. Add to GitHub secrets at:
#    https://github.com/howardleegeek/oyster-gamedata-pipeline/settings/secrets/actions
#    - VERCEL_TOKEN              (the token from step 2)
#    - VERCEL_ORG_ID             (cat web-tester/.vercel/project.json | jq .orgId)
#    - VERCEL_PROJECT_ID_TESTER  (cat web-tester/.vercel/project.json | jq .projectId)
#    - VERCEL_PROJECT_ID_BUYER   (cat web-buyer/.vercel/project.json  | jq .projectId)

# 4. Re-run failed CI: gh run rerun <run-id>
```

**Fix (option B — deploy hook, simpler):**
Skip the CLI/token setup. In each Vercel project's Settings → Git, generate
a Deploy Hook URL and add as `VERCEL_TESTER_DEPLOY_HOOK` /
`VERCEL_BUYER_DEPLOY_HOOK` secrets. Vercel will pull from the GitHub
integration and the CI workflow just pings the hook.

---

## 🔴 2. Supabase migrations not applied (BLOCKS UPLOAD + CATALOG)

**Symptom:** `/api/upload-tarball` returns 500 because `tarballs` table
does not exist on the prod Supabase project. `/api/catalog` returns 0 rows.

**Cost:** $0 (Supabase free tier covers this scale).
**Time:** 2 minutes per portal.

**Fix:**
```bash
# Tester portal
cd ~/Downloads/oyster-agent-runner/web-tester
supabase login
supabase link --project-ref <your-ref>
supabase db push

# Buyer portal — same Supabase project as tester (shared DB), so just verify:
cd ~/Downloads/oyster-agent-runner/web-buyer
supabase db push
```

Migration files (already on `main`):
- `web-tester/supabase/migrations/20260507000000_init.sql`
- `web-tester/supabase/migrations/20260507100000_stripe_connect.sql`
- `web-buyer/supabase/migrations/20260507000000_buyer_init.sql`

---

## 🔴 3. Recorder `.exe` is unsigned (TRIGGERS WINDOWS SMARTSCREEN)

**Symptom:** Every Windows tester sees a "Windows protected your PC" dialog
and has to click "More info → Run anyway." Some testers will refuse.

**Cost:** ~$200/year for an EV (extended-validation) code-signing cert from
DigiCert / Sectigo / SSL.com. Standard OV certs are cheaper (~$80/year) but
do not bypass SmartScreen on first run — they only build reputation over
weeks of downloads.

**Time:** 24-72 hours for cert provisioning (vendor verifies your business
identity). After cert is in hand, ~30 minutes to wire `signtool` into the
recorder release CI.

**Decision needed from Howard:**
- (a) Buy EV cert now → bypass SmartScreen on day 1.
- (b) Buy OV cert → cheaper but testers still see warning until the binary
      builds reputation. Brief testers explicitly: "click More info → Run
      anyway, this is expected for new releases."
- (c) Skip code-signing for launch → document the SmartScreen step in the
      tester onboarding flow. Iron-law-compatible (it's an honest workflow,
      not a fake) but adds friction.

**My recommendation:** (b) for cost, (c) for speed. (a) only if you have
buyers who care about provenance more than testers care about friction.

---

## 🟡 4. Stripe Connect deferred — testers earn imaginary money

**Symptom:** Testers record gameplay, see accumulated `$N.NN` on
`/dashboard`, but the `/payouts` page shows `<NotConfigured>`. They cannot
extract their earnings.

**Cost:** $0 (Stripe Connect Express has no setup fee; ~2.9% + 30¢ per
payout transaction).
**Time:** 30 min Stripe dashboard setup + 30 min wiring + 1 day platform
verification by Stripe.

**Iron-law tension:** Per `feedback_no_prototypes.md`, "production = real
users + real product." A tester portal that displays earned dollars but
cannot pay them out is half-real. The dashboard is iron-law-honest (real
hour counts, real rate), but the lack of payout breaks the social contract
with testers.

**Decision needed from Howard:**
- (a) Wire Stripe Connect tonight (ask MiniMax to split spec; 30-60 min
      execution by GLM agent). Launch with full payout flow.
- (b) Launch without Stripe but commit to wiring it within 7 days. Brief
      testers: "first week is free recording for prize money instead of
      Stripe payout — Howard sends you Venmo / Wise / wire."
- (c) Launch without Stripe and run the recording window as a paid pilot:
      sign 3-5 testers on individual contracts at $25/hour or similar, no
      Stripe needed.

---

## 🟡 5. Other CI lanes failing

`gh run list --status failure --limit 5`:
- "Test" — needs investigation
- "G189 · Heartbeat Skip Check" — needs investigation
- "Deploy Web Buyer (Vercel)" — gap #1 above
- "Deploy Web Tester (Vercel)" — gap #1 above

**Action:** I'll dig into "Test" and "G189" in the next session if you
want them green before launch. They are probably orchestration / heartbeat
checks unrelated to the demo critical path, but worth knowing.

---

## 🟢 What I closed tonight (no Howard credentials needed)

| Gap | Status |
|---|---|
| Rate limiting on `/api/upload-tarball` (12/min/IP, 30/hour/tester, 429 + Retry-After + headers) | ✅ shipped |
| Structured JSON logging on upload accept / reject / duplicate / error / rate-limit / not-configured | ✅ shipped |
| Privacy Policy on both portals (real text, real data flows, no boilerplate) | ✅ shipped |
| Terms of Service on both portals (real license terms, real refund policy) | ✅ shipped |
| `watch.sh` 24/7 production health monitor | ✅ shipped |
| Empty-state honesty on buyer landing | ✅ shipped |
| Iron-law lint blocks fabricated-data regressions | ✅ 24/24 |

---

## TL;DR for Howard

To go from "production-grade code" to "production system serving real users",
you need to:

1. **Add 4 secrets to GitHub repo** (5 min) → Vercel deploys land
2. **Run `supabase db push` × 2** (5 min) → DB schema lands
3. **Decide code-sign strategy** — buy EV cert / brief testers / both
4. **Decide Stripe strategy** — wire it now / Venmo bridge / paid pilot

I cannot do any of those four for you. Everything else (rate limiting,
logging, legal pages, monitoring, iron-law audit) is shipped on `main`.
