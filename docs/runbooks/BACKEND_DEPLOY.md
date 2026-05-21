# Backend Deployment Runbook

## Current Canonical Path — Fly.io

The repository currently ships `backend_stub/` as the deployable FastAPI
service. The canonical deployment path is Fly.io, not Render/Railway.

### Required GitHub Secret

Configure this in repository secrets:

```bash
FLY_API_TOKEN=<Fly.io deploy token>
```

Do not commit Fly tokens or `.env` files.

### Manual Deploy From GitHub Actions

Run the manual workflow after `FLY_API_TOKEN` is configured:

```bash
gh workflow run deploy-backend-fly.yml \
  -f backend_url=https://oyster-backend-stub.fly.dev \
  -f fly_app=oyster-backend-stub
gh run watch
```

The workflow deploys `backend_stub/` with `flyctl deploy --remote-only` and
then immediately runs:

```bash
python scripts/verify_deployed_backend.py \
  --url https://oyster-backend-stub.fly.dev \
  --verbose
```

### Scheduled Smoke

After the first successful deploy, set the repo variable:

```bash
gh variable set BACKEND_SMOKE_URL --body https://oyster-backend-stub.fly.dev
```

`backend-remote-smoke.yml` will then keep checking `/healthz`, tester apply,
income, and appcast. Without that variable, scheduled smoke intentionally
skips so the repo does not page on an undeployed backend.

### Local Deploy Fallback

If a local Fly CLI is authenticated, this also works:

```bash
./scripts/deploy_backend.sh
python scripts/verify_deployed_backend.py \
  --url https://oyster-backend-stub.fly.dev \
  --verbose
```

If `https://oyster-backend-stub.fly.dev` does not resolve, the backend is not
publicly deployed yet or the Fly app/DNS is not active.

---

## Purpose
Legacy reference for deploying a future persistent FastAPI backend to Render or
Railway: environment variables, PostgreSQL URL, S3 credentials, Alembic
migrations, and health check verification.

## Prerequisites
- Python 3.9+
- Git CLI
- PostgreSQL client (for local testing)
- AWS account (for S3)

---

## Step 1: Environment Configuration

### Required Environment Variables
Create a `.env` file (add to `.gitignore`):

```bash
# Database
DATABASE_URL=postgresql://user:password@host:port/database

# S3 Storage
AWS_ACCESS_KEY_ID=your_access_key
AWS_SECRET_ACCESS_KEY=your_secret_key
AWS_REGION=us-east-1
S3_BUCKET_NAME=your-bucket-name

# Application
APP_ENVIRONMENT=production
SECRET_KEY=your-secret-key-min-32-chars
CORS_ORIGINS=https://your-frontend.com
LOG_LEVEL=INFO
```

### Load Environment in FastAPI
```python
# main.py
from functools import lru_cache
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    database_url: str
    aws_access_key_id: str
    aws_secret_access_key: str
    aws_region: str = "us-east-1"
    s3_bucket_name: str
    secret_key: str
    cors_origins: str
    log_level: str = "INFO"

    class Config:
        env_file = ".env"

@lru_cache
def get_settings() -> Settings:
    return Settings()
```

---

## Step 2: Deploy to Render

