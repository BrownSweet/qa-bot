@echo off
chcp 65001 >nul
REM ===== 启动后端（Windows）=====
cd /d %~dp0backend

REM 没有虚拟环境就创建
if not exist .venv (
    echo [1/3] 创建虚拟环境...
    python -m venv .venv
)
call .venv\Scripts\activate.bat

REM 每次都确保依赖完整（已安装的会被 pip 跳过，很快）
echo [2/3] 检查/安装依赖...
python -m pip install --upgrade pip
pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo 依赖安装失败，请把上面的报错发给我。
    pause
    exit /b 1
)

REM 没有 .env 就从示例复制一份
if not exist .env copy .env.example .env >nul

echo [3/3] 启动服务 http://localhost:8888 ...
uvicorn main:app --reload --port 8888
pause
