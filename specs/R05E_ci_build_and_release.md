---
task_id: R05E-ci-build-and-release
project: recorder
priority: 1
estimated_minutes: 30
depends_on: [R05A, R05B, R05C, R05D]
modifies:
  - .github/workflows/build-recorder-installer.yml   # NEW
executor: glm-aliyun
---

## 目标
GitHub Actions 在每次 tag push 触发, 跑 R05A→D 全链, 产生
`OysterRecorder-Setup-vX.Y.Z.exe`, 上传到 GitHub Release.

## 数据准确铁律
- CI 必须验 SHA-256 of every fetched dependency (JRE, MC, Fabric, libs)
- Build artifact 内含 manifest.json (内含每个 bundled 文件的 SHA)
- Installer 自带 post-install verification (装完跑 manifest check)

## 验收
- [ ] `.github/workflows/build-recorder-installer.yml` 存在
- [ ] 触发: tag push `recorder-v*`
- [ ] Steps:
  1. checkout
  2. install Inno Setup (chocolatey)
  3. install PyInstaller
  4. R05A fetch JRE
  5. R05B fetch MC + Fabric
  6. R05C compile OysterPlay.py → OysterPlay.exe
  7. PyInstaller build OysterRecorder onedir
  8. Inno Setup compile installer.iss → OysterRecorder-Setup-vX.Y.Z.exe
  9. SHA-256 manifest 写入 release notes
  10. gh release upload
- [ ] Release page 显示:
  - `OysterRecorder-Setup-vX.Y.Z.exe` (~460MB) ← 用户下这个
  - `SHA-256-manifest.txt`
  - 9 个 mod jars (单独 download for advanced users)
- [ ] CI 失败时 (任何 SHA 不对) 不发 release

## 不要做
- ❌ 不要在 Release page 列那一堆中间产物 (只列用户需要的)
- ❌ 不要把 build credentials 写进 Release notes
- ❌ 不要 ship 没 sign 的 installer 给生产 (打 alpha tag OK, GA 必须 sign)
