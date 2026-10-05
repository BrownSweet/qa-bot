# QA Robot · 本地数据问答

Windows / macOS 桌面应用。接入 MySQL、PostgreSQL、SQLite 或 Excel，用自然语言查询数据并生成流式分析报告。

安装包包含 Electron 界面、Python 后端、SQLite 与数据库驱动。使用者无需安装 Python、Node、Docker 或单独启动服务。AI 分析需要联网和自己的 DeepSeek API Key；数据库、会话和配置保存在本机。问题、表结构和部分查询结果会发送到设置中的 AI 服务。

## 1.2.1 工作区与分析能力

- 查询记录包含 SQL、来源快照和受限结果预览，帮助核对结论与查询依据；单元格最多 8 KiB，返回行 JSON 总量最多 256 KiB，完整证据保存上限为 512 KiB。历史来源不明确的记录不补造证据。
- 数据源业务口径、可重复分析任务和标准问题评测提供独立管理入口。编辑数据源时，“保存并测试”仅在候选连接成功后替换旧配置。任务历史可并列查看两次运行，来源、SQL 或结果范围不一致时显示具体差异。评测需要自行录入真实业务问题、标准 SQL 与预期结果；项目不自带已经验证的 50–100 道业务样本。
- 设置 → 本地应用可以备份、校验备份、恢复、导出诊断、打开日志和重启本地服务。服务无法启动时，原生“工作区”菜单仍提供恢复与诊断入口。
- 新生成的回答、分析任务运行和评测记录保存当次 SQL 生成输入：模型、问题、Schema、业务口径、最近对话，以及任务模板和参数。历史页面可展开核对，CSV/Excel 会话导出也包含快照。旧记录显示“未记录”，不会用现值填充。Web 模式只返回 AI 服务地址指纹；桌面快照只存服务域名与地址指纹，不保存路径或凭证。

查询证据和生成输入快照可能包含业务敏感数据，并会进入本地 SQLite、会话导出和工作区备份。**备份 ZIP 包含配对密钥和凭证，不额外加密**，请只保存在受控位置，分享前先确认内容。

评测通过率只计算基线稳定且成功完成比对的样例，执行错误和基线变化单列。结果相同不能证明 SQL 语义在其他数据上正确，应为标准样例准备边界与反例数据。评测在请求模型前保存待执行记录与当次输入，中断的运行会留下可核对的错误记录。1.2.1 起保存的是当次输入快照；提示词模板代码、服务端模型实现和原始数据变化仍会影响重现，不能把快照当作确定性重放。升级前的旧记录无法补回当次输入。

## 使用

1. Windows 运行 `QA-Robot-<版本>-win-x64.exe` 安装；macOS 打开对应芯片的 `.dmg`，将应用拖入 Applications。
2. 启动后自动进入本地工作区，无需注册、手机验证码或登录。
3. 设置 → AI 服务配置，填写 API Key，点击“保存并测试连接”。默认地址为 `https://api.deepseek.com`，模型为 `deepseek-chat`。
4. 添加数据源。Excel / SQLite 可使用系统文件选择器；远程数据库建议专门创建只读账号。
5. 创建会话并提问。支持停止回答、查看历史、重试对应问题和导出会话。

Excel 支持 `.xlsx` / `.xlsm`，第一行为列名，各工作表映射为一张表。保留数字、日期类型，自动处理清洗后重名的列；单个工作簿最多 200,000 行。`.xls` 不受支持，公式使用文件中的缓存结果。

会话导出包含回答、来源快照、生成输入快照、SQL、范围说明和已保存结果预览；Excel 将较长查询依据及生成输入拆到独立工作表。回答中的查询依据也可单独导出，均不能代替全量原始数据。查询最多取 200 行且返回行 JSON 总量不超过 256 KiB，AI 最多分析其中前 50 行且结果输入不超过 64 KiB；实际行数还会随单元格与字节预算减少，截断时显示范围。最近 6 条已完成消息参与上下文理解；切换数据源后仍以本次实际 Schema 为准。

会话导出仅携带回答时保存的预览，最多 10,000 条消息、8 MiB 消息、证据与生成输入原文、10,000 行结果预览；超过任一限额会明确拒绝，可改为导出单次回答的结果预览。兼容接口 Excel `/sheet-data` 同样按流读取，限制为 200 行 / 256 KiB。

## 本地数据与安全边界

