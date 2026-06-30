# 问答机器人系统

基于自然语言的数据库问答机器人。用户接入自定义数据源（MySQL / PostgreSQL / SQLite / Excel），
用自然语言提问，系统通过 DeepSeek 完成 NL2SQL → 执行查询 → 流式生成分析报告（SSE 打字机效果）。

按照配套的 PRD / 技术架构 / 系统设计(SDD) / API 文档实现。

## 技术栈

- **前端**：Vue 2.7 + Vite 4 + ElementUI + Vuex + Vue Router + Axios + marked
- **后端**：FastAPI + SQLAlchemy 2 + JWT(python-jose) + bcrypt + AES-256-CBC(cryptography) + httpx + openpyxl
- **AI**：DeepSeek API（NL2SQL + 结果分析，流式）
- **主数据库**：默认 SQLite（零配置），可切换 MySQL 8.0+

## 目录结构

```
qa-robot/
├── backend/
│   ├── app/
│   │   ├── config.py          # 配置（读取 .env）
│   │   ├── database.py        # 引擎/会话/建表+初始化
│   │   ├── models.py          # 全部 SQLAlchemy 模型
│   │   ├── schemas.py         # 全部 Pydantic 模型
│   │   ├── security.py        # bcrypt / JWT / AES / 登录锁定 / 当前用户依赖
│   │   ├── ai.py              # DeepSeek 封装（生成SQL/流式分析/连接测试）
│   │   ├── engine_utils.py    # 目标库动态连接 / Schema / 执行SQL / Excel→SQLite
│   │   ├── utils.py           # 统一错误 + 日志记录
│   │   └── routers/           # auth/db_config/sessions/chat/system/user/excel/logs/export/notifications
│   ├── main.py                # FastAPI 入口（路由挂载 + 统一错误格式）
│   ├── requirements.txt
│   └── .env.example
└── frontend/
    ├── src/
    │   ├── api/index.js        # Axios 封装 + 全部接口 + SSE 流式问答
    │   ├── store/index.js      # Vuex（token/user 持久化）
    │   ├── router/index.js     # 路由 + 登录守卫
    │   ├── views/              # AuthView / DashboardView / SettingsView
    │   └── components/         # db / session / chat 组件
    ├── vite.config.js          # /api 代理到 :8000
    └── package.json
```

## 快速开始

### 1. 后端

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # 按需修改；默认用 SQLite，开箱即用
uvicorn main:app --reload --port 8000
```

启动后访问交互式 API 文档：http://localhost:8000/docs

### 2. 前端

```bash
cd frontend
npm install
npm run dev                     # http://localhost:5173 （已配置 /api 代理到 8000）
```

### 3. 使用流程

1. 打开 http://localhost:5173 → 注册（验证码在开发模式下直接弹出）→ 登录
2. 进入【设置 → AI 服务配置】，填入 DeepSeek API Key 并测试连接
3. 主页左侧【数据源】添加并测试一个数据库（或 Excel 文件）
4. 新建会话 → 选择数据源 → 输入问题，回车发送，观察流式回答

## 关键实现说明

- **SSE 流式问答**：`POST /api/chat/send` 返回 `text/event-stream`，依次推送
  `status(connecting/scanning/analyzing)` → 多个 `message` → `complete`。
  前端用 `fetch + ReadableStream` 解析，`AbortController` 实现“停止”。
- **NL2SQL**：从目标库提取 Schema → DeepSeek 生成 SQL（仅允许 SELECT）→ 执行 → DeepSeek 流式分析。
- **Excel 数据源**：把工作表读入内存 SQLite，每个 sheet 一张表，从而支持自然语言查询。
- **安全**：用户密码 bcrypt(成本因子12)；数据库密码 / API Key 使用 AES-256-CBC 加密存储；
  JWT 24h 过期（记住我 30 天）；密码错误 5 次锁定 15 分钟（内存计数）。
- **统一错误格式**：所有错误返回 `{ "error": code, "message": msg }`，与 API 文档一致。

## 相对文档的取舍（务实落地）

| 项 | 文档 | 实际实现 | 原因 |
|----|------|----------|------|
| 主数据库 | MySQL | 默认 SQLite，可切 MySQL | 零配置即可运行，改 `DATABASE_URL` 即用 MySQL |
| 短信验证码 | 手机短信 | 开发模式直接返回 `dev_code` | 无短信网关，便于联调；生产对接短信服务即可 |
| 前端语言 | Vue2 + TS | Vue 2.7 + JS（SFC） | 降低构建复杂度、减少分层，接口契约/类型语义保持一致 |
| 后端分层 | API/逻辑/数据访问分层 | 路由内直接访问 DB（无 service/repo 层） | 按需求“减少分层”，保持低抽象、易读 |

所有 **27 个 API 接口**、**8 张数据表**、SSE 事件协议、错误码均严格对齐文档。

## 接口对照

完整接口见 `http://localhost:8000/docs`，覆盖：认证、数据库配置、会话管理、问答交互、
系统配置、用户、数据导入(Excel)、日志、数据导出、通知 共 11 个模块。
