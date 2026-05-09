---
task_id: R05B-bundled-minecraft-packaging
project: recorder
priority: 1
estimated_minutes: 60
depends_on: [R05A]
modifies:
  - bin/build_bundled_installer/fetch_minecraft.py   # NEW
  - bin/build_bundled_installer/fetch_fabric.py      # NEW
executor: glm-aliyun
---

## 目标
CI 时从 Mojang piston-meta + Fabric meta 下载所有 MC 1.21.4 vanilla 资源
+ Fabric loader 0.16.10 jars + libraries, 全部 SHA 验证, 落到 `bundle/mc-instance/`.

## 数据准确铁律
- 用 piston-meta.mojang.com/mc/game/version_manifest_v2.json 找 1.21.4 client manifest URL
- 该 manifest 内含 client.jar / libraries / asset index URLs + SHAs
- 每个文件下载后 SHA 验证, 失败立刻 fail-loud
- Fabric: meta.fabricmc.net/v2/versions/loader/1.21.4/0.16.10 拉 launcher json
- 所有库 jars 按 maven 路径放好

## 验收
- [ ] `python bin/build_bundled_installer/fetch_minecraft.py` exits 0
- [ ] 产生 `bundle/mc-instance/versions/1.21.4/1.21.4.jar` + `.json`
- [ ] 产生 `bundle/mc-instance/libraries/...` 含全部 ~60 vanilla libs
- [ ] 产生 `bundle/mc-instance/assets/indexes/19.json`
- [ ] 产生 `bundle/mc-instance/versions/fabric-loader-0.16.10-1.21.4/...`
- [ ] 产生 `bundle/mc-instance/libraries/net/fabricmc/...` 8 Fabric libs
- [ ] 不下 assets objects (~280MB) — runtime 第一次启动时拉 (节省 install 大小)
- [ ] 总大小 < 200MB (libs only, no assets)

## 不要做
- 不要碰用户的 `%APPDATA%\.minecraft\` (那是 Mojang Launcher 的私产)
- 不要 ship 完整 assets (太大, 用户首次启动时下)
- 不要 ship Mojang authentication tokens
