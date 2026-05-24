@echo off
chcp 65001 >nul
title A股自动交易系统 - 打包工具
echo ============================================
echo   A股自动交易系统 - Windows 打包脚本
echo ============================================
echo.

:: ── 检查 Python 环境 ──────────────────────────────────────
where python >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] 未找到 Python，请先安装 Python 3.11+
    pause
    exit /b 1
)

:: ── 检查/创建虚拟环境 ────────────────────────────────────
if not exist ".venv" (
    echo [INFO] 创建虚拟环境...
    python -m venv .venv
    if %errorlevel% neq 0 (
        echo [ERROR] 创建虚拟环境失败
        pause
        exit /b 1
    )
)

:: ── 激活虚拟环境 ──────────────────────────────────────────
echo [INFO] 激活虚拟环境...
call .venv\Scripts\activate.bat
if %errorlevel% neq 0 (
    echo [ERROR] 激活虚拟环境失败
    pause
    exit /b 1
)

:: ── 安装依赖 ──────────────────────────────────────────────
echo [INFO] 安装项目依赖...
pip install -U pip -q
pip install -r requirements.txt -q
if %errorlevel% neq 0 (
    echo [WARN] 部分依赖安装失败，继续尝试...
)

:: ── 安装打包工具 ──────────────────────────────────────────
echo [INFO] 安装 PyInstaller...
pip install pyinstaller -q
if %errorlevel% neq 0 (
    echo [ERROR] PyInstaller 安装失败
    pause
    exit /b 1
)

:: ── 检查是否有 .env 文件（无则从模板复制）───────────────
if not exist ".env" (
    echo [INFO] 未找到 .env，从模板复制...
    copy .env.example .env >nul
    echo [WARN] 请编辑 .env 填入你的配置
)

:: ── 清理旧打包 ────────────────────────────────────────────
echo [INFO] 清理旧打包文件...
if exist "dist\A股自动交易系统" (
    rmdir /s /q "dist\A股自动交易系统"
)
if exist "build\A股自动交易系统" (
    rmdir /s /q "build\A股自动交易系统"
)
if exist "A股自动交易系统.spec" del "A股自动交易系统.spec"

:: ── 生成应用图标（如果没有）─────────────────────────────
if not exist "app.ico" (
    echo [INFO] 未找到 app.ico，使用默认图标...
    :: Python 生成一个简单的 PNG 再转 ico？直接跳过，spec 中已处理 None
)

:: ── 执行打包 ──────────────────────────────────────────────
echo.
echo [INFO] 开始打包...
echo       模式: --onedir（目录模式）
echo       输出: dist\A股自动交易系统\
echo.

pyinstaller autotrader.spec --noconfirm --log-level WARN

if %errorlevel% neq 0 (
    echo.
    echo [ERROR] 打包失败！请检查上面的错误信息。
    echo.
    echo 常见问题：
    echo   1. 缺少依赖：pip install easytrader akshare tushare
    echo   2. PyInstaller 版本问题：pip install --upgrade pyinstaller
    echo   3. Windows 编译工具缺失
    goto :end
)

:: ── 复制数据文件 ──────────────────────────────────────────
echo [INFO] 复制数据文件到输出目录...
if not exist "dist\A股自动交易系统\logs" mkdir "dist\A股自动交易系统\logs"
copy ".env" "dist\A股自动交易系统\.env" >nul 2>&1
echo [INFO] 数据文件复制完成

:: ── 输出结果 ──────────────────────────────────────────────
echo.
echo ============================================
echo   ✅ 打包成功！
echo ============================================
echo.
echo   输出目录: dist\A股自动交易系统\
echo   主程序:   dist\A股自动交易系统\A股自动交易系统.exe
echo.
echo   大小预估:
echo     --onedir: ~200-350 MB（当前模式）
echo     --onefile: ~150-250 MB（单文件，后续优化）
echo.
echo   使用说明:
echo     1. 直接双击运行 A股自动交易系统.exe
echo     2. 首次运行前编辑 .env 配置
echo     3. 默认使用 MockBroker（模拟交易）
echo     4. 如需实盘，请按文档配置 EasyTrader
echo.
echo   打包优化建议:
echo     - 使用 upx 压缩可减小 ~30%% 体积
echo     - 排除不需要的模块（已在 spec 中配置）
echo     - 正式发布可改为 --onefile 模式
echo.

:end
echo.
echo 按任意键退出...
pause >nul
