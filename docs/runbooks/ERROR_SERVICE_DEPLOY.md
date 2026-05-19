<!--
G238 · Error Service Deployment Runbook
Purpose: Deploy runbook for the error reporting service
Target: Render or Railway + Postgres + env vars + alembic migrate + healthcheck + E2E test
-->

# Error Reporting Service Deployment Runbook

## Overview

This runbook covers deploying the Error Reporting Service to Render or Railway,
including PostgreSQL setup, environment configuration, Alembic migrations,
healthcheck verification, and end-to-end testing.

**Platforms:** Render or Railway | **Database:** PostgreSQL 14+ | **Runtime:** Python 3.11+

---

## Prerequisites

- Platform account (Render or Railway) with deployment permissions
- GitHub repository access
- PostgreSQL database credentials
- Local tools: Python 3.11+, psql, alembic, curl

---

## Database Setup

### Render PostgreSQL

1. Dashboard → New → PostgreSQL
2. Configure: Name `error-service-db`, select region, PostgreSQL 14+
3. Note the Internal Connection String for the service

### Railway PostgreSQL

```bash
railway new error-service
railway add --plugin postgresql
railway variables  # View credentials
```

---

## Environment Variables

### Required

| Variable | Description |
|----------|-------------|
| `DATABASE_URL` | PostgreSQL connection string |
| `SECRET_KEY` | Application secret (32+ chars) |
| `ALLOWED_ORIGINS` | CORS allowed origins |

### Optional

| Variable | Description | Default |
|----------|-------------|---------|
| `LOG_LEVEL` | Logging verbosity | `INFO` |
| `ERROR_RETENTION_DAYS` | Days to retain errors | `30` |

### Setting Variables

**Render:** Dashboard → Environment tab, or CLI:
```bash
render env set DATABASE_URL "postgresql://..."
render env set SECRET_KEY "$(openssl rand -hex 32)"
```

**Railway:**
```bash
railway variables set DATABASE_URL "postgresql://..."
railway variables set SECRET_KEY "$(openssl rand -hex 32)"
```

---

## Deployment Steps

### Dockerfile

Ensure your repository has a Dockerfile:

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

### Render Deployment

1. Dashboard → New → Web Service → Connect GitHub repo
2. Configure:
   - Name: `error-service`
   - Build: `pip install -r requirements.txt`
   - Start: `alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port $PORT`
3. Link PostgreSQL database (auto-populates `DATABASE_URL`)
4. Click "Create Web Service"

### Railway Deployment

```bash
npm install -g @railway/cli
railway login
railway init
railway up
railway domain  # Configure domain
```

---

## Database Migration

### Pre-Deploy Verification

```bash
export DATABASE_URL="postgresql://localhost/error_service_dev"
alembic current              # Check status
alembic upgrade head         # Apply migrations
```

### Auto-Migration on Deploy

Include migration in start command:
```bash
alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

### Manual Migration

```bash
# Render shell
render ssh && cd /app && alembic upgrade head

# Railway CLI
railway run alembic upgrade head
```

### Verify Migration

```bash
alembic current  # Should show (head)
psql $DATABASE_URL -c "\dt"  # List tables
```

---

## Healthcheck Configuration

### Application Health Endpoint

```python
# app/main.py
from fastapi import FastAPI
from datetime import datetime

app = FastAPI()

@app.get("/health")
async def health_check() -> dict:
    """Health check endpoint for load balancers."""
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "service": "error-reporting-service"
    }
```

### Platform Configuration

**Render (render.yaml):**
```yaml
services:
  - type: web
    name: error-service
    healthCheckPath: /health
```

**Railway:** Uses TCP health check by default. For HTTP, configure in dashboard.

### Verify Healthcheck

```bash
SERVICE_URL="https://error-service.onrender.com"
curl -s "$SERVICE_URL/health" | jq .
# Expected: {"status": "healthy", "timestamp": "...", "service": "error-reporting-service"}
```

---

## End-to-End Testing

### Test 1: POST a Fake Error

```bash
SERVICE_URL="https://error-service.onrender.com"

