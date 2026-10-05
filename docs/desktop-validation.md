# 桌面版 1.1.0 验证记录

日期：2026-10-03。工作环境：macOS Apple Silicon。所有功能测试使用隔离临时目录和合成数据，未读取已有 `.env`、业务数据库或真实 API Key。

## 本轮交付

- Windows x64：NSIS `.exe` 安装包，内置官方 CPython 3.13.16 embeddable runtime 和 Windows wheels。
- macOS ARM64：`.app`、`.dmg`、`.zip`，内置 PyInstaller Python 3.12 后端。
- 保留 Web 开发路径；新增各平台构建脚本、依赖锁和 GitHub Actions。
- 原有 `compose.yml` 8082 端口修改保持不变。

## 已验证

- Python 后端及 Windows 装配脚本：50 项测试通过，另有 9 个 pytest subtests。
- Electron 进程、启动协议、IPC、权限、退出和剪贴板边界：24 项测试通过。
- 前端 SSE、会话竞态、取消、重试、输入法、复制、桌面身份与 Vue 构建：18 项测试通过。
- 实际冻结的 macOS 后端：启动、健康检查、未授权拒绝、禁用公开认证、会话 CRUD、禁止内部数据库、外部 SQLite 接入、模拟 AI HTTP → SQL → SSE → 历史保存、SPA 路由、stdin EOF 正常退出通过。
- 实际 Electron 源模式及 macOS 打包模式：界面挂载、渲染器无 Node 能力、自动身份、原生窗口内新建会话并落库、设置/本地版本、返回历史、Markdown 危险内容过滤、后台退出通过。
- Windows 后端资源：官方 Python SHA-256 匹配，34 个依赖，33 个 `.pyd`，46 个原生文件均为 x64 PE；没有混入 macOS 动态库。
- 后端打包资源未包含开发 `.env`、应用数据库或密钥文件。
- `git diff --check` 通过。

## 明确未验证的部分

- 当前环境未执行 Windows 程序；Windows 安装、窗口与 embedded 后端的实际运行需在 Windows 机器或所附 CI 上验证。
- Intel Mac 的构建流水线已配置，但本轮没有生成或运行 Intel Mac 安装包。
- 没有使用真实 DeepSeek Key，也没有连接实际 MySQL / PostgreSQL 服务。AI 流程使用本机模拟提供方，SQL 安全和 SQLite/Excel 使用真实隔离样本。
- 没有发布者证书。Mac 使用本地 ad-hoc 签名，无 Apple Developer ID 公证；Windows 无发行签名。
- CI 文件已添加，本轮未推送、未触发远端 Actions、未发布 Release。

## 已处理的实际整合问题

Vite 升级后最初同时打入 Vue ESM 和 CommonJS 两份运行时，导致 Element UI 消息组件异常，新建会话写库后无法继续更新界面。已通过提前配置 Vue alias 和 dedupe 修复，并新增生产构建回归测试。最终真实 Electron 窗口复测通过，没有通过降低 CSP 或渲染隔离规避问题。

前端依赖审计剩余 Vue 2 模板编译相关的低级别告警；未做破坏式 Vue 3 / Element Plus 迁移。桌面使用编译后的静态界面，不将用户内容作为 Vue 模板执行。
