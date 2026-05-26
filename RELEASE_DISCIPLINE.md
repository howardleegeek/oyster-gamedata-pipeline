# Release Discipline — 太多版本治理方案

*2026-05-26: 8 天 45+ tags, total 199 tags. 收口。*

---

## 现状 grill

| 病征 | 根因 |
|------|------|
| 8 天 45+ tags | S93 auto-tag bot + Codex auto-release + manual tag 三股线性叠加 |
| 测试员困惑哪个 link | 没有 "latest stable" anchor |
| Release 列表噪音 | `[skip ci]` chore commits 也出 tag |
| 版本号语义不清 | v0.11.x 和 recorder-v0.28-rc19.0.x 平行存在 |

---

## 3 个 Canonical Anchors (其他统统 archive)

| Tag | Role | 推荐场景 |
|-----|------|----------|
| **`recorder-v0.28.0-rc19.0.3.2`** | 🏆 **测试员路径最简** (1 GB Lite MVP) | 给新测试员发链接 |
| **`v0.11.10`** | ⭐ 历史 verified (18 downloads, production gate 加入) | 备份 fallback |
| **`recorder-v0.28.0-rc19.0.4`** | 🆕 Codex 今天 fix (launcher gate + OysterPlay auto-launch) | 等更彻底 verify |

### 直接 link (canonical)

```
🏆 Lite MVP (Howard 记忆里的 1 GB 双击就用):
https://github.com/howardleegeek/oyster-gamedata-pipeline/releases/download/recorder-v0.28.0-rc19.0.3.2/OysterRecorder.exe

⭐ Production-gated (v0.11.10):
https://github.com/howardleegeek/oyster-gamedata-pipeline/releases/tag/v0.11.10

🆕 Latest fix (rc19.0.4 — launcher bug fixed but new architecture):
https://github.com/howardleegeek/oyster-gamedata-pipeline/releases/download/recorder-v0.28.0-rc19.0.4/OysterRecorder.exe
```

---

## Cleanup actions (执行优先级)

### 优先级 1 — 立刻停 noise (5 min)

1. Disable `Auto Tag Bot` workflow (`.github/workflows/auto-tag-on-merge.yml`)
2. Disable `Auto Release Tagger` workflow
3. Codex `scripts/auto_release.sh` 只在 manual trigger 跑, 不再自动

### 优先级 2 — Mark canonical anchors (10 min)

1. GitHub UI: 给 rc19.0.3.2 + v0.11.10 + rc19.0.4 加 "Latest release" label (only one allowed, pick rc19.0.3.2 for 现在最简)
2. 其他 release marked as "Pre-release" 或 "Draft"
3. README 顶部加 "👉 Latest stable: rc19.0.3.2" 一行 link

### 优先级 3 — Archive 不要的 (15 min)

1. 删除所有 `vX.Y.Z` tag with 0 downloads AND `[skip ci]` chore origin
2. 保留 milestone tags: v0.4.0/.1, v0.5.0, v0.8.2 (历史 milestones), v0.10.0, v0.11.0
3. 删除中间 patch tags v0.5.1-.3, v0.6.x, v0.7.x, v0.8.3-.15, v0.9.x, v0.10.x, v0.11.2-.18 (~30 tags)

### 优先级 4 — 新版本规则 (强制 going forward)

```yaml
# .github/RELEASE_RULES.md
- 只在 manual `gh release create vX.Y.Z` 时新 tag
- 自动 tag bot DISABLED
- 每周最多 2 个 release (Mon + Thu)
- 每 release 必须有 release notes 含 [LATEST_STABLE] label
- patch tag 只在 hotfix-on-stable 场景 (not "another commit landed")
```

---

## 协同模式 (Claude + Codex)

| 谁能 tag | 谁不能 |
|----------|--------|
| Claude (cluster wave 完成) | ❌ 不能 daily auto-bump |
| Codex (stabilization milestone) | ❌ 不能每次 commit 后 tag |
| Auto-tag bot | ❌ DISABLED |
| Howard (任何时候) | ✅ 总能 manual tag |

### 命名 namespace

| Prefix | Owner | Pattern |
|--------|-------|---------|
| `v0.X.Y` | Claude (overall framework / audit pipeline) | semver |
| `recorder-v0.X-rcY` | Codex (recorder client only) | release candidate |
| `cluster-vX` | Aliyun cluster batch milestones | wave-based |

---

## Decision matrix — Howard 一句话决定

| 你说 | 我做 |
|------|------|
| "停 tag noise" | 立刻 disable 2 个 workflow + paused auto-release.sh |
| "只留 3 个 link" | hide 其他 release, README 锚 3 个 canonical |
| "全删, 从头来" | unsafe — 199 tags 删 100+ 不可逆，建议 archive only |
| "继续推但加纪律" | 实施优先级 1+2+4 (停 noise + canonical + new rules), 跳过 3 (不删旧 tag) |

**推荐: "继续推但加纪律"** — 5/10/15 min 执行, 30 min 后产线清爽.

---

🦪 Joint discipline (Claude + Codex)
2026-05-26 PT