- 设置 → 本地应用 → 打开数据目录。备份时退出应用，**同时保留 `qabot.db` 和 `secrets.json`**；缺失密钥后无法解密已有 API Key 和数据源密码。
- 推荐使用“备份工作区”：通过 SQLite 在线备份得到一致快照，再记录文件摘要与数据库版本。可选择同时携带 Excel / SQLite 文件（最多 100 个、单文件 256 MiB、总量 1 GiB），在另一台机器恢复时复制到用户目录的独立 `sources/` 文件夹并重新关联数据源。远程数据库只保留连接配置。
- “校验备份”不会修改当前数据；检查 ZIP 清单、摘要、SQLite 完整性、数据库与密钥能否配对、版本兼容性及缺失文件。仅配置备份不会携带外部文件，跨设备恢复后应重新定位。来源路径变化后，旧业务口径和分析任务可能需要重新确认，不能自动视为原数据源。
- 恢复前会显示原生确认并停止本地服务；当前有效工作区会自动生成 `pre-restore` 备份。数据与密钥通过恢复日志共同替换，异常中断时下次启动先回滚。已有数据库但密钥缺失时，应用停止启动并引导恢复，不会另生成一套密钥掩盖问题。
- 数据库通过 `schema_migrations` 记录版本。升级已有桌面库前会自动写入 `data/backups/pre-upgrade-*.zip`；旧记录新增的来源字段保持空值，未来版本数据库禁止降级打开。自动快照也包含敏感数据，长期使用时应定期检查磁盘与清理不再需要的备份。
- 桌面使用新的独立数据目录，不自动读取或迁移仓库旧数据库、开发 `.env` 或 Docker 数据卷。升级与卸载不主动删除本地数据。
- FastAPI 只监听 `127.0.0.1` 随机端口。每次启动生成独立传输凭证，仅 Electron 主进程为可信窗口请求添加；普通浏览器不能直接访问 API。
- 页面启用 sandbox、context isolation、CSP，禁用 Node 集成、任意页面导航和默认权限。Markdown 用 DOMPurify 清洗。
- SQLite 数据源以文件只读方式打开；SQL 经过单语句 AST 校验，拒绝写入、锁、文件访问和未知函数，执行时还有只读事务、超时与行数限制。应用内部数据库禁止作为数据源使用。
- 本地密钥文件使用安装实例专属随机密钥和系统文件权限保护。这不抵御已取得当前操作系统账号权限的攻击者；安装包也不提供不可逆向保证。
- 本地构建默认没有 Windows 发布者签名或 Apple Developer ID 公证。正式对外分发前应使用自己的签名身份，不能把本地 ad-hoc 签名当成 Apple 公证。
- 日志在用户目录 `logs/`，单份最多约 4 MiB，保留 3 份。诊断 ZIP 只包含平台、架构、数据文件存在状态及日志数量统计，不导出原始日志文字、SQL、结果、数据库、密钥、外部数据文件或环境变量。原始日志可能包含业务错误描述，仅保存在本机；如需另外提供给支持人员，请先人工检查。

## 开发

构建工具需要 Node.js 24、Python 3.12 和 uv，最终用户不需要这些工具。

```sh
npm ci
npm ci --prefix frontend
uv venv --python 3.12 backend/.venv-desktop
uv pip install --python backend/.venv-desktop/bin/python -r backend/requirements-build.txt
npm run frontend:build
QA_BACKEND_PYTHON="$PWD/backend/.venv-desktop/bin/python" npm run desktop:dev
```

Windows 开发时虚拟环境解释器位于 `backend\.venv-desktop\Scripts\python.exe`，在 PowerShell 中设置 `$env:QA_BACKEND_PYTHON` 后运行 `npm run desktop:dev`。后端使用本机动态端口，不需要启动 Vite 服务。

如果本机 npm 配置了 `ignore-scripts=true`，首次安装后还需运行 `node node_modules/electron/install.js` 下载 Electron 运行时。

## 构建安装包

先执行前端构建。打包钩子会校验前端构建指纹及后端平台、CPU 架构和源码摘要，避免带入旧页面或错平台后端。前端源码、配置、依赖锁或构建环境变化后需重新执行 `npm run frontend:build`。

### macOS

```sh
backend/.venv-desktop/bin/python scripts/build_backend.py
backend/.venv-desktop/bin/python scripts/smoke_backend.py
npx electron-builder --mac --arm64 --publish never
node scripts/smoke_desktop.cjs
```

