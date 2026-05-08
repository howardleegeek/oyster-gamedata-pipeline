# Storage Backend Abstraction

> **Owner:** Howard Li (`howard.li@berkeley.edu`)
> **Source:** `bin/storage_backend.py`, `bin/upload_tarball.py`
> **Tests:** `tests/test_storage_backend.py` (19 cases)
> **Iron-law:** No stub backends. Every backend roundtrips real bytes.

## Why this exists

Tester tarballs originally went straight to a single GitHub Release
(`real-data-sample-v1-20260507-0742`). GitHub caps each release asset at
**2 GiB** and each release at **50 assets**. Beyond ~100 testers/day we hit
both walls.

This module decouples the watcher daemon (`bin/auto_upload_watcher.sh`) from
any one storage provider. Switch backends with a single env var:

```bash
export STORAGE_BACKEND=s3   # or "github" (default), or "local"
```

## Architecture in one paragraph

`StorageBackend` is an abstract base class with four primitives — `upload`,
`list_assets`, `get_signed_url`, `delete`. Three production-grade
implementations ship today: `LocalFileStorageBackend` (filesystem, used in
tests and local dev), `S3StorageBackend` (boto3, works against AWS S3 /
Cloudflare R2 / Backblaze B2 / MinIO), and `GitHubReleaseStorageBackend`
(`gh` CLI). The CLI (`bin/upload_tarball.py`) accepts a tarball + metadata,
selects a backend by name, performs an idempotent upload, and emits one line
of JSON containing the canonical storage URL, a 24-hour signed download URL,
and the metadata block. Idempotency is enforced backend-side using sha256:
re-uploading the same tarball is a no-op.

## How upload flows now

```
swarm produces /tmp/swarm_real_*.tar.gz
              |
              v
auto_upload_watcher.sh    (poll loop, 60s, idempotent via SEEN_LIST)
              |
              v
auto_upload_real_sample.sh (D5 re-validate; reject non-REAL=6 / oversized)
              |
              v
bin/upload_tarball.py     (backend-agnostic CLI)
              |
              +--> github   (legacy default — 3 newest browsable on release page)
              +--> s3       (AWS S3 / R2 / B2 — preferred for scale)
              +--> local    (tests + local dev only)
```

## Per-backend env vars

### `STORAGE_BACKEND=github` (default — preserves legacy behaviour)

| Var | Required | Default | Notes |
|---|---|---|---|
| `STORAGE_GITHUB_REPO` | no | `howardleegeek/oyster-gamedata-pipeline` | `<owner>/<repo>` |
| `STORAGE_GITHUB_TAG` | no | `real-data-sample-v1-20260507-0742` | release tag |
| `STORAGE_GITHUB_KEEP_NEWEST` | no | `3` | rotate-buffer policy |

Auth: `gh auth login` once on the machine. The CLI inherits credentials.

**Constraints (enforced client-side):**
- 2 GiB hard cap per asset → backend raises `ValueError` and the shell
  wrapper exits 1 if exceeded. Caller should fall back to S3.
- Signed URL = public release-asset URL. Testers click it without auth.
- `oyster_REAL6_test.tar.gz` is permanently protected (the seed sample).

### `STORAGE_BACKEND=s3` (preferred for >100 testers/day)

| Var | Required | Default | Notes |
|---|---|---|---|
| `STORAGE_S3_BUCKET` | yes | — | e.g. `oyster-tester-tarballs` |
| `STORAGE_S3_REGION` | no | `us-east-1` | |
| `STORAGE_S3_ENDPOINT_URL` | no | — | Required for R2/B2/MinIO; omit for AWS |
| `AWS_ACCESS_KEY_ID` | yes | — | standard boto3 chain |
| `AWS_SECRET_ACCESS_KEY` | yes | — | standard boto3 chain |

**Provider matrix:**
| Provider | endpoint_url | Notes |
|---|---|---|
| AWS S3 | (omit) | standard |
| Cloudflare R2 | `https://<account>.r2.cloudflarestorage.com` | $0 egress |
| Backblaze B2 | `https://s3.<region>.backblazeb2.com` | cheap storage |
| MinIO | `http://minio:9000` | self-hosted |

**Bucket layout:**
```
s3://<bucket>/<tester-id>/<sha8>_<basename>.tar.gz
s3://<bucket>/<tester-id>/<sha8>_<basename>.tar.gz.metadata.json
```

The `.metadata.json` sidecar is what `list_assets` reads — avoids N HEAD calls.
Object metadata headers (`x-amz-meta-sha256`, `x-amz-meta-tester-id`, etc.)
are also set so consumers using `aws s3api head-object` see the same data.

**Signed URL TTL:** default 24h (`DEFAULT_SIGNED_URL_TTL_SECONDS`). Override
per-call via `--ttl-seconds` on the CLI or `ttl_seconds=` on the API.

### `STORAGE_BACKEND=local` (tests + local dev only — never production)

| Var | Required | Default | Notes |
|---|---|---|---|
| `STORAGE_LOCAL_ROOT` | no | `/tmp/oyster_storage` | |
| `STORAGE_LOCAL_BASE_URL` | no | `file://${STORAGE_LOCAL_ROOT}` | for synthesised URLs |

