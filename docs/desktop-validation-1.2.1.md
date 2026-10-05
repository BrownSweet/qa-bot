# QA Robot 1.2.1 本地验证记录

验证日期：2026-10-03；构建主机：macOS 26.5.1 / Apple Silicon。旧版安装包保留在 `release/`（1.1.0）和 `release-1.2.0/`。1.2.1 的构建资源与安装包分别使用 `build-1.2.1/`、`release-1.2.1/`，不覆盖旧包。所有运行检查使用临时工作区、合成 SQLite 数据和本地模拟 AI，没有读取个人数据库或 API Key。

## 功能与数据迁移

1.2.1 在生成 SQL 前保存当次输入：模型、方言、Schema、业务口径、问题、实际传给模型的最近对话，以及分析任务的模板和参数。回答失败后仍可从历史记录查看已提交的输入。任务运行、评测历史和会话 CSV/Excel 导出均可读取对应快照；Excel 超长文本拆到证据页。旧记录保持空值，界面显示“未记录”，不会用后来编辑过的任务或业务口径补造历史。

桌面历史仅保存 AI 服务 origin 和完整路由地址的 SHA-256 指纹，不保存 URL 路径、查询参数或凭证；Web 模式只返回指纹。旧快照若含完整地址，在 API 和导出读取时也会去掉路径。快照可能包含业务表结构、口径与对话文字，已纳入会话导出大小限制和备份敏感数据提示。该快照记录生成输入，不保存当次提示词模板全文、服务端模型权重或数据库完整状态，因此不能保证确定性重放。评测在调用模型前先持久化待执行记录与输入；取消和重启后遗留的运行会标为错误，Web 历史只收束超过 30 分钟的遗留运行。

数据库 schema 由 v4 升至 v5，为旧消息和评测记录新增可空快照字段。真实安装包在同一隔离 profile 上完成 1.1.0→1.2.1（v1→v5）及 1.2.0→1.2.1（v4→v5）升级：会话、密钥保留，升级前备份存在且 manifest 记录旧 schema 版本，新版窗口正常运行。

## 本机产物

| 文件 | 字节数 | SHA-256 | 验证范围 |
| --- | ---: | --- | --- |
| `QA-Robot-1.2.1-mac-arm64.dmg` | 155754773 | `67cccac37fd63f6b02ba08af423769c595df2afb645ea7cbe5c6bda11e8700f2` | 本机运行 |
| `QA-Robot-1.2.1-mac-arm64.zip` | 156816334 | `4c203e71280040eb2312cc5a86457fea0dc02d048d398cce6af183a4f86df042` | 同一 `.app` 已运行 |
| `QA-Robot-1.2.1-mac-x64.dmg` | 166376887 | `59166463f9c5e64dd5aa7a0d9d1609b8ef02322a2d0e4b2245c6b63ab564b155` | Rosetta 下运行 |
| `QA-Robot-1.2.1-mac-x64.zip` | 167461628 | `9d46d3da470dc530308e2f45f278bea403e51e9b3f9cd25b43068dae24d14efa` | 同一 `.app` 已在 Rosetta 下运行 |
| `QA-Robot-1.2.1-win-x64.exe` | 132243504 | `7bd024d278e2de5d78d27e822bb8651c9b110816a8cd1a448cbaa9842987ac48` | 跨平台装配与静态检查 |

同目录的 `SHA256SUMS.txt` 用于校验分发文件。打包钩子核对后端平台/架构、源文件列表与 SHA-256、依赖锁摘要；前端构建指纹还核对源码、构建配置、依赖锁与相关环境，过期 `dist` 已实测会被拒绝。源码或锁发生变化后必须重建对应资源。Mac x64 后端通过 Rosetta 下的 Python 3.12.13 / PyInstaller 6.22.3 构建，28 个 Mach-O 文件均含 x86_64。Windows 使用经官方 SHA-256 校验的 CPython 3.13.16 embeddable runtime、34 个锁定依赖和 33 个 `.pyd`；46 个原生 PE 文件均为 x64。Windows 安装器本身是 32 位 NSIS 启动程序，其内的应用 EXE 与 Python 后端均为 x64。这些静态检查不代表已在 Windows 运行。

## 已执行检查