Intel Mac 使用 `--x64`，后端也必须由 x64 Python 构建，不能只修改 Electron 架构后复用 ARM 后端。[cryptography 49.0.0 发布说明](https://cryptography.io/en/49.0.0/changelog/)已移除官方 macOS x64 wheel；当前锁定的 50.0.2 需要 x64 Rust/OpenSSL 工具链从源码编译。[1.2.1 验证记录](docs/desktop-validation-1.2.1.md)列出了本机 Rosetta 构建和运行结果。`QA_SMOKE_ARCH=x64 node scripts/smoke_desktop.cjs` 可在 Apple Silicon 上检查 x64 包，但 Intel 实机仍需单独验收。

### Windows x64

```sh
python scripts/build_windows_backend.py
npx electron-builder --win --x64 --publish never
```

Windows 使用官方 CPython 3.13.16 embeddable runtime，校验官方 SHA-256，装配锁定的 Windows x64 wheels。该脚本可在 Mac 上准备资源，独立写入 `build/backend-windows`。在 Windows 上继续执行下面命令验证实际启动；跨平台装配成功不等于 Windows 运行测试通过：

```sh
python scripts/smoke_backend.py
node scripts/smoke_desktop.cjs
```

产物位于 `release/`。`.github/workflows/desktop.yml` 支持手动构建，以及 `desktop-v*` 标签构建，包含 Windows x64、Mac ARM64、Mac Intel 三个原生 runner 的验证和安装包上传；不会自动发布 GitHub Release。

为保留旧安装包，可设置 `QA_BUILD_ROOT="$PWD/build-1.2.1"` 和 `QA_RELEASE_DIR="$PWD/release-1.2.1"`，然后执行对应后端构建及 `node scripts/package_desktop.cjs mac arm64`（Windows 为 `win x64`）。打包前校验前端构建指纹及后端平台、架构、源码摘要与依赖锁，源码发生变化后必须重建对应资源。smoke 脚本也读取这两个隔离路径。

正式签名使用 `node scripts/package_desktop.cjs mac arm64 --signed` 或 `win x64 --signed`。Mac 需要 `QA_MAC_SIGN_IDENTITY`、`CSC_LINK`、`CSC_KEY_PASSWORD`、`APPLE_ID`、`APPLE_APP_SPECIFIC_PASSWORD`、`APPLE_TEAM_ID`；Windows 需要 `WIN_CSC_LINK`/`CSC_LINK` 与对应密码。缺少证书或公证凭证会直接失败，不退回未签名产物。CI 的手动 `signed` 输入启用相同门槛。配置路径参照当前锁定的 [electron-builder v26 macOS 文档](https://www.electron.build/v26/docs/mac/)和 [Windows 文档](https://www.electron.build/v26/docs/win/)。证书和远端 CI 尚未实际执行时，不能声称已经完成正式发行签名。

## 验证

1.2.1 安装包、真实运行与升级检查见 [1.2.1 验证记录](docs/desktop-validation-1.2.1.md)；[1.2.0 验证记录](docs/desktop-validation-1.2.md)保留作历史依据。Windows、Intel Mac、正式签名、真实 AI 与远端 CI 的实际验证边界在新记录中分别列明。

```sh
backend/.venv-desktop/bin/python -m pytest backend/tests scripts/tests -q
npm run test:desktop
npm run test:frontend
npm run frontend:build
backend/.venv-desktop/bin/python scripts/smoke_backend.py --source
```

测试使用合成数据、临时目录和模拟 AI，不访问个人数据库、密钥或真实 AI 账号。打包后还需要运行上面的两个 smoke 脚本：后端覆盖本机鉴权、数据源与会话；Electron 覆盖真实窗口挂载、建立会话、设置页和退出。

目前前端保留 Vue 2.7 / Element UI，升级了 Vite 与 Axios 并锁定依赖。Vue 2 模板编译相关的低级别依赖告警仍存在；应用不把用户输入编译为 Vue 模板。后续迁移 Vue 3 应单独完成组件回归。

## 目录

```text
desktop/                   Electron 生命周期、IPC、文件对话框、进程管理
backend/desktop_server.py  动态端口和父进程退出协议
backend/app/               认证、数据源、SQL 安全、AI、会话和持久化
frontend/src/              桌面界面与兼容 Web 界面
scripts/                   各平台后端装配、架构校验、打包后验证
backend/tests/             查询安全、Excel 类型、流取消、权限测试
.github/workflows/         Windows / macOS 构建流水线
```

## 原有 Web / Docker 模式

仍可独立使用 FastAPI 和 Vite。后端默认 `8000`，前端 `5555`，Vite 的 `/api` 代理到 `127.0.0.1:8000`。

```sh
cd backend
uvicorn main:app --reload --port 8000
```

```sh
cd frontend
npm run dev
```

Web 模式默认禁用验证码回显；仅开发联调可设置 `QA_DEV_AUTH=1`。共享 AI 配置仅允许 `QA_ADMIN_USER_IDS` 中列出的用户修改，本地文件访问需显式设置 `QA_ALLOW_LOCAL_FILES=1`。复制 `.env.example` 后必须替换密钥，Web 模式不应直接沿用开发配置对外开放。

原有 Docker 部署文件保留。SQLite 和 MySQL 是独立存储，切换数据库配置不会自动迁移已有数据。