curl -X POST "$SERVICE_URL/api/v1/errors" \
  -H "Content-Type: application/json" \
  -d '{
    "error_type": "TestError",
    "message": "Deployment verification test error",
    "stack_trace": "File \"test.py\", line 42\n  raise TestError",
    "context": {"environment": "production", "version": "1.0.0"},
    "severity": "warning"
  }'

# Expected: {"id": "err_...", "status": "received"}
```

### Test 2: Verify Error in Dashboard

```bash
# Query API
curl -s "$SERVICE_URL/api/v1/errors?limit=10" | jq .

# Or open dashboard in browser
open "$SERVICE_URL/dashboard"
```

### Test 3: Error Detail and Resolution

```bash
ERROR_ID="err_abc123xyz"

# Get error details
curl -s "$SERVICE_URL/api/v1/errors/$ERROR_ID" | jq .

# Mark as resolved
curl -X PATCH "$SERVICE_URL/api/v1/errors/$ERROR_ID" \
  -H "Content-Type: application/json" \
  -d '{"status": "resolved"}'
```

### Automated E2E Test Script

```bash
#!/usr/bin/env bash
# E2E Test Script - save as scripts/e2e_test.sh
set -euo pipefail

cleanup() { echo "Cleanup complete."; }
trap cleanup EXIT

SERVICE_URL="${1:?Usage: $0 <SERVICE_URL>}"

echo "=== Error Service E2E Test ==="

# Health check
HEALTH=$(curl -s -o /dev/null -w "%{http_code}" "$SERVICE_URL/health")
[[ "$HEALTH" == "200" ]] && echo "✓ Health check passed" || exit 1

# POST error
RESPONSE=$(curl -s -X POST "$SERVICE_URL/api/v1/errors" \
  -H "Content-Type: application/json" \
  -d '{"error_type":"E2ETest","message":"Automated test","severity":"info"}')
ERROR_ID=$(echo "$RESPONSE" | jq -r '.id')
[[ -n "$ERROR_ID" && "$ERROR_ID" != "null" ]] && echo "✓ Error created: $ERROR_ID" || exit 1

# Retrieve error
DATA=$(curl -s "$SERVICE_URL/api/v1/errors/$ERROR_ID")
echo "$DATA" | jq -r '.error_type' | grep -q "E2ETest" && echo "✓ Error retrieved" || exit 1

echo "=== All tests passed ==="
```

Run: `chmod +x scripts/e2e_test.sh && ./scripts/e2e_test.sh "https://your-service.onrender.com"`

---

## Troubleshooting

### Database Connection Errors

```bash
# Test connectivity
psql "$DATABASE_URL" -c "SELECT 1;"

# Check: DATABASE_URL set, same region, database running
```

### Migration Failures

```bash
alembic current  # Check status
alembic downgrade -1 && alembic upgrade head  # Retry
```

### Healthcheck Failures

- Verify `/health` returns HTTP 200
- Check app binds to `0.0.0.0` not `127.0.0.1`
- Review logs: `render logs` or `railway logs`

### View Logs

```bash
render logs --tail    # Render
railway logs          # Railway
```

---

## Rollback Procedures

### Application Rollback

**Render:** Dashboard → Service → Deployments → Rollback on last good deployment

**Railway:** `railway rollback <deployment-id>`

### Database Migration Rollback

```bash
alembic downgrade -1           # Rollback one migration
alembic downgrade <revision>   # Rollback to specific revision
```

### Full Recovery

1. Stop traffic (DNS/maintenance mode)
2. Rollback application via platform
3. Rollback database: `alembic downgrade <revision>`
4. Verify health and run E2E tests
5. Restore traffic

---

## Checklist

- [ ] Database created and `DATABASE_URL` set
- [ ] `SECRET_KEY` generated and set
- [ ] Application deployed
- [ ] Migrations applied (`alembic upgrade head`)
- [ ] Healthcheck passing (`/health` returns 200)
- [ ] E2E test: POST error succeeds
- [ ] E2E test: Error visible in dashboard

---

**Version:** 1.0 | **Updated:** 2024-01-15 | **Maintainer:** Platform Engineering