- `pytest backend/tests scripts/tests -q`：114 项通过，另有 9 个 pytest subtests；包括 v5 迁移、旧记录空值、失败保留快照、AI 地址路径净化、评测中断/遗留运行、任务与评测历史、会话导出和原有工作区恢复检查。
- 前端 44 项、Electron 28 项通过；前端生产构建成功。实际 Mac `.app` 窗口验证了入口、会话、设置版本、任务与评测窗口、查询证据、生成输入快照与 AI 地址脱敏、Markdown DOM 清洗。
- 源码与冻结 Mac ARM/x64 后端均通过模拟 AI HTTP/SSE 全链路检查：本机鉴权、SQLite 数据源、SQL、回答、生成快照、任务参数改动前后历史、评测口径改动前后历史，以及 CSV/Excel 导出。冻结后端检查还涵盖启动和正常退出。
- Mac ARM 原生与 x64 Rosetta 下的实际 `.app` 窗口完成备份、ZIP 校验、停服恢复、会话确认及诊断导出。1.1.0 和 1.2.0 两条真实安装包升级路径在 ARM 上通过。
- 两个 Mac DMG 均通过 `hdiutil verify`；两个 `.app` 均通过 `codesign --verify --deep --strict`。签名类型是 ad-hoc，没有 Developer ID 身份和 Apple 公证。Mac/Windows `app.asar` 中主程序与源码一致，所携带前端文件与最终 `frontend/dist` 一致。
- Windows EXE 与 NSIS 安装器的 PE 证书目录为空；没有 Authenticode 发布者签名。`git diff --check` 通过。

## 尚未验证与发布边界

- Windows 系统中的安装、启动、数据源连接、恢复、卸载和界面行为。本机仅能生成 Windows x64 EXE 并做静态检查。
- Intel Mac 原生硬件上的运行。本机已从 `cryptography 50.0.2` 官方源码包编译 x64 扩展，静态链接 OpenSSL 4.0.3，保留原依赖锁；CPython、Rust、OpenSSL 与源码包的下载摘要均与各自上游公布值匹配。上游自 49.0.0 起不发布 Mac x64 wheel，本机 Rosetta 成功仍需 Intel 实机及远端 CI 重现后才能声称原生平台验收完成。没有使用回退到 48.0.1 的实验版本。
- 真实 AI 服务、真实 MySQL/PostgreSQL 与用户业务数据。尚无经过用户核对的 50–100 道业务标准样例，也没有生产准确率结论。评测相同结果不证明其他数据上的 SQL 语义相同。
- Developer ID/Apple 公证、Windows 证书签名、Gatekeeper/SmartScreen 下载体验及远端 CI。当前本地工作树尚未推送，也未发布 GitHub Release。

Mac x64 源码构建的下载 SHA-256 留作复核：Astral CPython 3.12.13 x64 `ed741cca5783263844e051cedbb9e0728a8e8c0a903038ac910a2664c36e532a`；Rust x64 rustup-init `259e2b84274434085163fe8d556510571772cda2aa6d87ca6aa664f57bc644e3`；OpenSSL 4.0.3 `325b5c806167c13b40b1ffeadfe0248197c00eccc4cf123ec1e28d2d2fd216d9`；PyPI `cryptography 50.0.2` sdist `7b46165bb56eb4704e2eaaf86f3c940d19154535d9b0ca7d6d590b04060e00d5`。这些是依赖来源校验，不替代最终安装包的签名与公证。

## 可重复命令

```sh
export QA_BUILD_ROOT="$PWD/build-1.2.1"
export QA_RELEASE_DIR="$PWD/release-1.2.1"
backend/.venv-desktop/bin/python -m pytest backend/tests scripts/tests -q
npm run test:frontend
npm run test:desktop
npm run frontend:build
backend/.venv-desktop/bin/python scripts/smoke_backend.py --source
backend/.venv-desktop/bin/python scripts/build_backend.py
backend/.venv-desktop/bin/python scripts/build_windows_backend.py
backend/.venv-desktop/bin/python scripts/smoke_backend.py
node scripts/package_desktop.cjs mac arm64
node scripts/package_desktop.cjs win x64
node scripts/smoke_desktop.cjs
# Intel Mac：先准备含 x64 Python 3.12 和源码编译 cryptography 50.0.2 的独立环境
QA_BUILD_ROOT="$PWD/build-1.2.1-x64" arch -x86_64 build-1.2.1-x64/toolchain/venv/bin/python scripts/build_backend.py
QA_BUILD_ROOT="$PWD/build-1.2.1-x64" backend/.venv-desktop/bin/python scripts/smoke_backend.py
QA_BUILD_ROOT="$PWD/build-1.2.1-x64" node scripts/package_desktop.cjs mac x64
QA_SMOKE_ARCH=x64 node scripts/smoke_desktop.cjs
backend/.venv-desktop/bin/python scripts/smoke_upgrade.py --from-release release --to-release release-1.2.1
backend/.venv-desktop/bin/python scripts/smoke_upgrade.py --from-release release-1.2.0 --to-release release-1.2.1
```

`smoke_backend.py` 和桌面 smoke 使用临时目录。Intel Mac 命令中的 toolchain 是本机隔离构建环境，不能仅复制目录就证明他处可复现；远端 CI 仍需执行。Windows 的 `scripts/smoke_backend.py` 与 `scripts/smoke_desktop.cjs` 仍需在 Windows 主机实际执行。旧文档 [1.2.0 验证记录](desktop-validation-1.2.md)保留的是当时版本的证据。
