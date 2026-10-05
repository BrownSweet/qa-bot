# 桌面版 1.2.0 验证记录

日期：2026-10-03。实际运行环境：macOS 26.5.1 / Apple Silicon ARM64，Node 24，Electron 44.5.1；Mac 后端为 Python 3.12.13 / PyInstaller 6.22.3。全部运行测试使用临时工作区、合成 SQLite 数据与模拟 AI，未使用个人数据库、API Key 或已有桌面工作区。

## 最终产物

独立保存在 `release-1.2.0/`，构建资源在 `build-1.2.0/`。原有 `release/` 中的 1.1.0 DMG、ZIP、EXE 已重新对照原 SHA256SUMS 校验，全部一致。

| 文件 | 字节数 | SHA-256 |
| --- | ---: | --- |
| QA-Robot-1.2.0-mac-arm64.dmg | 155751603 | 0cfa9f976c883c3a9582b8494c589e26f29cffb716af55f4e64c31b71d2e1762 |
| QA-Robot-1.2.0-mac-arm64.zip | 156810556 | b355aa9547b72765fdb9c634f420bbe2dcf0deb52ff12ccbace9c365c24e3c6c |
| QA-Robot-1.2.0-win-x64.exe | 132240300 | 321012108c1ea778e67c3d7af205b3b61ea85c21629bb063886a3fd257b04086 |

同目录 `SHA256SUMS.txt` 可独立校验。两平台 `app.asar` 内的 main/runtime/preload 与 1.2.0 构建时的源码逐字节一致，前端入口与当时的最终 dist 一致；后端资源清单、源文件 SHA-256 和依赖锁均通过打包门槛。打包前检测到后端源码变化时会拒绝旧资源，本轮实际验证过此拒绝路径。

## 已执行的检查

- Python 后端及脚本全量回归：106 项通过，另有 9 个 pytest subtests。测试新增候选数据源测试失败不覆盖旧配置、30 秒超时、保留旧密码，以及评测期间数据漂移不误判。
- Electron 启动协议、生命周期、信任边界、IPC、剪贴板、日志轮转、工作区命令及签名配置门槛：28 项通过。
- 前端：39 项通过，生产构建成功。任务历史对比会按所选两次运行提示来源、SQL、结果列和截断范围的差异；评测执行错误不再进入样例通过率分母。
- 最终 Mac 冻结后端：动态 loopback 端口、健康鉴权、自动本地身份、公开认证关闭、会话持久化、内部数据库拒绝、外部 SQLite、业务口径进入真实 SQL 提示、模拟 AI HTTP → SQL → SSE → 保存记录、参数化任务两次运行及结果、标准样例运行/原始快照/基线变化、结果和会话 CSV/Excel 导出、SPA 和 stdin EOF 退出通过。
- 最终 Mac `.app` 实际窗口：渲染隔离、自动会话、新建会话、设置版本、分析任务和准确率评测窗口、合成查询证据的 SQL/来源/结果显示、危险 Markdown 的真实 DOM 清洗通过。
- 同一次打包窗口测试完成：在线备份 → 校验 ZIP → 停止服务 → 恢复 → 重启 → 确认会话保留 → 导出诊断；未读写用户剪贴板，复制能力由注入 mock 的 IPC 单元测试验证。
- 使用真实 1.1.0 与最终 1.2.0 两个 Mac `.app`，先后打开同一隔离 profile。最终 schemaVersion 为 4；原密钥字节相同、原会话保留、升级前备份存在，新版窗口检查通过。
- `hdiutil verify` 验证最终 DMG 成功；`codesign --verify --deep --strict` 验证 `.app` 结构成功。实际签名为 ad-hoc，TeamIdentifier 未设置，无 Developer ID 公证。
- Windows 资源：官方 CPython 3.13.16 embeddable runtime 的校验值匹配，34 个依赖、33 个 `.pyd`、46 个 x64 PE 原生文件完成静态验证；NSIS EXE 已生成。应用 EXE 的图标/版本资源已写入，版本字符串为 1.2.0；应用 EXE 与安装器的 Authenticode 证书目录均为空，确认没有发布者签名。
- `git diff --check` 通过。

## 工作区恢复与升级边界

