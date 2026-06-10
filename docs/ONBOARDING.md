# ONBOARDING

Welcome to the Session Recording and Audit System. This document will guide you through setting up and using the system.

## Table of Contents
1. [Prerequisites](#prerequisites)
2. [Quick Start](#quick-start)
3. [Core Concepts](#core-concepts)
4. [Recording Sessions](#recording-sessions)
5. [Audit Pipeline](#audit-pipeline)
6. [Dashboard Usage](#dashboard-usage)
7. [Troubleshooting](#troubleshooting)

## Prerequisites

Before you begin, ensure you have:

- **Git** installed
- **Python 3.8+** with pip
- **Docker** and **Docker Compose** (for local development)
- Access to the project repository
- Required API keys and credentials

## Quick Start

### 1. Clone the repository
```bash
git clone https://github.com/your-org/session-recorder.git
cd session-recorder
```

### 2. Set up environment
```bash
cp .env.example .env
# Edit .env with your credentials
```

### 3. Install dependencies
```bash
pip install -r requirements.txt
```

### 4. Start services
```bash
docker-compose up -d
```

### 5. Run the recorder
```bash
python -m recorder.cli start --session-id my-first-session
```

## Core Concepts

### Session
A **session** represents a single recording instance with a unique identifier. Sessions capture user interactions, system events, and metadata.

### Canonical Pipeline
The **canonical pipeline** is the standard processing workflow that transforms raw session data into auditable records with full provenance.

### Audit
**Audit** refers to the process of reviewing and verifying session recordings for compliance and quality assurance.

### Provenance
**Provenance** tracks the complete lineage of data transformations, from raw recording to final audit report.

### Watchdog
The **watchdog** is a monitoring daemon that ensures system health and alerts on anomalies.

### Route Type
**route_type** defines the classification of user navigation paths within recorded sessions.

### Playback
**Playback** allows you to review recorded sessions with full fidelity, including screen capture, user interactions, and system events.

## Recording Sessions

### Starting a Recording
```bash
python -m recorder.cli start \
  --session-id "user-123-session" \
  --output-dir ./sessions \
  --metadata '{"user_id": 123, "environment": "production"}'
```

### Recording Configuration
Create a `recorder_config.yaml`:
```yaml
session:
  max_duration: 3600  # seconds
  compression: gzip
  encryption: true
  
capture:
  screen: true
  audio: false
  network: true
  system_events: true
  
storage:
  backend: s3
  bucket: session-recordings
  region: us-east-1
```

### Session Metadata
Each session includes:
- **session_id**: Unique identifier
- **start_time**: ISO 8601 timestamp
- **user_context**: User information
- **environment**: Production/staging/development
- **route_type**: Initial navigation classification

## Audit Pipeline

### Pipeline Stages
1. **Ingestion**: Raw session data intake
2. **Validation**: Data integrity checks
3. **Transformation**: Normalization and enrichment
4. **Analysis**: Pattern detection and anomaly scoring
5. **Reporting**: Audit report generation

### Running the Pipeline
```bash
python -m pipeline.process \
  --session-id "user-123-session" \
  --pipeline canonical \
  --output-format json
```

### Audit Reports
Audit reports include:
- Session completeness score
- Data integrity validation
- Anomaly detection results
- Provenance chain verification
- Compliance checklist

## Dashboard Usage

### Login
Access the dashboard at `http://localhost:8080`

1. Click **Log in** with your credentials
2. Authenticate via OAuth 2.0
3. Select your organization

### Session Management
- **View sessions**: Browse all recorded sessions
- **Filter by route_type**: Filter sessions by navigation type
- **Search**: Find sessions by ID or metadata
- **Export**: Download session data in various formats
- **Playback**: Review recorded sessions with full playback capability

### Audit Interface
- **Approve**: Mark session as compliant
- **Reject**: Flag session for review
- **Comment**: Add audit notes
- **Payout pending**: Sessions awaiting processing

### Watchdog Status
Monitor system health:
- **Active sessions**: Currently recording
- **Pipeline throughput**: Sessions processed per hour
- **Error rate**: Failed recordings percentage
- **Storage usage**: Disk/S3 utilization

## Troubleshooting

### Common Issues

#### "Cannot start recorder"
```bash
# Check if ports are available
netstat -an | grep 8080

# Verify Docker is running
docker ps
```

#### "Session data corrupted"
```bash
# Run data validation
python -m recorder.validate --session-id problematic-session

# Check storage backend
aws s3 ls s3://session-recordings/
```

#### "Dashboard login fails"
1. Clear browser cookies for localhost
2. Verify OAuth credentials in `.env`
3. Check backend service logs:
```bash
docker-compose logs auth-service
```

### Getting Help

- **Documentation**: See `/docs` for detailed guides
- **Issue tracker**: GitHub Issues for bug reports
- **Slack channel**: #session-recorder-support
- **Email**: support@session-recorder.example.com

## Next Steps

1. **Complete your first recording**: Try the quick start guide
2. **Review audit reports**: Understand the analysis output
3. **Use playback feature**: Review recorded sessions
4. **Customize the pipeline**: Modify for your use case
5. **Integrate with your systems**: API documentation available
6. **Join the community**: Contribute back to the project

---

*Last updated: 2024-01-15*
*Version: 2.1.0*