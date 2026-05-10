# SM3 — Phase C Atomic: Cloudflare R2 Client + tus.io

> Depends on: SM2 (API stub). Don't dispatch until SM2 routes have stubs.

## Goal
R2 client wrapper + tus.io resumable upload protocol. Output:
`backend/storage/` module wired into routes from SM2.

## Files to create
- `backend/storage/__init__.py`
- `backend/storage/r2_client.py` (boto3 with R2 endpoint, multipart upload helpers)
- `backend/storage/tus_handler.py` (tus.io 1.0.0 server)
- `backend/storage/keying.py` (R2 key format: sessions/{tester}/{yyyy}/{mm}/{id}.tar.gz)

## Tus.io implementation
- POST `/v1/sessions/upload` accepts `Upload-Length`, `Upload-Metadata`
  (b64 `session_id,filename,sha256`), creates upload session record in
  Postgres, returns 201 with `Location: /v1/sessions/upload/{upload_id}`
- PATCH on that location accepts chunks ≥10MB, validates `Upload-Offset`
  matches Postgres state, streams to R2 multipart
- HEAD returns current upload offset
- Final PATCH (offset == length) validates SHA-256 + completes multipart
  upload + inserts into `sessions` table

## R2 buckets
- Use `oyster-gamedata-pilot` initially per Howard 2026-05-09 brand-iron-law decision
- Per-brand buckets created on demand when tester from that brand joins

## Verification
- [ ] Upload 80MB session.tar.gz via tus → R2 object exists at expected key
- [ ] Mid-stream kill + resume → upload completes without duplicate bytes
- [ ] SHA-256 mismatch → 409 Conflict, no DB write, partial R2 multipart aborted
- [ ] Diagnostic ZIP via SM2 endpoint stored at `diagnostics/{tester}/{yyyy}/{mm}/{diagnostic_id}.zip`

## Do NOT
- Implement quarantine workers (SM5)
- Touch JWT (SM4)
