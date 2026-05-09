---
task_id: R05A-bundled-jre-packaging
project: recorder
priority: 1
estimated_minutes: 30
depends_on: []
modifies:
  - bin/build_bundled_installer/fetch_jre.py    # NEW
  - bin/build_bundled_installer/manifest.json   # NEW (JRE SHA pin)
executor: glm-aliyun
---

## 目标
CI 时下载 portable Eclipse Temurin OpenJDK 21 LTS, SHA-256 验证, 解压到
`bundle/jre/`. 不依赖系统 Java.

## 数据准确铁律
- JRE 来自 adoptium.net 官方 release URL (写死 release tag, 不要 latest)
- Manifest 写 SHA-256 pin, build 时 fail-loud 如果不匹配
- 解压后必须含 `bin/javaw.exe` (Windows x64)

## 验收
- [ ] `python bin/build_bundled_installer/fetch_jre.py` exits 0
- [ ] 产生 `bundle/jre/bin/javaw.exe`
- [ ] SHA 验证失败时 exits ≠ 0 with clear error
- [ ] manifest.json 内含确切 release URL + SHA + size

## 不要做
- 不要从系统 PATH 找 Java
- 不要用 winget/choco/scoop (用户机器可能没装)
- 不要假设有 admin 权限