数据库迁移按版本执行：旧记录的来源字段、任务/口径来源身份、旧评测快照均保持未知值，不推测历史证据。桌面升级前自动生成完整数据库与密钥快照；已有数据库但密钥缺失时阻断启动。新安装默认 AI 地址与模型固定，宿主环境不能静默改写桌面首启配置，Web 模式继续支持环境配置。

备份使用 SQLite 在线快照，检查文件清单、大小、摘要、SQLite 完整性、密钥配对与版本。可携带本地 Excel/SQLite，旧 `database_name` 路径格式也已覆盖；包含文件的备份在另一临时目录恢复后可查询原数据。复制前和复制后都限制单文件大小，涵盖 WAL 合并或源文件增长；恢复时将来源放在独立 sources 目录并更新关联。

恢复停服后通过日志替换数据库与密钥，提交时原子更名恢复日志，再清理旧文件；回滚会清理本次搬移的数据源目录。测试覆盖替换中断与提交后清理中断。SQLite 连接在结束时显式关闭，避免 Windows 上残留句柄阻碍替换。以上属于本机故障注入与回归验证，不等同于所有文件系统及断电场景的可靠性认证。

ZIP 含密钥、凭证、SQL、来源路径与已保存预览，不额外加密，应保存在受控位置。仅配置备份不会携带外部文件，检查时报告缺失引用；换机后需要重新定位。恢复后来源路径改变时，原业务口径和任务可能需要重新确认。诊断包只导出系统状态及日志计数，不含原始日志文本、业务 SQL 或结果；原始日志仍仅留在用户日志目录。

评测只比较在当前数据上执行标准 SQL 与模型 SQL 得到的列和结果；等值不能证明两条 SQL 在其他数据上语义等价。评测期间若标准结果变化会记录为需复核并停止本次比对。真实业务样例应包含退款、状态、时间边界等反例。1.2.0 的回答/评测尚未保存当次业务口径版本或模型版本，分析任务运行也尚未保存模板及参数的独立快照；历史可核对问题、来源、SQL 与结果，但不能据此完整复现旧提示词。来源和 SQL 不同的两次任务运行会在界面提示差异，仍需人工核对业务可比性。

## 可重复命令

```sh
export QA_BUILD_ROOT="$PWD/build-1.2.0"
export QA_RELEASE_DIR="$PWD/release-1.2.0"
backend/.venv-desktop/bin/python scripts/build_backend.py
backend/.venv-desktop/bin/python scripts/build_windows_backend.py
backend/.venv-desktop/bin/python scripts/smoke_backend.py
node scripts/package_desktop.cjs mac arm64
node scripts/package_desktop.cjs win x64
node scripts/smoke_desktop.cjs
backend/.venv-desktop/bin/python scripts/smoke_upgrade.py --from-release release --to-release release-1.2.0
```

两个 smoke 桌面脚本自动使用隔离目录；成功清理，失败保留日志。升级脚本需要本机架构对应的旧、新 `.app` 或 Windows unpacked 应用。源文件变化后必须重建后端再打包。

## 尚未实际验证

- Windows 操作系统上的安装、运行、文件恢复、卸载和 UI；本轮 Windows 只有跨平台装配与静态校验。
- Intel Mac 的构建与运行。CI 已配置原生 runner，但本轮没有相应机器或产物。
- 真实 DeepSeek/其他兼容 AI 服务、真实 MySQL/PostgreSQL 与用户业务数据。没有声称已有 50–100 道业务评测样本或已测得生产准确率。
- 正式证书签名、Apple Developer ID 公证、干净下载环境的 Gatekeeper/SmartScreen 体验。签名配置缺少凭证会失败，但没有证书实跑。
- 远端 GitHub Actions、安装包发布和自动更新。未推送、未触发 CI、未发布 Release；当前升级方式为安装新版本并迁移本地工作区。

GitHub runner 标签及输入条件仅完成静态核对：[官方 runner 列表](https://docs.github.com/en/actions/reference/runners/github-hosted-runners)列有 `macos-15-intel`，`macos-14` 为 ARM64；签名仅在手动 dispatch 且布尔输入为 true 时执行，PR/tag 明确走未签名构建。配置存在不代表远端执行成功。
