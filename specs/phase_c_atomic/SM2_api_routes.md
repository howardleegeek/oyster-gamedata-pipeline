# SM2 — Phase C Atomic: FastAPI HTTP Endpoints

> Depends on: SM1 (DB schema). Don't dispatch until SM1 deployed.

## Goal
Implement all 8 HTTP endpoints from SM_backend_mvp.md §1 in FastAPI.
Output: `backend/api/` module with route handlers + Pydantic models.

## Files to create
- `backend/api/__init__.py`
- `backend/api/main.py` (FastAPI app factory)
- `backend/api/routes/__init__.py`
- `backend/api/routes/sessions.py` (upload, heartbeat, terminator, status)
- `backend/api/routes/diagnostics.py` (POST /v1/diagnostics + GET + triage)
- `backend/api/routes/health.py` (/v1/healthz)
- `backend/api/models.py` (Pydantic request/response models)
- `backend/api/dependencies.py` (DB session, JWT decode)
- `backend/api/exceptions.py` (typed HTTP exceptions)

## Endpoints (verbatim from SM_backend_mvp.md §1)
1. POST /v1/sessions/upload — tus.io resumable
2. POST /v1/sessions/heartbeat — health.json ingest
3. POST /v1/sessions/terminator — terminator.json ingest
4. GET /v1/sessions/{id}/status — query
5. GET /v1/healthz — no auth
6. POST /v1/diagnostics — multipart zip upload (catbox.moe replacement)
7. GET /v1/diagnostics/{id} — engineer triage
8. POST /v1/diagnostics/{id}/triage — engineer marks reviewed

## Verification
- [ ] All 8 routes return correct status codes (201/200/404/401/413/429)
- [ ] Pydantic request models reject malformed payloads with 422
- [ ] OpenAPI schema at `/docs` shows all 8 routes with examples
- [ ] Integration test: end-to-end upload → status query → presigned URL works
- [ ] `pytest backend/tests/api/` ≥ 80% coverage

## Do NOT
- Implement R2 client (that's SM3)
- Implement JWT issuer (that's SM4)
- Deploy (that's SM5)
