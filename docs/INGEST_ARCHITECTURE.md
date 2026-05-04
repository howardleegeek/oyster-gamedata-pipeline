# R037 · Ingest Architecture

> Backend pipeline scaffold for automated clip ingestion, linting, and triage.

## Overview

PRD §7 describes vendor uploads to S3, but stops at manual triage. At 1000 clips/day (~42/hour), manual review is impossible. This document describes the automated ingestion pipeline that replaces manual triage with a lint-based workflow and web dashboard.

---

## 1. Architecture Diagram

```
┌─────────────────────┐
│   Vendor (Windows)  │
│  aws s3 sync / CLI  │
└──────────┬──────────┘
           │ S3 PUT (multipart)
           ▼
┌─────────────────────────────────┐
│     S3 Bucket (vendor uploads)  │
│  ┌───────────────────────────┐  │
│  │ raw/                      │  │
│  │   vendor_id/batch_id/*.mp4│  │
│  └───────────────────────────┘  │
└──────────┬──────────────────────┘
           │ S3 PUT Event Notification
           ▼
┌─────────────────────────────────┐
│        SQS Queue                │
│  (ingest-pending-queue)         │
│  Visibility: 5min               │
│  DLQ: ingest-dlq                │
└──────────┬──────────────────────┘
           │ pull (long poll)
           ▼
┌─────────────────────────────────┐
│  Lambda / Cloud Run Worker      │
│  ┌───────────────────────────┐  │
│  │ 1. Extract metadata       │  │
│  │ 2. Compute SHA256         │  │
│  │ 3. lint_buyer_spec()      │  │
│  │ 4. Write to Postgres      │  │
│  └───────────────────────────┘  │
└──────────┬──────────────────────┘
           │ INSERT submission
           ▼
┌─────────────────────────────────┐
│     Postgres `submissions`       │
│  status: received               │
│         | lint_pass             │
│         | lint_fail             │
│         | sampled               │
│         | accepted              │
│         | rejected             │
│         | paid                  │
└──────────┬──────────────────────┘
           │ READ/WRITE
           ▼
┌─────────────────────────────────┐
│   Web Dashboard (Flask + HTMX)  │
│   ┌───────────────────────────┐ │
│   │ Auth: OAuth2 / SAML       │ │
│   │ Users: Howard + ops team  │ │
│   │ Views:                    │ │
│   │   - Batch overview        │ │
│   │   - Lint failures         │ │
│   │   - Acceptance workflow   │ │
│   │   - Payment summary       │ │
│   └───────────────────────────┘ │
└──────────┬──────────────────────┘
           │ acceptance signal
           ▼
┌─────────────────────────────────┐
│   SQS → Vendor Notification     │
│   ┌───────────────────────────┐ │
│   │ Email (SES)               │ │
│   │ API webhook (POST)        │ │
│   │ Dashboard self-serve      │ │
│   └───────────────────────────┘ │
└─────────────────────────────────┘
```

### Data Flow Summary

| Stage | Component | Action |
|-------|-----------|--------|
| 1 | Vendor | `aws s3 sync` uploads clips to `s3://bucket/raw/{vendor_id}/{batch_id}/` |
| 2 | S3 | PUT event triggers notification to SQS |
| 3 | SQS | Queues messages with bucket/key metadata |
| 4 | Lambda | Pulls message, extracts metadata, runs `lint_buyer_spec()` |
| 5 | Postgres | Records submission with status `lint_pass` or `lint_fail` |
| 6 | Dashboard | Howard reviews batches, marks `accepted` or `rejected` |
| 7 | Notification | Vendor receives acceptance/rejection via email or webhook |

---

## 2. Postgres Schema

### 2.1 Submissions Table

Primary table for tracking each clip through the ingestion pipeline.

