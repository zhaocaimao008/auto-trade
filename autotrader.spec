# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for A股自动交易系统.

Usage:
    pyinstaller autotrader.spec

Output: dist/A股自动交易系统/
"""
import sys
from pathlib import Path

# ── 项目根目录 ─────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent
SRC_DIR = PROJECT_ROOT / "src"

block_cipher = None

# ── 数据文件收集 ──────────────────────────────────────────
# .env 模板、文档、日志目录占位
datas = [
    (str(PROJECT_ROOT / ".env.example"), "."),
    (str(PROJECT_ROOT / "README.md"), "."),
    (str(PROJECT_ROOT / "docs"), "docs"),
]

# ── 隐藏导入（PyInstaller 无法自动发现的模块）─────────────
hiddenimports = [
    # PyQt6 子模块
    "PyQt6.QtCore",
    "PyQt6.QtGui",
    "PyQt6.QtWidgets",
    "PyQt6.QtSvg",
    # matplotlib 后端
    "matplotlib",
    "matplotlib.backends",
    "matplotlib.backends.backend_qtagg",
    "matplotlib.backends.backend_qt5agg",
    "matplotlib.figure",
    "mpl_toolkits",
    # pandas / numpy
    "pandas",
    "numpy",
    # SQLAlchemy
    "sqlalchemy",
    "sqlalchemy.ext.declarative",
    "sqlalchemy.orm",
    "sqlalchemy.sql",
    # loguru
    "loguru",
    # schedule
    "schedule",
    # python-dotenv
    "dotenv",
    "dotenv.variables",
    # pydantic
    "pydantic",
    "pydantic_settings",
    # easytrader (optional)
    "easytrader",
    # akshare (optional, needed for data)
    "akshare",
    # tushare (optional)
    "tushare",
    # backtrader
    "backtrader",
    # uuid
    "uuid",
]

# ── 排除不需要的模块（减小体积）───────────────────────────
excludes = [
    "tkinter",
    "tkinter.*",
    "test",
    "unittest",
    "distutils",
    "setuptools",
    "pdb",
    "lib2to3",
    "concurrent",
    "http.server",
    "email",
    "xmlrpc",
    "asyncio",
    "multiprocessing",
    "curses",
    "bz2",
    "lzma",
    "zipfile",
    "tarfile",
]

# ── 主程序入口 ────────────────────────────────────────────
a = Analysis(
    [str(SRC_DIR / "autotrader_app" / "main.py")],
    pathex=[str(SRC_DIR)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    cipher=block_cipher,
)

# ── PYZ（压缩字节码）─────────────────────────────────────
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# ── EXE（主执行文件）─────────────────────────────────────
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="A股自动交易系统",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,          # 不显示控制台窗口（GUI 模式）
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(PROJECT_ROOT / "app.ico") if (PROJECT_ROOT / "app.ico").exists() else None,
)

# ── 目录打包（--onedir 模式）────────────────────────────
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="A股自动交易系统",
)
