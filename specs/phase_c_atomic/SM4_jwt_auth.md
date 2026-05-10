# SM4 — Phase C Atomic: JWT Issuer + Per-Brand Keys + Rate Limit

> Depends on: SM2 (route deps). Don't dispatch until SM2 routes wired.

## Goal
Per-brand JWT issuance + verification + slowapi rate limit. Output:
`backend/auth/` module + `backend/cli/issue_jwt.py` for engineer-side
tester onboarding.

## Files to create
- `backend/auth/__init__.py`
- `backend/auth/jwt_issuer.py` (HS256, per-brand secret keys)
- `backend/auth/jwt_verifier.py` (FastAPI dependency)
- `backend/auth/rate_limit.py` (slowapi config: 100/min sessions, 10/hr diagnostics)
- `backend/cli/issue_jwt.py` (admin CLI: issue tester JWT)

## Brand-key isolation per iron law
- `JWT_SECRET_PILOT`, `JWT_SECRET_CLAWGLASSES`, `JWT_SECRET_OYSTER`, etc.
- Loaded from env / Supabase secrets, NEVER co-mingled
- JWT claim `kid` indicates which brand secret to verify against
- Lookup table: `brand_to_secret = {pilot: env, clawglasses: env, ...}`

## Rate limits
- 100 req/min per tester for /sessions/* endpoints
- 10 req/hr per tester for /diagnostics endpoint (zips are big)
- Engineer endpoints (GET /diagnostics/{id}, triage): 1000/hr

## Verification
- [ ] JWT signed with `JWT_SECRET_PILOT` rejected by `JWT_SECRET_CLAWGLASSES` verifier
- [ ] 101 req/min from same tester → 429 Too Many Requests
- [ ] 11 diagnostic uploads from same tester in 1 hour → 429
- [ ] Engineer JWT with admin scope can call `/diagnostics/{id}/triage`
- [ ] Tester JWT cannot call triage endpoint (403)

## Do NOT
- Refresh tokens (P1, separate)
- OAuth integration (overkill for pilot)
