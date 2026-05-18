# Oyster Marketplace API

Buyer Integration Guide for programmatic access to the Oyster Marketplace.

## Overview

The Oyster Marketplace API enables AI labs (buyers) to programmatically browse, filter, download, and approve session data for training pipelines. This REST API provides:

- **Session Discovery**: Browse and filter sessions by quality metrics, data types, and more
- **Bulk Downloads**: Efficient batch downloads with idempotent job management
- **Webhook Integration**: Real-time event notifications for pipeline automation
- **Quality Assurance**: Access audit results and provenance verification

## Authentication

All API endpoints require JWT authentication via the OAuth 2.0 flow (see #24).

```bash
# Include JWT token in Authorization header
curl -H "Authorization: Bearer YOUR_JWT_TOKEN" \
  https://api.oyster.ai/api/v1/sessions
```

## Rate Limits

- **Limit**: 1000 requests per hour per buyer
- **Response**: HTTP 429 with `Retry-After` header when exceeded

```json
HTTP/1.1 429 Too Many Requests
Retry-After: 3600
Content-Type: application/json

{
  "detail": "Rate limit exceeded"
}
```

## OpenAPI Specification

Auto-generated OpenAPI 3.0 documentation is available at:

- **Swagger UI**: `https://api.oyster.ai/api/v1/docs`
- **ReDoc**: `https://api.oyster.ai/api/v1/redoc`
- **OpenAPI JSON**: `https://api.oyster.ai/api/v1/openapi.json`

## Endpoints

### List Sessions

```http
GET /api/v1/sessions
```

Paginated list of sessions with optional filters.

**Query Parameters**:

| Parameter | Type | Description |
|-----------|------|-------------|
| `page` | integer | Page number (default: 1) |
| `page_size` | integer | Items per page (default: 50, max: 200) |
| `game` | string | Filter by game name |
| `scene` | string | Filter by scene name |
| `route_type` | string | Filter by route type (driving, walking, flying) |
| `audit_score_min` | integer | Minimum audit score (0-150) |
| `quality_score_min` | integer | Minimum quality score (0-100) |
| `has_depth` | boolean | Has depth data |
| `has_audio` | boolean | Has audio data |
| `has_voice` | boolean | Has voice annotations |
| `has_zbuffer` | boolean | Has z-buffer data |

**Response**:

```json
{
  "sessions": [
    {
      "id": "sess_abc123",
      "game": "cyberpunk_2077",
      "scene": "night_city_downtown",
      "route_type": "driving",
      "audit_score": 105,
      "quality_score": 85,
      "has_depth": true,
      "has_audio": true,
      "has_voice": false,
      "has_zbuffer": true,
      "created_at": "2026-05-17T10:30:00Z",
      "status": "available"
    }
  ],
  "total": 1523,
  "page": 1,
  "page_size": 50,
  "has_more": true
}
```

### Get Session

```http
GET /api/v1/sessions/{session_id}
```

Get single session metadata with signed download URLs.

**Response**:

```json
{
  "id": "sess_abc123",
  "game": "cyberpunk_2077",
  "scene": "night_city_downtown",
  "route_type": "driving",
  "audit_score": 105,
  "quality_score": 85,
  "has_depth": true,
  "has_audio": true,
  "has_voice": false,
  "has_zbuffer": true,
  "created_at": "2026-05-17T10:30:00Z",
  "status": "available",
  "download_urls": {
    "rgb": "https://storage.oyster.ai/sessions/sess_abc123/rgb?expires=...&sig=...",
    "depth": "https://storage.oyster.ai/sessions/sess_abc123/depth?expires=...&sig=...",
    "audio": "https://storage.oyster.ai/sessions/sess_abc123/audio?expires=...&sig=...",
    "metadata": "https://storage.oyster.ai/sessions/sess_abc123/metadata?expires=...&sig=..."
  }
}
```

### Get Session Audit

```http
GET /api/v1/sessions/{session_id}/audit
```

Get full audit results for a session.

**Response**:

```json
{
  "session_id": "sess_abc123",
  "audit_score": 105,
  "checks": {
    "frame_consistency": {"passed": true, "score": 98},
    "depth_quality": {"passed": true, "score": 95},
    "audio_sync": {"passed": true, "score": 100},
    "motion_blur": {"passed": true, "score": 92}
  },
  "passed": true,
  "timestamp": "2026-05-17T11:00:00Z"
}
```

### Verify Session Provenance

```http
GET /api/v1/sessions/{session_id}/verify
```

Verify session provenance and data lineage.

**Response**:

```json
{
  "session_id": "sess_abc123",
  "verified": true,
  "provenance_chain": [
    {"step": "capture", "timestamp": "2026-05-17T10:30:00Z", "node": "node_abc123"},
    {"step": "upload", "timestamp": "2026-05-17T10:45:00Z", "node": "gateway_eu_west"},
    {"step": "audit", "timestamp": "2026-05-17T11:00:00Z", "node": "audit_worker_01"},
    {"step": "store", "timestamp": "2026-05-17T11:05:00Z", "node": "storage_cluster_01"}
  ],
  "timestamp": "2026-05-17T12:00:00Z"
}
```

### Bulk Download

```http
POST /api/v1/sessions/bulk-download
```

Initiate a bulk download job. **Idempotent**: same filter + time-window returns same job_id within 24h.

**Request**:

```json
{
  "filters": {
    "audit_score_min": 100,
    "has_depth": true,
    "quality_score_min": 80
  },
  "since": "2026-05-17"
}
```

**Response**:

```json
{
  "job_id": "a1b2c3d4e5f6",
  "status": "pending",
  "created_at": "2026-05-18T14:00:00Z",
  "total_sessions": 1523,
  "download_url": null,
  "expires_at": "2026-05-19T14:00:00Z"
}
```

### Poll Bulk Download Status

```http
GET /api/v1/bulk-download/{job_id}
```

Check bulk download job status.

**Response (completed)**:

```json
{
  "job_id": "a1b2c3d4e5f6",
  "status": "completed",
  "created_at": "2026-05-18T14:00:00Z",
  "total_sessions": 1523,
  "download_url": "https://storage.oyster.ai/bulk/a1b2c3d4e5f6/download.tar.gz?expires=...",
  "expires_at": "2026-05-19T14:00:00Z"
}
```

### Approve Session

```http
POST /api/v1/sessions/{session_id}/approve
```

Approve a session, triggering contributor payout.

**Request**:

```json
{
  "notes": "High quality depth data, suitable for training"
}
```

**Response**:

```json
{
  "status": "approved",
  "session_id": "sess_abc123"
}
```

### Reject Session

```http
POST /api/v1/sessions/{session_id}/reject
```

Reject a session with reason.

**Request**:

```json
{
  "reason": "quality_issues",
  "notes": "Motion blur exceeds acceptable threshold"
}
```

**Response**:

```json
{
  "status": "rejected",
  "session_id": "sess_abc123",
  "reason": "quality_issues"
}
```

## Webhooks

### Register Webhook

```http
POST /api/v1/webhooks
```

Register a webhook URL for event notifications.

**Request**:

```json
{
  "url": "https://your-lab.com/webhooks/oyster",
  "events": ["session.created", "session.approved", "payout.completed"],
  "secret": "your_webhook_secret_here"
}
```

**Response**:

```json
{
  "id": "wh_xyz789",
  "url": "https://your-lab.com/webhooks/oyster",
  "events": ["session.created", "session.approved", "payout.completed"],
  "created_at": "2026-05-18T14:00:00Z"
}
```

### List Webhooks

```http
GET /api/v1/webhooks
```

List all registered webhooks for your account.

**Response**:

```json
[
  {
    "id": "wh_xyz789",
    "url": "https://your-lab.com/webhooks/oyster",
    "events": ["session.created", "session.approved"],
    "created_at": "2026-05-18T14:00:00Z"
  }
]
```

### Delete Webhook

```http
DELETE /api/v1/webhooks/{webhook_id}
```

Unregister a webhook.

## Webhook Events

All webhook POSTs include an `X-Oyster-Signature` header containing an HMAC-SHA256 signature of the payload body, using your registered secret.

### Verifying Webhook Signatures

```python
import hmac
import hashlib

def verify_signature(secret: str, payload: str, signature: str) -> bool:
    """Verify webhook HMAC signature."""
    expected = hmac.new(
        secret.encode('utf-8'),
        payload.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)

# In your webhook handler
signature = request.headers.get('X-Oyster-Signature')
if not verify_signature(WEBHOOK_SECRET, request.body, signature):
    return Response(status=401)
```

### Event Types

#### session.created

Fired when a new finalized session is available.

```json
{
  "event_type": "session.created",
  "timestamp": "2026-05-18T14:30:00Z",
  "data": {
    "session_id": "sess_abc123",
    "game": "cyberpunk_2077",
    "scene": "night_city_downtown",
    "audit_score": 105,
    "quality_score": 85
  },
  "attempt": 1
}
```

#### session.audit_passed

Fired when a session passes audit.

```json
{
  "event_type": "session.audit_passed",
  "timestamp": "2026-05-18T14:35:00Z",
  "data": {
    "session_id": "sess_abc123",
    "audit_score": 105,
    "checks_passed": 4,
    "checks_total": 4
  },
  "attempt": 1
}
```

#### session.approved

Fired when a buyer approves a session.

```json
{
  "event_type": "session.approved",
  "timestamp": "2026-05-18T15:00:00Z",
  "data": {
    "session_id": "sess_abc123",
    "buyer_id": "buyer_xyz",
    "approved_at": "2026-05-18T15:00:00Z",
    "notes": "High quality depth data"
  },
  "attempt": 1
}
```

#### payout.completed

Fired when a contributor is paid.

```json
{
  "event_type": "payout.completed",
  "timestamp": "2026-05-18T15:05:00Z",
  "data": {
    "session_id": "sess_abc123",
    "contributor_id": "contrib_456",
    "amount": "12.50",
    "currency": "USD",
    "transaction_id": "tx_789xyz"
  },
  "attempt": 1
}
```

### Webhook Retry Policy

- **Retries**: Up to 5 attempts for 5xx errors
- **Backoff**: Exponential (1s, 2s, 4s, 8s, 16s, ...)
- **Max Age**: 24 hours
- **Dead Letter**: Failed deliveries logged for debugging

## Filter Syntax

The CLI tool supports a simple filter syntax:

```
audit_score>=100 and has_depth and quality_score>=80
```

**Supported operators**:
- `>=` (greater than or equal)
- `<=` (less than or equal)
- `>` (greater than)
- `<` (less than)
- `=` (equal)
- `!=` (not equal)

**Supported fields**:
- `audit_score` - Audit score (0-150)
- `quality_score` - Quality score (0-100)
- `has_depth` - Has depth data (boolean)
- `has_audio` - Has audio data (boolean)
- `has_voice` - Has voice annotations (boolean)
- `has_zbuffer` - Has z-buffer data (boolean)
- `game` - Game name (string)
- `scene` - Scene name (string)
- `route_type` - Route type (string)

## CLI Tool

Install the CLI tool for easy integration:

```bash
# List sessions
oyster-marketplace list --filter "audit_score>=100 and has_depth"

# Sync sessions to local directory
oyster-marketplace sync \
  --filter "audit_score>=101 and has_depth and quality_score>=80" \
  --since 2026-05-17 \
  --output ./oyster-data/

# Create bulk download job
oyster-marketplace bulk \
  --filter "has_depth and has_audio" \
  --wait \
  --output ./downloads/
```

## SDK Examples

### Python

```python
import requests

class OysterClient:
    def __init__(self, api_key, base_url="https://api.oyster.ai"):
        self.base_url = base_url
        self.session = requests.Session()
        self.session.headers["Authorization"] = f"Bearer {api_key}"
    
    def list_sessions(self, **filters):
        return self.session.get(f"{self.base_url}/api/v1/sessions", params=filters).json()
    
    def get_session(self, session_id):
        return self.session.get(f"{self.base_url}/api/v1/sessions/{session_id}").json()
    
    def approve_session(self, session_id, notes=None):
        return self.session.post(
            f"{self.base_url}/api/v1/sessions/{session_id}/approve",
            json={"notes": notes}
        ).json()

# Usage
client = OysterClient("your_api_key")
sessions = client.list_sessions(audit_score_min=100, has_depth=True)
for session in sessions["sessions"]:
    print(f"Session {session['id']}: audit={session['audit_score']}")
```

### JavaScript/TypeScript

```typescript
class OysterClient {
  constructor(private apiKey: string, private baseUrl = 'https://api.oyster.ai') {}
  
  private async request(path: string, options: RequestInit = {}): Promise<any> {
    const response = await fetch(`${this.baseUrl}${path}`, {
      ...options,
      headers: {
        'Authorization': `Bearer ${this.apiKey}`,
        'Content-Type': 'application/json',
        ...options.headers,
      },
    });
    return response.json();
  }
  
  async listSessions(filters: Record<string, any> = {}): Promise<any> {
    const params = new URLSearchParams(filters as any);
    return this.request(`/api/v1/sessions?${params}`);
  }
  
  async getSession(sessionId: string): Promise<any> {
    return this.request(`/api/v1/sessions/${sessionId}`);
  }
  
  async approveSession(sessionId: string, notes?: string): Promise<any> {
    return this.request(`/api/v1/sessions/${sessionId}/approve`, {
      method: 'POST',
      body: JSON.stringify({ notes }),
    });
  }
}

// Usage
const client = new OysterClient('your_api_key');
const sessions = await client.listSessions({ audit_score_min: 100, has_depth: true });
```

## Support

- **Documentation**: https://docs.oyster.ai/marketplace
- **API Status**: https://status.oyster.ai
- **Support Email**: api-support@oyster.ai