### 2.1 Create Web Service
1. Log into [Render Dashboard](https://dashboard.render.com)
2. Click **New +** → **Web Service**
3. Connect GitHub repository
4. Configure:
   - **Name**: `fastapi-backend`
   - **Environment**: `Python 3`
   - **Region**: Nearest to users
   - **Branch**: `main`
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `uvicorn main:app --host 0.0.0.0 --port $PORT`

### 2.2 Add PostgreSQL
1. **New +** → **PostgreSQL**
2. Configure database name and region
3. Copy `Internal Database URL` from PostgreSQL
4. Paste into web service **Environment Variables** as `DATABASE_URL`

### 2.3 Add S3 Credentials
Add to web service environment variables:
```env
AWS_ACCESS_KEY_ID=AKIA...
AWS_SECRET_ACCESS_KEY=...
AWS_REGION=us-east-1
S3_BUCKET_NAME=your-bucket
```

---

## Step 3: Deploy to Railway

### 3.1 Create Project
1. Log into [Railway](https://railway.app)
2. Click **New Project** → **Deploy from GitHub repo**
3. Select repository

### 3.2 Add PostgreSQL
1. Click **New +** → **Add Database** → **PostgreSQL**
2. Copy `DATABASE_URL` from **Variables** tab

### 3.3 Add Environment Variables
Add in Railway **Variables** tab:
```env
DATABASE_URL=postgresql://...
AWS_ACCESS_KEY_ID=AKIA...
AWS_SECRET_ACCESS_KEY=...
AWS_REGION=us-east-1
S3_BUCKET_NAME=your-bucket
SECRET_KEY=your-secret-key
CORS_ORIGINS=https://your-frontend.com
```

---

## Step 4: Run Alembic Migrations

### 4.1 Initialize Alembic (if not done)
```bash
alembic init alembic
```

### 4.2 Configure alembic.ini
```ini
[alembic]
sqlalchemy.url = driver://user:pass@localhost/dbname
```

### 4.3 Create Migration
```bash
alembic revision --autogenerate -m "create initial tables"
```

### 4.4 Run Migrations
```bash
# Locally
alembic upgrade head

# On Render/Railway (via shell)
alembic upgrade head
```

### 4.5 Verify Migration Status
```bash
alembic current
alembic history --verbose
```

---

## Step 5: Health Check Verification

### 5.1 Add Health Endpoint
```python
# main.py
from fastapi import FastAPI
from fastapi.responses import JSONResponse

app = FastAPI()

@app.get("/health")
def health_check() -> JSONResponse:
    """Health check endpoint for deployment verification."""
    return JSONResponse(
        status_code=200,
        content={"status": "healthy", "service": "fastapi-backend"}
    )

@app.get("/health/ready")
def readiness_check() -> JSONResponse:
    """Readiness check including database connectivity."""
    try:
        # Test database connection
        from sqlalchemy import text
        from main import engine
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return JSONResponse(
            status_code=200,
            content={"status": "ready", "database": "connected"}
        )
    except Exception as e:
        return JSONResponse(
            status_code=503,
            content={"status": "not ready", "error": str(e)}
        )
```

### 5.2 Test Health Endpoints
```bash
# Basic health
curl https://your-backend.onrender.com/health

# Readiness (includes DB check)
curl https://your-backend.onrender.com/health/ready
```

### 5.3 Configure Platform Health Checks
- **Render**: Settings → **Health Check** → Path: `/health`
- **Railway**: Settings → **Healthcheck** → Path: `/health`

---

## Step 6: Verify S3 Configuration

### 6.1 Test S3 Connectivity
```python
# test_s3.py
import boto3
from botocore.exceptions import ClientError

def verify_s3_access(bucket_name: str, region: str) -> bool:
    """Verify S3 bucket is accessible."""
    s3_client = boto3.client("s3", region_name=region)
    try:
        s3_client.head_bucket(Bucket=bucket_name)
        return True
    except ClientError as e:
        print(f"S3 error: {e}")
        return False

if __name__ == "__main__":
    import os
    bucket = os.getenv("S3_BUCKET_NAME")
    region = os.getenv("AWS_REGION", "us-east-1")
    print(f"S3 accessible: {verify_s3_access(bucket, region)}")
```

### 6.2 Run S3 Verification
```bash
python test_s3.py
```

---

## Troubleshooting

### Database Connection Failed
- Verify `DATABASE_URL` is correct
- Check PostgreSQL is in same region as web service
- Ensure IP allowlist includes platform IPs

### Alembic Migration Fails
- Check `alembic.ini` has correct `sqlalchemy.url`
- Ensure database user has migration permissions
- Run `alembic stamp <revision>` to sync state

### S3 Upload Fails
- Verify credentials have `s3:PutObject` and `s3:GetObject` permissions
- Check bucket policy allows cross-account access if needed
- Confirm `AWS_REGION` matches bucket region

### Health Check Returns 503
- Check application logs in platform dashboard
- Verify all required environment variables are set
- Test locally with same environment variables

---

## Rollback Procedure

### Quick Rollback (Previous Deployment)
1. Go to Deployments in platform dashboard
2. Find last working deployment
3. Click **Redeploy**

### Database Rollback
```bash
alembic downgrade -1
# Or specific revision
alembic downgrade <revision_number>
```

---

## Security Checklist
- [ ] `SECRET_KEY` is minimum 32 characters
- [ ] `DATABASE_URL` does not contain credentials in code
- [ ] S3 credentials have minimal required permissions
- [ ] CORS origins restricted to known domains
- [ ] Environment variables set in platform, not in code
- [ ] `.env` file in `.gitignore`
