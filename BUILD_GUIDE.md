# Windows 打包指南

## 快速打包

```powershell
# 1. 在项目目录下直接运行
build.bat
```

## 手动打包步骤

```powershell
# 1. 激活虚拟环境
.venv\Scripts\activate

# 2. 安装打包工具
pip install pyinstaller

# 3. 执行打包
pyinstaller autotrader.spec --noconfirm --log-level WARN

# 4. 复制配置文件（自动打包时已处理）
copy .env dist\A股自动交易系统\.env
```

## 输出文件

```
dist\A股自动交易系统\
├── A股自动交易系统.exe      # 主程序（双击运行）
├── .env                      # 配置文件（编辑此文件）
├── .env.example              # 配置模板
├── README.md
├── docs\                     # 文档
│   ├── easytrader_guide.md
│   └── go_live_checklist.md
├── logs\                     # 日志目录（自动创建）
└── ... (其他 dll 和依赖)
```

## 常见问题

### 1. 打包后运行报错 "Failed to execute script"

**原因**: 缺少 hidden imports。
**解决**: 在 `autotrader.spec` 的 `hiddenimports` 列表中添加缺失的模块，重新打包。

### 2. 打包后 matplotlib 图表不显示

**原因**: matplotlib 后端未正确包含。
**解决**: 确保 `hiddenimports` 中包含 `matplotlib.backends.backend_qtagg`。

### 3. 打包后数据库无法写入

**原因**: 程序运行目录无写入权限。
**解决**: 将整个 `A股自动交易系统` 文件夹放在有写入权限的位置（如桌面、D 盘）。

### 4. 打包后 Easytrader 无法连接

**原因**: easytrader 依赖 pywinauto 和 Windows COM 组件。
**解决**: 
- 确保已安装 `pywinauto`：`pip install pywinauto`
- 确保券商客户端已安装并登录
- easytrader 仅支持 Windows

### 5. 打包体积过大（200MB+）

**原因**: numpy/pandas/matplotlib 体积较大。
**优化方案**:
1. UPX 压缩：下载 upx.exe 放到 PATH 中，spec 中已配置 `upx=True`
2. 排除不需要的模块：spec 中已有 `excludes` 列表
3. 使用 `--onefile` 模式（但启动较慢）
4. 使用 `pip install --no-build-isolation` 减少冗余

### 6. 打包后防病毒软件报毒

**原因**: PyInstaller 打包的 exe 会被部分杀软误报。
**解决**:
- 提交给杀软厂商分析白名单
- 使用数字签名
- 不影响功能，可放心使用

### 7. 打包后缺少 .env 文件

**解决**: 手动从项目根目录复制 `.env` 到 `dist\A股自动交易系统\`。

## 打包选项说明

| 选项 | 当前 | 说明 |
|------|------|------|
| `--onedir` | ✅ | 目录模式，方便调试，启动快 |
| `--onefile` | ❌ | 单文件模式，体积小但启动慢 |
| `--console` | ❌ | 不显示控制台（GUI 应用） |
| `--noconfirm` | ✅ | 覆盖输出目录不提示 |
| `--log-level WARN` | ✅ | 减少打包日志输出 |
| `upx=True` | ✅ | UPX 压缩（需安装 upx） |

## 版本记录

| 日期 | 版本 | 说明 |
|------|------|------|
| 2026-05 | v1.0 | 初始打包配置 |
