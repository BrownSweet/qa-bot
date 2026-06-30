@echo off
chcp 65001 >nul
REM ===== 启动前端（Windows）=====
cd /d %~dp0frontend

REM 首次运行：安装依赖
if not exist node_modules (
    echo [1/2] 安装前端依赖...
    npm install
)

echo [2/2] 启动开发服务器 http://localhost:5555 ...
npm run dev
pause
