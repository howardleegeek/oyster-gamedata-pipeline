# 持续迭代框架 — Oyster GameData Pipeline

> Living document. Cluster + iron-law-gate auto-runs daily. Howard reviews 5 min/day.

## ISC (Ideal State Criteria) — 8 条硬指标

| # | 指标 | 当前 | 目标 |
|---|------|------|------|
| 1 | `--strict-buyer` 在真 MC session exit 0 | 0/0 真 session | 10/10 |
| 2 | 买方一条命令离线 verify exit 0 | 端到端 verified（合成） | 真 session × 1 个买方角色 |
| 3 | Consumer 装机 30s + 30 天 0 干预 | 75% | 100% |
| 4 | 100 并发 7 天 < 5% CPU | 未测试 | green |
| 5 | Producer 24h 收款 SLA | 0 真 payout | green |
| 6 | 多游戏 ≥3 | 1 (MC only) | MC + Roblox + BeamNG |
| 7 | 单 session 集群成本 < $0.05 | ~$0.02 (est.) | green |
| 8 | 日均 ≥1 release | 2/天 (5/18 + 5/19) | 持续 |

## 4 阶段蓝图

| 阶段 | Week | 版本 | 硬门 ISC |
|------|------|------|----------|
| Phase A — 闭 Gap #1 | 1 (5/20–5/26) | v0.5.0 | ISC-1 |
| Phase B — 买方信任链 | 2 (5/27–6/02) | v0.6.0 | ISC-2 |
| Phase C — Consumer tray daemon | 3 (6/03–6/09) | v0.7.0 | ISC-3 |
| Phase D — Scale + Payout | 4 (6/10–6/16) | v0.8.0 | ISC-4, ISC-5 |
| Phase E — Multi-game | 5+ | v1.0 | ISC-6 |
| Phase F — 持续 flywheel | 持续 | v1.x | ISC-7, ISC-8 |

## 迭代引擎 — 4 daemon + 1 dispatcher

| 组件 | 触发 | 不允许 |
|------|------|--------|
| `iter-watcher.py` | cron `0 * * * *` | 写代码 |
| `cluster-dispatcher.py` | iter-watcher 出新 spec | 跑两个 spec 抢同一文件 |
| `iron-law-gate.sh` | PR open | 让 SKIP/synthetic-PASS 过 |
| `release-tagger.sh` | cron `*/30 * * * *` | 出 tag 不写 CHANGELOG |
| `rsv-feeder.sh` | cron `0 */6 * * *` | 跑过的 session 再跑 |

## 当周（v0.5.0）实际 dispatch 状态

| Spec | 状态 | 模型 | Branch |
|------|------|------|--------|
| S05 — H8 PASS_STRICT 档 | 🟡 cluster 运行中 | qwen3.6-plus | feat/S05-h8-pass-strict |
| S06 — strict-buyer evidence provenance | 🟡 cluster 运行中 | qwen3.6-plus | feat/S06-strict-buyer-evidence-provenance |
| RSV01 sweep (10 真 session) | ⏳ 等 FLK 装上 minipc → schtasks fire → 10 session | — | — |

## 铁律

1. 集群出代码，mac1 不直接写产品代码
2. 任何 PR 通过 iron-law-gate 才 merge：no假pass / black+ruff / pytest / prd_compliance_audit
3. release tag 必须有 CHANGELOG entry
4. ISC 不达标的 week 不开下一 week 工作流
