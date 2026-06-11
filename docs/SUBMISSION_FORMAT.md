# Submission Format · 提交格式规范

> **配套文档**: [`PRD.md`](PRD.md) · [`BUYER_SPEC_V1.md`](BUYER_SPEC_V1.md) · [`VENDOR_ONBOARDING.md`](VENDOR_ONBOARDING.md)
> **版本**: v1.0 · **生效日期**: 2026-05-02
> **强制级**: 所有 vendor 提交必须严格遵守,违反格式直接拒收

---

## 1. tarball 命名规范

### 1.1 命名格式
```
<vendor_id>_<batch_id>_<clip_id>_v<spec_version>.tar.gz
```

| 字段 | 格式 | 示例 |
|---|---|---|
| `vendor_id` | `vendor-NNN`(三位数字) | `vendor-001` |
| `batch_id` | `batch-YYYY-MM-X`(年月+批次字母) | `batch-2026-05-A` |
| `clip_id` | `clip-NNNNN`(五位数字) | `clip-00042` |
| `spec_version` | `v1` / `v2` ...(本 PRD 是 v1) | `v1` |

### 1.2 完整示例
```
vendor-001_batch-2026-05-A_clip-00042_v1.tar.gz
vendor-001_batch-2026-05-A_clip-00043_v1.tar.gz
vendor-001_batch-2026-05-A_clip-00044_v1.tar.gz
...
vendor-002_batch-2026-05-A_clip-00001_v1.tar.gz
```

### 1.3 命名规则
- ✅ 全小写字母 + 数字 + 短横 `-` + 下划线 `_`
- ❌ 禁止空格 / 中文 / 大写字母 / 特殊字符 / 表情符号
- ❌ 禁止文件名超过 100 字符
- ✅ 同 vendor 同 batch 内 clip_id 必须连号(00001, 00002, ...)
- ✅ 不同 batch 间 clip_id 重置(每 batch 从 00001 开始)

### 1.4 错误示例
```
❌ Vendor001_batch1_clip42_v1.tar.gz       # 大小写混乱
❌ vendor-1_batch-A_clip-42.tar.gz         # 缺少 spec_version
❌ vendor-001_batch-A_clip-42_v1.tgz       # 后缀必须是 .tar.gz
❌ vendor-001 batch-2026-05 clip-42.tar    # 空格 + 缺压缩
❌ 录制1.tar.gz                            # 中文
```

---

## 2. tarball 内部结构

### 2.1 标准目录布局
```
<clip_id>/                                # 顶层目录,与 clip_id 一致
├── video.mp4                             # 必需
├── action_camera.json                    # 必需
├── gameinfo.xlsx                         # 必需
└── depth/                                # 必需(目录)
    ├── 000000.exr
    ├── 000001.exr
    ├── ...
    └── 001799.exr                        # 5min × 6fps = 1800 帧
```

### 2.2 验证内部结构
```bash
tar -tzf vendor-001_batch-2026-05-A_clip-00042_v1.tar.gz | head -10
# 期望输出:
# clip-00042/
# clip-00042/video.mp4
# clip-00042/action_camera.json
# clip-00042/gameinfo.xlsx
# clip-00042/depth/
# clip-00042/depth/000000.exr
# clip-00042/depth/000001.exr
# ...
```

### 2.3 禁止
- ❌ 无顶层目录(直接 `video.mp4` 在 tarball 根)
- ❌ 顶层目录名与 `clip_id` 不一致
- ❌ 多余文件(README.md / .DS_Store / Thumbs.db / *.log)
- ❌ 嵌套压缩(tar 内再 tar)
- ❌ 软链接 / 硬链接

---

## 3. 上传方式

### 3.0 Tester portal: direct-to-Supabase signed URL (recorder v0.27.0+) {#direct-to-supabase-upload}