```sql
CREATE TABLE submissions (
    id              BIGSERIAL PRIMARY KEY,
    vendor_id       VARCHAR(64) NOT NULL,
    batch_id        VARCHAR(64) NOT NULL,
    clip_id         VARCHAR(128) NOT NULL,
    s3_key          VARCHAR(512) NOT NULL,
    sha256          VARCHAR(64),
    size            BIGINT,
    duration_sec    FLOAT,
    status          VARCHAR(32) NOT NULL DEFAULT 'received',
    lint_summary    JSONB,
    ingested_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    
    CONSTRAINT uq_submission_clip UNIQUE (vendor_id, batch_id, clip_id),
    CONSTRAINT chk_status CHECK (status IN (
        'received', 'lint_pass', 'lint_fail', 
        'sampled', 'accepted', 'rejected', 'paid'
    ))
);

CREATE INDEX idx_submissions_vendor ON submissions(vendor_id);
CREATE INDEX idx_submissions_batch ON submissions(vendor_id, batch_id);
CREATE INDEX idx_submissions_status ON submissions(status);
CREATE INDEX idx_submissions_ingested ON submissions(ingested_at DESC);
```

**Status Transitions:**

```
received ──► lint_pass ──► sampled ──► accepted ──► paid
    │            │              │
    │            └──────────────┴──► rejected
    │
    └──► lint_fail ──► (manual review / re-queue)
```

### 2.2 Events Table

Audit trail for all submission state changes and system events.

```sql
CREATE TABLE events (
    id              BIGSERIAL PRIMARY KEY,
    submission_id   BIGINT NOT NULL REFERENCES submissions(id) ON DELETE CASCADE,
    type            VARCHAR(64) NOT NULL,
    message         TEXT,
    metadata        JSONB,
    occurred_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_events_submission ON events(submission_id);
CREATE INDEX idx_events_type ON events(type);
CREATE INDEX idx_events_time ON events(occurred_at DESC);
```

**Event Types:**

| Type | Description |
|------|-------------|
| `INGESTION_STARTED` | Worker picked up SQS message |
| `LINT_PASSED` | All lint checks passed |
| `LINT_FAILED` | One or more lint checks failed |
| `STATUS_CHANGED` | Manual status update from dashboard |
| `REQUEUED` | Submission re-queued for re-processing |
| `NOTIFICATION_SENT` | Email/webhook sent to vendor |
| `PAYMENT_RECORDED` | Payment amount recorded |

### 2.3 Payments Table

Monthly payment tracking per vendor.

```sql
CREATE TABLE payments (
    id              BIGSERIAL PRIMARY KEY,
    vendor_id       VARCHAR(64) NOT NULL,
    period          VARCHAR(7) NOT NULL,  -- 'YYYY-MM'
    accepted_count  INTEGER NOT NULL DEFAULT 0,
    amount_usd      DECIMAL(10,2) NOT NULL DEFAULT 0.00,
    status          VARCHAR(32) NOT NULL DEFAULT 'pending',
    paid_at         TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    
    CONSTRAINT uq_payment_period UNIQUE (vendor_id, period),
    CONSTRAINT chk_payment_status CHECK (status IN ('pending', 'approved', 'paid', 'cancelled'))
);

CREATE INDEX idx_payments_vendor ON payments(vendor_id);
CREATE INDEX idx_payments_period ON payments(period);
```

### 2.4 SQLAlchemy Models

```python
# models.py
from sqlalchemy import Column, BigInteger, String, Float, JSON, DateTime, Numeric, Integer
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql import func
from database import Base

class Submission(Base):
    __tablename__ = 'submissions'
    
    id = Column(BigInteger, primary_key=True)
    vendor_id = Column(String(64), nullable=False, index=True)
    batch_id = Column(String(64), nullable=False)
    clip_id = Column(String(128), nullable=False)
    s3_key = Column(String(512), nullable=False)
    sha256 = Column(String(64))
    size = Column(BigInteger)
    duration_sec = Column(Float)
    status = Column(String(32), nullable=False, default='received')
    lint_summary = Column(JSONB)
    ingested_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

class Event(Base):
    __tablename__ = 'events'
    
    id = Column(BigInteger, primary_key=True)
    submission_id = Column(BigInteger, nullable=False, index=True)
    type = Column(String(64), nullable=False)
    message = Column(String)
    metadata = Column(JSONB)
    occurred_at = Column(DateTime(timezone=True), server_default=func.now())

class Payment(Base):
    __tablename__ = 'payments'
    
    id = Column(BigInteger, primary_key=True)
    vendor_id = Column(String(64), nullable=False, index=True)
    period = Column(String(7), nullable=False)
    accepted_count = Column(Integer, default=0)
    amount_usd = Column(Numeric(10, 2), default=0)
    status = Column(String(32), default='pending')
    paid_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
```

