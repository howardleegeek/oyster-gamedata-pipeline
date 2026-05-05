# SOP — Minecraft 数据采集交付 (给老刘)

> 5 行原则。复杂细节看 [`docs/MC_STACK_VERSIONS.md`](MC_STACK_VERSIONS.md)。

## 一、版本

- **Minecraft Java Edition 1.20.4**(不是 Bedrock,也不是 1.21+)
- 服务端:**PaperMC 1.20.4 build 499**(我们的脚本会自动下载,无需手动装)

## 二、模组 / 插件

**全部 NONE。** 不装任何 Forge / Fabric / Spigot 插件。我们的 bot 走 vanilla Java 协议,**装任何模组都会破坏协议反而跑不通。**

## 三、运行依赖

只需要两件:
- **Java 21**(OpenJDK,`brew install openjdk@21` 或 `apt install openjdk-21-jdk`)
- **Node.js 20**(`brew install node@20` 或 `nvm install 20`)

## 四、运行命令

```bash
git clone https://github.com/howardleegeek/oyster-gamedata-pipeline.git
cd oyster-gamedata-pipeline
bash bin/smoke_phase1.sh           # 自动下载 paper-1.20.4.jar 并启动
cd mineflayer && npm install && cd ..
bash bin/produce_real_sample_v2.sh # 端到端采一份样本数据
```

## 五、产出验证

```bash
python3 dist/oyster-qa-tool.pyz check --quick
# 期望: ✓ 24/24 PRD checks PASSED
```

---

## 兼容性红线(踩了就跑不通)

| 误以为可以 | 实际 |
|---|---|
| 装 OptiFine / Sodium / Iris | ❌ 破坏协议,bot 连不上 |
| 用 Forge / Fabric mod loader | ❌ 同上 |
| 升 1.21+ | ❌ Mineflayer 4.20 还没稳定支持 |
| 用 Bedrock 客户端 | ❌ 协议完全不兼容 |
| 只装 Java 17 | ⚠️ Paper 1.20.4 还能跑但 1.20.5+ 强制要 Java 21,统一上 21 |

## 卡住的 3 种典型报错

1. `UnsupportedClassVersionError 65.0` → Java 不是 21,装一下
2. `Cannot find supported version` → 服务端版本不是 1.20.4
3. `Connection lost: Outdated server!` → 同上

**就这些。**