> 适用于 **tester portal 通过 OysterRecorder.exe 上传 Minecraft 单段录像**(每段
> 500 MB–1.5 GB)。Vendor 批量上传仍走 §3.1–§3.4 的 S3/SFTP/OSS 通道。
>
> Why this flow exists (Gap #8): Vercel route handlers 限制 request body ≤ 4.5 MB。
> 任何真实游戏录像通过 POST `/api/upload-tarball` 都会 413。我们把上传拆成三步,
> 二进制走 recorder → Supabase Storage,完全绕过 Vercel。

**Protocol (three calls, all idempotent):**

1. `POST /api/upload-tarball/sign` — JSON body
   ```json
   {
     "tester_id":        "<uuid>",
     "filename":         "<safe>.tar.gz",
     "size_bytes":       12345,
     "sha256":           "<64 lowercase hex>",
     "duration_seconds": 1800
   }
   ```
   Header: `X-Tester-Auth: v1 <tester_id> <ts_ms> <hex_sha256_hmac>` (Gap #6).
   Returns `{ tarball_id, signed_url, storage_bucket, storage_path, expires_at, ttl_seconds: 900 }`.
   The signed URL is good for **15 min** — enough to start the upload, short enough
   to limit blast radius if it leaks.

2. `PUT <signed_url>` — `Content-Type: application/gzip`, header `x-upsert: true`,
   body = raw tarball bytes. **Recorder uploads directly to Supabase, Vercel sees nothing.**

3. `POST /api/upload-tarball/finalize` — JSON body `{ tarball_id, sha256 }`.
   Header: same `X-Tester-Auth`. Server HEADs the storage object, verifies size
   matches what step 1 declared, flips `upload_status` from `pending_upload` to
   `uploaded`. Returns the canonical tarball row.

**Reference client:** `bin/upload_tarball_signed.py`
```bash
TESTER_AUTH_HMAC_SECRET=<from onboarding email> \
  bin/upload_tarball_signed.py /path/to/clip.tar.gz \
    --tester-id <uuid> \
    --duration-seconds 1800 \
    --base-url https://tester.oysterworld.dev
```

**Idempotency:**
- Repeating sign with the same sha256 returns the existing `tarball_id` (no
  duplicate rows). If the previous upload completed, the response carries
  `already_uploaded: true` and the recorder skips step 2.
- Repeating finalize on an already-finalized row returns the canonical row
  with `duplicate: true`.

**Error semantics:**
- `400` — malformed body / bad sha256 shape.
- `401` — missing or invalid `X-Tester-Auth` (Gap #6).
- `403` — HMAC `tester_id` doesn't match body `tester_id`.
- `404` (finalize) — `tarball_id` unknown.
- `409` (sign) — sha256 already owned by a different tester (replay attack).
- `409` (finalize) — storage object missing, size mismatch, or row marked
  `failed` from a previous attempt.
- `410` — caller hit the legacy `/api/upload-tarball` endpoint; response body
  spells out the new three-call migration.
- `413` — declared `size_bytes` > 1 GiB.
- `422` — `sha256` in finalize body doesn't match the sha256 reserved at sign time.
- `429` — rate limit (30/min/IP, 60/hour/tester for sign; cheaper PUT is the
  Supabase bucket's responsibility).
- `503` — Supabase env vars missing on server.

**Env vars (server side):**
- `NEXT_PUBLIC_SUPABASE_URL` (or alias `SUPABASE_URL`)
- `NEXT_PUBLIC_SUPABASE_ANON_KEY`
- `SUPABASE_SERVICE_ROLE_KEY` (or alias `SUPABASE_SERVICE_KEY`)
- `SUPABASE_TARBALL_UPLOAD_BUCKET` (or alias `SUPABASE_BUCKET`, default `tarball-uploads`)
- `SUPABASE_SIGNED_UPLOAD_URL_TTL_SECONDS` (default `900`)
- `TESTER_AUTH_HMAC_SECRET` — when set, gap #6 HMAC verification is enforced.
  When unset, the server logs a warning per request and accepts unsigned calls
  (stub_mode mode — only safe in dev/preview until gap #6 ships).

**Abandoned uploads:** if a recorder gets a signed URL and crashes before
PUTting or finalizing, the `tarballs` row sticks at `upload_status =
'pending_upload'` with `signed_url_expires_at` in the past. A follow-up reaper
(out of scope here — see `bin/storage_reaper.py` TODO) should sweep these and
either retry or delete.

---

### 3.1 默认方式: S3 (推荐,海外 vendor)

#### 3.1.1 我方提供
SOW 签约后,我方通过加密邮件发送:
- AWS Access Key ID
- AWS Secret Access Key
- S3 bucket name
- AWS region

#### 3.1.2 上传命令
```bash
# 装 awscli
pip install awscli

# 配置
aws configure
# AWS Access Key ID: <我方给的>
# AWS Secret Access Key: <我方给的>
# Default region: <我方给的>
# Default output format: json

# 上传单个
aws s3 cp vendor-001_batch-2026-05-A_clip-00042_v1.tar.gz \
  s3://oysterworld-gamedata-vendor-uploads/vendor-001/batch-2026-05-A/

# 批量上传整个 batch
aws s3 sync ./out/batch-2026-05-A/ \
  s3://oysterworld-gamedata-vendor-uploads/vendor-001/batch-2026-05-A/ \
  --exclude "*" --include "*.tar.gz"
```

#### 3.1.3 断点续传
```bash
# multipart + resume 自动开启,不用配
aws s3 cp big_file.tar.gz s3://... --no-progress
```

### 3.2 备选方式: SFTP (国内/网络受限 vendor)

#### 3.2.1 我方提供
- SFTP 主机: `upload.oysterworld.dev`
- 端口: `22`
- 用户名: `vendor-001`(对应 vendor_id)
- 密码: 我方加密邮件发送

#### 3.2.2 上传命令
```bash
# scp 单个
scp vendor-001_batch-2026-05-A_clip-00042_v1.tar.gz \
    vendor-001@upload.oysterworld.dev:/uploads/batch-2026-05-A/

# rsync 批量(支持断点续传)
rsync -avz --progress \
  ./out/batch-2026-05-A/*.tar.gz \
  vendor-001@upload.oysterworld.dev:/uploads/batch-2026-05-A/

# sftp 交互
sftp vendor-001@upload.oysterworld.dev
sftp> cd /uploads/batch-2026-05-A
sftp> put vendor-001_batch-2026-05-A_clip-00042_v1.tar.gz
```

### 3.3 备选方式 2: 阿里云 OSS (国内 vendor)

#### 3.3.1 我方提供
- 阿里云 AccessKey ID + Secret
- OSS bucket: `oss-cn-hangzhou://oysterworld-gamedata`

#### 3.3.2 上传命令
```bash
# 装 ossutil
brew install aliyun-cli && ossutil --version

# 配置
ossutil config

# 上传
ossutil cp vendor-001_batch-2026-05-A_clip-00042_v1.tar.gz \
  oss://oysterworld-gamedata/vendor-001/batch-2026-05-A/

# 批量
ossutil cp -r ./out/batch-2026-05-A/ \
  oss://oysterworld-gamedata/vendor-001/batch-2026-05-A/ \
  --include "*.tar.gz"
```

### 3.4 应急方式: 大文件分享(小 vendor < 50 clip / 月)

可接受:
- 阿里云盘 / 百度网盘(需付会员保速度)
- WeTransfer / Send Anywhere
- 微信文件传输助手 + 手动整理

**要求**: 提交前必须先发邮件 `vendor-XXX 准备上传 N 个 clip 共 X GB,需要分享链接`,我方回复后再传。

---

## 4. 提交清单(每批必交)

每个 batch 上传完毕后,**必须**同时上传一份 manifest.yaml:

### 4.1 manifest 格式
```yaml
# manifest.yaml (放在 batch 根目录)
batch_id: vendor-001_batch-2026-05-A
vendor_id: vendor-001
spec_version: v1
upload_date: 2026-05-09T15:30:00+08:00

total_clips: 200
total_size_gb: 187.5

# 每个操作员的产出
operators:
  - operator_id: vendor-001-op-A
    clip_count: 80
    routes: {normal: 40, special: 20, loop: 20}
  - operator_id: vendor-001-op-B
    clip_count: 70
    routes: {normal: 35, special: 18, loop: 17}
  - operator_id: vendor-001-op-C
    clip_count: 50
    routes: {normal: 25, special: 13, loop: 12}

# 录制元信息
game: Minecraft
game_version: 1.20.4
platform: Java Edition
scene_pool:
  - flat-overworld
  - forest-biome
  - desert-biome
  - mountain-biome

# clip 列表(必须 sorted by clip_id)
clips:
  - clip_id: clip-00001
    operator: vendor-001-op-A
    duration_sec: 302.4
    sha256: a3f5b2...c9
    size_bytes: 943718400
    route_type: 1
    scene: flat-overworld
  - clip_id: clip-00002
    # ...
  # ... (200 entries)

notes: |
  - 第一批 200 clip
  - 包含 day-clear weather (180) + sunset (20)
  - 全部 1080p 30fps H.264 (CRF 18)

# manifest 自身校验
manifest_sha256: <auto-generated, 见 §4.3>
```

### 4.2 自动生成 manifest
我方提供生成脚本:
```bash
python3 bin/generate_manifest.py \
  --batch-dir ./out/batch-2026-05-A/ \
  --vendor-id vendor-001 \
  --batch-id vendor-001_batch-2026-05-A \
  --output ./out/batch-2026-05-A/manifest.yaml
```

### 4.3 manifest 校验
manifest 必须通过我方 lint:
```bash
oyster-buyer-lint --manifest ./out/batch-2026-05-A/manifest.yaml
```

---

## 5. 自助验收(提交前必跑)

### 5.1 单 clip lint
```bash
oyster-buyer-lint vendor-001_batch-2026-05-A_clip-00042_v1.tar.gz

# 输出 PASS 才能提交
# 任何 FAIL 自行修复重打包
```

### 5.2 整批 lint
```bash
# 跑整个 batch 目录
oyster-buyer-lint --batch ./out/batch-2026-05-A/

# 输出汇总:
# Batch: vendor-001_batch-2026-05-A
# Total: 200 clips
# PASS: 198 (99.0%)
# FAIL: 2 (1.0%)
#   - clip-00067: video duration 4m 53s (need ≥ 5 min)
#   - clip-00121: depth/*.exr count 1500 (expect 1800)
```

### 5.3 修复 FAIL clip
- 重录: 推荐(简单可靠)
- 重打包: 仅当 metadata 错(如 gameinfo.xlsx 错填),不能修视频

**禁止**:
- ❌ 拼接 / 剪辑视频凑够 5 分钟
- ❌ 修改 action_camera.json 时间戳
- ❌ 用其他 clip 的 depth 替补

---

## 6. 提交后流程

### 6.1 上传完成通知
**必须**在上传完毕后 24 小时内发邮件给 `howard.linra@gmail.com`,主题:

```
[Vendor-001] Batch 2026-05-A 上传完成 - 200 clips - 187.5 GB
```

正文:
```
Batch ID: vendor-001_batch-2026-05-A
Total clips: 200
Total size: 187.5 GB
Upload method: S3
Upload date: 2026-05-09T15:30 +08:00
Manifest URL: s3://...../manifest.yaml
Self-lint: 200/200 PASS
```

### 6.2 我方处理时间表
| 步骤 | 时长 | 我方动作 |
|---|---|---|
| 接收确认 | ≤ 4 小时 | 邮件回复 "received,manifest 校验通过" |
| 全量 lint | ≤ 24 小时 | 跑 `oyster-buyer-lint --batch` |
| 抽样人工 review | ≤ 48 小时 | 抽 5 % 看真画面 / 真行为 |
| 验收报告 | ≤ 72 小时 | 发邮件 "通过 195/200,5 个需重做" |
| 结算 | ≤ 7 天 | 通过 clip 按单价 × 数量结算 |

### 6.3 重做规则
- 我方拒收的 clip 不计入计费
- 同 batch 重做率 ≤ 10 %: 视为正常,正常结算其余
- 同 batch 重做率 > 10 %: 整批暂停,共同分析原因
- 同 batch 重做率 > 30 %: 整批拒收,重做整批

---

## 7. 常见 lint 错误 + 修复

### 7.1 STRUCTURE
```
[1/8] FAIL: tarball missing 'depth/' directory
```
**原因**: 打包时漏了 depth 目录
**修复**: 重打包,确保 4 件套齐全

### 7.2 VIDEO_DURATION
```
[3/8] FAIL: video duration 4m 53s (expected 5-6 min)
```
**原因**: 录制提前结束 / 客户端断线
**修复**: 重录,bot 加 `--duration 360`(留 60s buffer)

### 7.3 SCHEMA
```
[4/8] FAIL: action_camera.json missing field 'metric_scale'
```
**原因**: 老版本 adapter 输出
**修复**: 升级 `git pull` + 重跑 adapter

### 7.4 CONTINUITY
```
[5/8] FAIL: action_camera.json frame skip detected (frame 5023 → 5025)
```
**原因**: 录制中卡顿,丢帧
**修复**: 重录,关闭其他高 CPU 进程

### 7.5 GAMEINFO
```
[6/8] FAIL: gameinfo.xlsx missing 'operator_id'
```
**原因**: 模板未完整填写
**修复**: 编辑 xlsx 补 operator_id 重打包(不必重录)

### 7.6 EXR
```
[7/8] FAIL: depth/000123.exr is 4 bytes (stub_mode, not real depth)
```
**原因**: 跑了 stub_mode e2e,没装 PyTorch / DepthAnything
**修复**: 完成 [STEP 5/8](VENDOR_ONBOARDING.md#step-5--装-obs-studio--depthanything-v2真画面真深度) 装真推理

### 7.7 EXR_COUNT
```
[7/8] FAIL: depth/ has 1500 EXRs (expected 1800 for 5 min × 6 fps)
```
**原因**: 视频不足 5 分钟 / 深度采样间隔错
**修复**: 检查 `bin/extract_frames.py --fps 6`(必须 6,不是 5/10)

### 7.8 ALIGN
```
[8/8] FAIL: timestamp drift > 100ms between video and action_camera
```
**原因**: video 与 telemetry 不同步
**修复**: 重录,确保 OBS 与 mineflayer 同时启动(用 produce_real_sample_v2.sh orchestrator)

---

## 8. 模板与示例

### 8.1 成功 batch 示例(我方提供)
```bash
# 下载示例 batch(供 vendor 参考结构)
aws s3 sync \
  s3://oysterworld-gamedata-public/sample-batch/ \
  ./sample-batch/ --no-sign-request

ls sample-batch/
# vendor-000_batch-sample_clip-00001_v1.tar.gz
# vendor-000_batch-sample_clip-00002_v1.tar.gz
# manifest.yaml
```

### 8.2 manifest 模板
我方在仓库 `templates/manifest_template.yaml` 提供空白模板,vendor 填写后用 `bin/generate_manifest.py --validate` 校验。

### 8.3 邮件模板
```
To: howard.linra@gmail.com
Subject: [Vendor-001] Batch 2026-05-A 上传完成 - 200 clips

Howard,

Batch upload complete:
- Batch ID: vendor-001_batch-2026-05-A
- Total clips: 200
- Total size: 187.5 GB
- Upload method: S3
- Upload date: 2026-05-09T15:30 +08:00
- Manifest URL: s3://oysterworld-gamedata-vendor-uploads/vendor-001/batch-2026-05-A/manifest.yaml
- Self-lint: 200/200 PASS
- Notes: 第一批 200 clip, 包含 day-clear (180) + sunset (20).

Awaiting your validation report.

Thanks,
Vendor-001 (你的名字)
```

---

## 附录 A: 字段速查
- `vendor_id` 是签约时分配的,不能自选
- `batch_id` 由 vendor 自己选,但要按 `batch-YYYY-MM-X` 格式
- `clip_id` 必须连号,中间不能跳(reject 的 clip 重做后可以**复用同一编号**)
- `spec_version` 当前固定 `v1`,我方升级 spec 时会同步通知

## 附录 B: 工具速查
```bash
# 验单 clip
oyster-buyer-lint <tarball>

# 验整 batch
oyster-buyer-lint --batch <dir>

# 验 manifest
oyster-buyer-lint --manifest <path>

# 生成 manifest
python3 bin/generate_manifest.py --batch-dir <dir> ...

# 计算 sha256
shasum -a 256 <tarball>     # macOS
sha256sum <tarball>          # Linux
Get-FileHash <tarball>       # Windows
```

## 附录 C: 联络
- **技术问题**: howard.linra@gmail.com (Howard Li)
- **紧急 production stop**: WhatsApp +1 (341) 250-6526 (≤ 4 小时)
- **结算 / 商务**: howard.linra@gmail.com (主题加 `[BILLING]`)