---

## 3. Failure Recovery

### 3.1 Dead Letter Queue (DLQ)

Failed lint operations are automatically routed to a DLQ for investigation:

```
┌─────────────────────┐
│ ingest-pending-queue│
│  MaxReceiveCount: 3 │
└──────────┬──────────┘
           │ failed 3x
           ▼
┌─────────────────────┐
│    ingest-dlq       │
│  Retention: 14 days │
│  Manual review      │
└─────────────────────┘
```

**Configuration:**

```terraform
# terraform/sqs.tf
resource "aws_sqs_queue" "ingest_pending" {
  name                       = "ingest-pending-queue"
  visibility_timeout_seconds = 300
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.ingest_dlq.arn
    maxReceiveCount     = 3
  })
}

resource "aws_sqs_queue" "ingest_dlq" {
  name                       = "ingest-dlq"
  message_retention_seconds  = 1209600  # 14 days
}
```

### 3.2 Re-queue Button

Dashboard provides a "Re-queue" action for failed submissions:

1. User clicks "Re-queue" on a `lint_fail` submission
2. Backend sends message back to `ingest-pending-queue`
3. Event logged: `REQUEUED` with `{reason: "manual", user: "howard"}`
4. Worker re-processes the submission

```python
# routes/dashboard.py
@bp.route('/submission/<int:submission_id>/requeue', methods=['POST'])
def requeue_submission(submission_id):
    submission = Submission.query.get_or_404(submission_id)
    
    # Send back to SQS
    sqs.send_message(
        QueueUrl=INGEST_QUEUE_URL,
        MessageBody=json.dumps({
            's3_key': submission.s3_key,
            'vendor_id': submission.vendor_id,
            'batch_id': submission.batch_id,
            'clip_id': submission.clip_id,
            'requeue': True
        })
    )
    
    # Log event
    event = Event(
        submission_id=submission.id,
        type='REQUEUED',
        metadata={'user': current_user.email}
    )
    db.session.add(event)
    submission.status = 'received'
    db.session.commit()
    
    flash('Submission re-queued for processing')
    return redirect(url_for('dashboard.submission_detail', id=submission_id))
```

### 3.3 S3 Lifecycle Policy

Raw uploads transition through storage tiers to minimize cost:

```
┌─────────────────────────────────────────────────────────────┐
│ S3 Lifecycle Policy                                          │
├─────────────────────────────────────────────────────────────┤
│ Day 0-30:   S3 STANDARD (frequent access during ingestion)  │
│ Day 30-90:  S3 STANDARD-IA (infrequent access)              │
│ Day 90+:    S3 Glacier Deep Archive (long-term retention)   │
│ Day 365+:   Expiration (optional, per compliance)           │
└─────────────────────────────────────────────────────────────┘
```

```json
{
  "Rules": [
    {
      "ID": "IngestLifecycle",
      "Status": "Enabled",
      "Filter": {"Prefix": "raw/"},
      "Transitions": [
        {
          "Days": 30,
          "StorageClass": "STANDARD_IA"
        },
        {
          "Days": 90,
          "StorageClass": "GLACIER"
        }
      ],
      "Expiration": {
        "Days": 365
      }
    }
  ]
}
```

---

## 4. Cost Analysis

### 4.1 Current Cost (S3 STANDARD only)