`get_signed_url` returns `file://.../asset#expires=<unix-ts>`. The fragment
lets tests assert TTL propagation; it has no enforcement effect.

## CLI usage

```bash
# Upload a tarball with explicit backend selection.
.venv/bin/python bin/upload_tarball.py \
    /tmp/swarm_real_X.tar.gz \
    --tester-id tester-001 \
    --d5-verdict REAL \
    --backend s3

# Output (single line, JSON):
# {"storage_url":"s3://oyster-tester-tarballs/tester-001/abcd1234_X.tar.gz",
#  "signed_url":"https://oyster-tester-tarballs.s3.amazonaws.com/...?X-Amz-...",
#  "asset_name":"tester-001/abcd1234_X.tar.gz",
#  "backend":"s3","idempotent_skip":false,
#  "metadata":{"tester_id":"tester-001","sha256":"abcd...","d5_verdict":"REAL",
#              "size_bytes":12345678,"uploaded_at":"2026-05-07T12:34:56+00:00",
#              "notes":""}}
```

Re-running with the same tarball returns `"idempotent_skip": true` and does
not transfer bytes.

## Migrating from `github` → `s3`

1. **Create the S3 bucket** (one-time):
   ```bash
   aws s3 mb s3://oyster-tester-tarballs --region us-east-1
   aws s3api put-bucket-versioning --bucket oyster-tester-tarballs \
       --versioning-configuration Status=Enabled
   ```

2. **Provision IAM credentials** with `s3:GetObject`, `s3:PutObject`,
   `s3:DeleteObject`, `s3:ListBucket` on `oyster-tester-tarballs/*`.

3. **Backfill (optional)** — copy the 3 newest tarballs from the GitHub
   release into S3 so existing tester URLs keep working:
   ```bash
   for asset in $(gh release view real-data-sample-v1-20260507-0742 \
       --repo howardleegeek/oyster-gamedata-pipeline --json assets \
       --jq '.assets[].name | select(endswith(".tar.gz"))'); do
     gh release download real-data-sample-v1-20260507-0742 \
       --repo howardleegeek/oyster-gamedata-pipeline -p "$asset" -D /tmp/migrate
     .venv/bin/python bin/upload_tarball.py /tmp/migrate/"$asset" \
       --tester-id "$(echo $asset | cut -d__ -f1)" \
       --d5-verdict REAL \
       --backend s3
     rm /tmp/migrate/"$asset"
   done
   ```

4. **Flip the watcher**:
   ```bash
   pkill -f auto_upload_watcher.sh
   STORAGE_BACKEND=s3 \
   STORAGE_S3_BUCKET=oyster-tester-tarballs \
   AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=... \
       nohup bash bin/auto_upload_watcher.sh > /tmp/auto_upload_watcher.log 2>&1 &
   ```

5. **Verify** with a fresh tarball:
   ```bash
   ls -la /tmp/swarm_real_*.tar.gz
   # wait one poll cycle (60s), then:
   tail /tmp/auto_upload_watcher.log
   aws s3 ls s3://oyster-tester-tarballs/ --recursive
   ```

The GitHub release stays around as a public read-only mirror of the 3
newest samples — set up a periodic copy from S3 if you want to keep it.

## Adding a new backend

1. Subclass `StorageBackend` and implement the four `@abstractmethod` methods.
2. Conform to the **idempotency contract**: `upload` must short-circuit when
   the same `metadata.sha256` is already present and return
   `UploadResult(idempotent_skip=True)` without transferring bytes.
3. Register your backend so the factory and CLI recognise it:
   ```python
   from bin.storage_backend import register_backend
   register_backend("gcs", GCSStorageBackend)
   ```
4. Add tests in `tests/test_storage_backend.py` mirroring the
   `LocalFileStorageBackend` round-trip pattern. **No stubs.** If your
   backend has no in-memory mock library, write a `FakeRunner`-style
   double that implements the upstream protocol the way `FakeGhRunner`
   does for `gh`.
5. Document env vars in this file.

## Public API surface

```python
from bin.storage_backend import (
    StorageBackend,                # ABC
    TarballMetadata,               # frozen dataclass — validates verdict + sha256
    UploadResult,                  # frozen dataclass — what upload() returns
    LocalFileStorageBackend,
    S3StorageBackend,
    GitHubReleaseStorageBackend,
    get_backend,                   # factory by name (or env STORAGE_BACKEND)
    register_backend,              # 3rd-party plugin
    compute_sha256,                # streaming hash, 1 MB chunks
    derive_asset_name,             # canonical "<tester>/<sha8>_<base>"
    DEFAULT_SIGNED_URL_TTL_SECONDS,
    GITHUB_ASSET_LIMIT_BYTES,
)
```

## Testing

```bash
.venv/bin/pytest tests/test_storage_backend.py -v
```

19 tests cover validation, round-trip, idempotency, signed-URL minting,
delete semantics, GitHub asset rotation, the factory, and the CLI. Total
runtime under 5s.
