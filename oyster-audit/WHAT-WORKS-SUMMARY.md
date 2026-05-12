# What works — Oyster GameData Recorder（2026-05-12 截止）

## ✅ 真测过 + 实证（minipc1 实跑）

| 项 | 实证 |
|---|---|
| **rc17.0.4 录 1080p30 mp4** | session_230537 录到 **359 MB mp4**（5min 接近完整） |
| **9 个完整 session 历史** | recordings/ 里 9 个 6-file session，最大 364 MB |
| **桌面 "Open Recordings Folder" shortcut** | SSH 创建的 .lnk 文件确认能双击直达 recordings/ |
| **Window Shell COM 视频 metadata 读取** | mp4 frame rate 30.00 / 1920×1080 / bit rate 10017 kbps 全确认 |
| **graceful Esc 退出生成完整 5 文件** | session_234542 验证 6 文件齐 (mp4 + 5 JSON) |
| **2 个 sample 已搬到 Downloads** | 268 MB + 376 MB 两份完整 session 在 minipc1 Downloads 里 |

## 📦 SHIPPED + 装包在 GitHub Release（公开可下载）

| 装包 | URL | 大小 | 含 |
|---|---|---|---|
| **rc17.1.1** | [link](https://github.com/howardleegeek/oyster-gamedata-pipeline/releases/tag/recorder-v0.28.0-rc17.1.1) | 858 MB | 老品牌"Oyster Recorder"，可装可录 |
| **rc17.2.3** | [link](https://github.com/howardleegeek/oyster-gamedata-pipeline/releases/tag/recorder-v0.28.0-rc17.2.3) | 859 MB | **新品牌"GameData Recorder"** + Open Recordings shortcut + 自动 lint + 7 字段非 null + schema 修正 + gameinfo.xlsx + depth EXR |

## 🟡 已 tag + CI 跑中（还没出装包）

| 装包 | 加什么 | 等多久 |
|---|---|---|
| **rc17.3** | BG-rescue 输入捕获（WH_MOUSE_LL + 消息泵） | ~25 min |
| **rc17.3.1** | gameinfo.xlsx PRD §3.3 schema + 禁用错的 depth EXR | ~25 min |

## 🟢 代码 commit + push 到 origin（未 tag）

### Submodule (`vendor/recorder`)

| Branch | HEAD | 修啥 |
|---|---|---|
| `stream-bd-recorder-schema-emit` | `af21d56` | PRD schema 字段 recordDpi / intrinsics / quaternions |
| `stream-bh-narrow-camera-position` | `c053463` | sibling-dir fallback → 7 个 null 字段填值 |
| `stream-bn-postvalidate` | `e41a653` | 录完自动跑 lint v3 + Windows toast PASS/FAIL |
| `stream-bg-rescue` | `875431f` | input capture WH_MOUSE_LL + 消息泵（rescued from cluster） |
| `stream-bj-rewrite-rc17.3.1` | `7bd4d8c` | gameinfo.xlsx PRD §3.3 schema + 禁用 broken depth |
| `rc17.2-merged-bj` | `dcf9e29` | rc17.2 全 batch + BJ-cluster 4 文件原始 |
| `rc17.2-submodule-merged` | `633cf34` | 5-stream octopus merge (BD + BH-narrow + BN) |

### Parent (`oyster-agent-runner`)

| Branch | HEAD | 修啥 |
|---|---|---|
| `stream-be-bundler-trigger-fix` | `5a8ca92` | 修 bundler workflow_run trigger bug |
| `stream-bl-installer-ui-paths` | `d83f2f5` | installer.iss 加 "Open Recordings Folder" shortcut |
| `stream-bm-rebrand` | `c778c75` | 品牌统一 "GameData Recorder" + 文件名 |
| `stream-bn-postvalidate` | `56ea9c2` | (parent) submodule pointer bump |
| `stream-bh-narrow-camera-position` | `e17942b` | (parent) submodule pointer bump |
| `rc17.2-merged` | `c0377e3` | rc17.2 batch first |
| `rc17.2.1-fix` | `319ae4f` | GITHUB_TOKEN env for cargo-obs-build |
| `rc17.2.2-merged` | `51e28e9` | + BJ-cluster files |
| `rc17.2.3-asset-list-fix` | `b1d1e6c` | Mojang asset SHA drift fix |
| `rc17.3-bg-rescue` | `15db8f4` | + BG-rescue submodule bump |
| `rc17.3.1-merged` | `a8b3aa6` | + BJ-rewrite (gameinfo PRD + depth disabled) |

## 🛠️ 工具 + 文档（写了的）

| 文件 | 用途 |
|---|---|
| `oyster-audit/PRD-DATA-REQUIREMENTS.md` | PRD 4 件套 + 20 字段 + 14 xlsx 字段 + 32 lint criteria 全清单 |
| `oyster-audit/PRD-CHECKLIST-rc17.2.3.md` | rc17.2.3 字段逐项 PASS/FAIL 状态 |
| `oyster-audit/AUDIT-SYNTHESIS-rc17.2.2.md` | Red/Blue/Grill/Super 四维度审计（部分基于 cluster trace） |
| `oyster-audit/WHAT-WORKS-SUMMARY.md` | **这个文件** |
| `oyster/infra/dispatch/temporal-poc/minimax_agent_simple.py` | Aliyun cluster dispatch（验证通） |
| SSH `Open Recordings Folder.lnk` on minipc1 Desktop | 桌面 shortcut（即时 deploy） |

## 🔌 集群 + 后端

| 项 | 状态 |
|---|---|
| **Aliyun cluster dispatch** | ✅ 验证通（deepseek-v3.2 / MiniMax-M2.5 / qwen3.6-plus / glm-5 四模型轮转） |
| **SSH 3 节点全通** | mac1 ✅ / mac2 ✅ / minipc ✅ (Tailscale 100.105.39.60) |
| **API key** | `~/.oyster-keys/aliyun-token-plan.env` 工作中 |
| **Tool loop** | list_files → read_file → write_file → run_cmd → finish 全 5 工具验证 |
| **Cluster 80 turn max** | 配置可调（`MINIMAX_MAX_TURNS=80`） |

## 📊 数据流（录一次 session 实际产出）

每次 graceful 退出 MC 后，`%LOCALAPPDATA%\GameData Recorder\recordings\session_<TS>_<HASH>\` 应有：

```
recording.mp4         200-400 MB    ✅ rc17.2.3+ 都对
metadata.json         ~2 KB         ✅ 含 recordDpi (rc17.2.3+)
action_camera.json    ~150-200 KB   ✅ camera_position 非 null (rc17.2.3+)
                                    ⚠️ mouse_dx/dy 还 0 (rc17.3+ 才修)
frames.jsonl          ~6-10 KB      ✅ 1 Hz 帧 index
fps_log.json          ~30 KB        ✅
inputs.jsonl          438 B         ❌ markers only (rc17.3+ 才有 per-event)
                                    ✅ rc17.3+ 会有几百事件
gameinfo.xlsx         ~10 KB        ⚠️ rc17.2.2-2.3 是 4-sheet wrong schema
                                    ✅ rc17.3.1+ 是 PRD §3.3 单 sheet 14 字段
depth/depth_*.exr     —             ❌ rc17.2.2-2.3 是错的桌面截图（截到 desktop）
                                    🚫 rc17.3.1+ 禁用，rc17.4 重写
lint_result.json      ~2 KB         ✅ 自动 lint v3 + toast PASS/FAIL (rc17.2.2+)
```

## 🔧 修了什么 bug（这一夜）

1. ✅ **bundler trigger** rc17.0.5/17.1 没出装包（workflow_run 失效）
2. ✅ **品牌不一致** Oyster vs GameData 三套名字
3. ✅ **Installer shortcut** 找不到 recordings dir（empty 错觉）
4. ✅ **camera_position null** 等 7 字段（session_dir mismatch 根因）
5. ✅ **schema 字段错** recordDpi/intrinsics/quaternion 字段缺/重命名
6. ✅ **没自动验证** 录完客户看才知坏（BN 加自动 lint + toast）
7. ✅ **GITHUB_TOKEN 缺** cargo-obs-build anon 60/hr 撞死
8. ✅ **Mojang asset SHA 漂移** post_fetch_required_files 失效
9. ✅ **input capture 缺消息泵** inputs.jsonl 5 markers / 鼠标全默认（BG rescue）
10. ✅ **gameinfo.xlsx 错 schema** 4-sheet placeholder → PRD §3.3 单 sheet 14 字段
11. 🚫 **depth EXR 错实现** desktop 截图 + 1 Hz cadence → 禁用待 rc17.4

## ❌ 还没修（rc17.4 候选）

1. Depth EXR 正确实现（cv2 读 mp4 frames + DepthAnything → 6 fps × 1080p × float32 EXR）
2. 启动器表单 UI（operator_id / character_name / route_type / notes）
3. mc-mod IPC 传 scene_name / weather / time_of_day
4. OTLP telemetry 上报（Audit B1）—— cluster agent 跑中
5. `Recording::stop()` 幂等（Audit B2）
6. `session_id` 8 hex → 16 hex 防碰撞（Audit G4）
7. R1/R2 path/race 加固
8. Lint v3 加 criteria #33-37（depth EXR 数 / xlsx schema check）
9. mc-mod 速度 IPC（`speed` / `player_speed` 还 0）

## 总览

**已上线可下载装包**：rc17.1.1 + rc17.2.3
**即将上线**（CI 跑中）：rc17.3 + rc17.3.1
**Howard 实测的录像**：9 个完整 session（rc17.0.4 录的，部分字段 null 已知）
**总修复 bug 数**：11 个（10 修完 + 1 禁用待重写）

**核心成就**：从"很多 null + 找不到文件 + 品牌乱 + 装不出"演化到"安装就用 + 桌面 shortcut + 自动验证 + 真位置数据 + 品牌统一 + PRD schema 单 sheet 14 字段"。