| Item | Volume | Rate | Monthly Cost |
|------|--------|------|--------------|
| S3 STANDARD | 75 TB | $0.023/GB | **$1,725** |
| PUT requests | ~30,000/mo | $0.005/1k | $0.15 |
| GET requests | ~10,000/mo | $0.0004/1k | $0.004 |
| **Total** | | | **~$1,725/mo** |

### 4.2 Optimized Cost (Lifecycle Transitions)

| Item | Volume | Rate | Monthly Cost |
|------|--------|------|--------------|
| S3 STANDARD (0-30d) | 75 TB × 30/90 | $0.023/GB | $575 |
| S3 STANDARD-IA (30-90d) | 75 TB × 60/90 | $0.0125/GB | $625 |
| S3 Glacier (90d+) | negligible | $0.00099/GB | ~$75 |
| **Total** | | | **~$200/mo** |

**Savings: ~$1,525/mo (88% reduction)**

### 4.3 Alternative: Cloudflare R2

For downstream training pipeline (high egress):

| Item | Volume | Rate | Monthly Cost |
|------|--------|------|--------------|
| R2 Storage | 75 TB | $0.015/GB | $1,125 |
| Egress | Unlimited | $0 | $0 |
| Operations | ~40,000/mo | $4.50/million | $0.18 |
| **Total** | | | **~$1,125/mo** |

**Recommendation:** Use S3 with lifecycle for ingestion, replicate accepted clips to R2 for training pipeline to avoid egress fees.

---

## 5. Implementation Order

### Phase B-1: SQS Receiver + Lambda Lint Worker

**Scope:** Python Lambda that processes SQS messages and runs lint checks.

**Files:**
```
lambda/
├── handler.py          # Entry point (~50 LOC)
├── linter.py           # lint_buyer_spec implementation (~60 LOC)
├── s3_client.py        # S3 metadata extraction (~20 LOC)
└── requirements.txt
```

**Key Functions:**

```python
# lambda/handler.py
import json
import boto3
from linter import lint_buyer_spec
from s3_client import extract_metadata
from database import get_session, Submission, Event

sqs = boto3.client('sqs')
s3 = boto3.client('s3')

def handler(event, context):
    for record in event['Records']:
        body = json.loads(record['body'])
        s3_key = body['s3_key']
        bucket = body['bucket']
        
        # Extract metadata
        metadata = extract_metadata(bucket, s3_key)
        
        # Run lint
        lint_result = lint_buyer_spec(metadata)
        
        # Write to Postgres
        session = get_session()
        submission = Submission(
            vendor_id=body['vendor_id'],
            batch_id=body['batch_id'],
            clip_id=body['clip_id'],
            s3_key=s3_key,
            sha256=metadata['sha256'],
            size=metadata['size'],
            duration_sec=metadata['duration'],
            status='lint_pass' if lint_result['passed'] else 'lint_fail',
            lint_summary=lint_result
        )
        session.add(submission)
        session.commit()
```

**Estimated LOC:** ~150

---

### Phase B-2: Postgres Schema + DAO

**Scope:** SQLAlchemy models, migrations, and data access layer.

**Files:**
```
models/
├── __init__.py
├── submission.py       # Submission model + DAO (~80 LOC)
├── event.py            # Event model + DAO (~40 LOC)
├── payment.py          # Payment model + DAO (~40 LOC)
└── migrations/
    └── 001_initial.py  # Alembic migration (~40 LOC)
```

**DAO Example:**

```python
# models/submission.py
class SubmissionDAO:
    @staticmethod
    def get_by_batch(vendor_id, batch_id):
        return Submission.query.filter_by(
            vendor_id=vendor_id, 
            batch_id=batch_id
        ).all()
    
    @staticmethod
    def get_pending_review():
        return Submission.query.filter(
            Submission.status.in_(['lint_pass', 'lint_fail'])
        ).order_by(Submission.ingested_at.desc()).limit(100).all()
    
    @staticmethod
    def update_status(submission_id, new_status, user):
        submission = Submission.query.get(submission_id)
        submission.status = new_status
        event = Event(
            submission_id=submission_id,
            type='STATUS_CHANGED',
            message=f'Status changed to {new_status}',
            metadata={'user': user, 'old_status': submission.status}
        )
        db.session.add(event)
        db.session.commit()
        return submission
```

**Estimated LOC:** ~200

---

### Phase B-3: Web Dashboard (Flask + HTMX)

**Scope:** Admin dashboard for reviewing and managing submissions.

**Files:**
```
dashboard/
├── __init__.py
├── routes.py           # Flask routes (~80 LOC)
├── templates/
│   ├── base.html
│   ├── dashboard.html  # Main dashboard
│   ├── batch.html      # Batch detail view
│   └── submission.html # Submission detail
├── static/
│   └── style.css
└── auth.py             # OAuth2/SAML integration (~40 LOC)
```

**Key Routes:**

| Route | Method | Description |
|-------|--------|-------------|
| `/` | GET | Dashboard overview |
| `/batch/<vendor_id>/<batch_id>` | GET | Batch detail with all clips |
| `/submission/<id>` | GET | Single submission detail |
| `/submission/<id>/accept` | POST | Mark as accepted |
| `/submission/<id>/reject` | POST | Mark as rejected |
| `/submission/<id>/requeue` | POST | Re-queue for lint |
| `/payments` | GET | Payment summary by vendor |

**Estimated LOC:** ~300

---

### Phase B-4: Acceptance Feedback API → Vendor

**Scope:** Notify vendors of acceptance/rejection via email or webhook.

**Files:**
```
notification/
├── __init__.py
├── email.py            # SES email sender (~40 LOC)
├── webhook.py          # HTTP POST to vendor URL (~30 LOC)
└── templates/
    ├── accepted.html   # Email template
    └── rejected.html
```

**Notification Flow:**

```
┌─────────────────┐
│ Dashboard       │
│ (accept/reject) │
└────────┬────────┘
         │
         ▼
┌─────────────────┐     ┌─────────────────┐
│ Notification    │────►│ SES (email)     │
│ Service         │     └─────────────────┘
└────────┬────────┘
         │         ┌─────────────────┐
         └────────►│ Vendor Webhook  │
                   └─────────────────┘
```

**Email Template:**

```html
<!-- notification/templates/accepted.html -->
<h2>Clip Accepted</h2>
<p>Your clip has been accepted for the training dataset.</p>
<ul>
  <li>Clip ID: {{ clip_id }}</li>
  <li>Batch: {{ batch_id }}</li>
  <li>Accepted at: {{ accepted_at }}</li>
</ul>
<p>Payment will be processed at the end of the month.</p>
```

**Estimated LOC:** ~100

---

## 6. Deployment Checklist

- [ ] Create S3 bucket with PUT event notifications → SQS
- [ ] Provision SQS queue with DLQ
- [ ] Deploy Lambda function (or Cloud Run)
- [ ] Run Postgres migrations
- [ ] Deploy Flask dashboard with OAuth2
- [ ] Configure SES for vendor emails
- [ ] Set up S3 lifecycle policy
- [ ] Configure CloudWatch alarms for DLQ depth
- [ ] Load test with 1000 clips/day simulation

---

## 7. Monitoring & Alerting

| Metric | Threshold | Action |
|--------|-----------|--------|
| DLQ depth | > 10 messages | PagerDuty alert |
| Lambda errors | > 1% rate | CloudWatch alarm |
| Processing latency | > 5 min p99 | Investigate worker scaling |
| S3 storage | > 80 TB | Review lifecycle policy |
| Postgres connections | > 80% pool | Scale connection pooler |

---

## 8. Security Considerations

1. **S3 Access:** Vendor uses scoped IAM credentials with `s3:PutObject` only on their prefix
2. **Dashboard Auth:** OAuth2 with role-based access (Howard + ops team)
3. **API Keys:** Vendor webhook secrets stored in AWS Secrets Manager
4. **Encryption:** S3 SSE-S3, Postgres TDE, TLS in transit

---

*Document version: 1.0 | Last updated: 2024-01*