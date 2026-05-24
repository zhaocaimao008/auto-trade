# EasyTrader 实盘交易指南

通过 `easytrader` 库连接券商客户端，实现自动化实盘交易。

> ⚠️ **风险警告**
>
> 实盘交易涉及真实资金，可能导致亏损。请务必：
> 1. **先用模拟模式（BROKER_TYPE=mock）运行至少 1 个月**
> 2. **先在模拟模式下验证策略逻辑**
> 3. **从小资金开始测试实盘连接**
> 4. **理解并设置合理的止损参数**
> 5. **本软件不承担任何投资损失**

---

## 一、支持的券商

| 券商 | broker_type | 客户端 |
|------|------------|--------|
| 华泰证券 | `ht` | 涨乐财富通 |
| 银河证券 | `yjb` | 中国银河证券 |
| 广发证券 | `gf` | 广发证券易淘金 |
| 东方财富 | `eastmoney` | 东方财富证券 |
| 国泰君安 | `gtja` | 国泰君安君弘 |
| 中信建投 | `csc` | 中信建投证券 |

---

## 二、安装

### 2.1 安装 easytrader

```powershell
# 推荐：先创建并激活虚拟环境
python -m venv .venv
.venv\Scripts\activate

# 安装 easytrader
pip install easytrader

# 安装 pywinauto（Windows 客户端自动化必需）
pip install pywinauto

# 安装本项目的其余依赖
pip install -r requirements.txt
pip install -e .
```

### 2.2 验证安装

```powershell
python -c "import easytrader; print('easytrader version:', easytrader.__version__)"
```

正常输出：
```
easytrader version: x.x.x
```

---

## 三、准备工作

### 3.1 下载券商客户端

以华泰证券为例：

1. 访问 [华泰证券官网](https://www.htsc.com) 下载 **涨乐财富通** PC 版
2. 安装完成后，**手动登录** 客户端
3. 确认客户端保持在 **已登录状态**（不要关闭窗口）

> easytrader 是通过连接已登录的客户端进程进行操作的，客户端必须保持运行。

### 3.2 找到客户端可执行文件路径

找到券商客户端安装目录下的主 exe 文件，例如：

- 华泰涨乐财富通：`C:\Program Files\华泰证券\涨乐财富通\xiadan.exe`
- 银河证券：`C:\Program Files\银河证券\xiadan.exe`

> 注意：easytrader 需要连接的是 **交易客户端**（xiadan.exe），而非行情客户端。

---

## 四、配置

### 4.1 编辑 `.env` 文件

```env
# ── 数据源 ──────────────────────────────────────────────
TUSHARE_TOKEN=
DEFAULT_DATA_SOURCE=akshare

# ── Broker 类型 ─────────────────────────────────────────
# mock       → 模拟交易（默认，无风险）
# easytrader → 实盘交易（通过 easytrader 连接券商客户端）
BROKER_TYPE=easytrader

# ── EasyTrader 实盘配置 ─────────────────────────────────
EASYTRADER_BROKER_TYPE=ht         # ht=华泰, yjb=银河, gf=广发
EASYTRADER_ACCOUNT=你的账号
EASYTRADER_PASSWORD=你的密码
EASYTRADER_EXE_PATH=C:\Program Files\华泰证券\涨乐财富通\xiadan.exe
```

### 4.2 安全设置（重要）

**建议**：首次配置时保持 `IS_LIVE=False`（即 `BROKER_TYPE=mock`），通过 GUI 配置界面切换：

1. 启动 GUI 后，点击菜单 **配置 → Tushare Token / 券商设置**
2. 切换到 **Broker** Tab
3. Broker 类型选择 `easytrader`
4. 填写配置信息
5. **不要勾选** "启用实盘交易"
6. 点击 OK 保存

在模拟模式下，下单操作只会打印日志，不会实际发送委托。

---

## 五、启动与测试

### 5.1 启动 GUI

```powershell
.venv\Scripts\activate
python -m autotrader_app.main
```

### 5.2 测试连接（推荐用 Python Shell 测试）

```powershell
.venv\Scripts\activate
python
```

```python
>>> from autotrader_app.broker import create_broker
>>> from autotrader_app.config import get_settings

# 1. 检查配置
>>> s = get_settings()
>>> print(s.easytrader_account, s.easytrader_exe_path)

# 2. 创建 Broker 实例（模拟模式，不下真单）
>>> broker = create_broker(broker_type="easytrader", is_live=False)

# 3. 登录（客户端需要已经打开并登录）
>>> success = broker.login()
>>> print(success)  # True 表示连接成功

# 4. 查询持仓（不涉及下单，安全）
>>> positions = broker.get_positions()
>>> print(positions)

# 5. 查询账户
>>> account = broker.get_account()
>>> print(account)

# 6. 查询当日成交
>>> fills = broker.get_fills()
>>> print(fills)
```

### 5.3 预期结果

- `login()` 返回 `True` → 客户端连接成功
- `get_positions()` → 返回当前持仓 DataFrame（可能为空）
- `get_account()` → 返回资金信息
  - `cash`：可用资金
  - `market_value`：持仓市值
  - `total_assets`：总资产

---

## 六、下单测试（先用模拟模式）

以下操作均**不会**发送真单（`is_live=False`）：

```python
>>> from autotrader_app.models import OrderRequest, OrderSide, OrderType

# 创建买单请求
>>> order = OrderRequest(
...     symbol="000001",
...     side=OrderSide.BUY,
...     quantity=100,
...     price=12.50,
...     order_type=OrderType.MARKET,
...     strategy_name="manual",
... )

# 模拟下单
>>> result, fills = broker.place_order(order)
>>> print(result.status)   # FILLED（模拟）
>>> print(result.reason)   # "模拟确认（is_live=False）"
```

---

## 七、启用实盘模式（谨慎！）

当确认以下所有条件满足后，可启用实盘：

- [ ] 模拟交易运行至少 1 周，策略表现符合预期
- [ ] 登录和查询功能均正常
- [ ] 已在非交易时段完成下单测试
- [ ] 已设置合理的止损参数
- [ ] 已理解可能产生的亏损风险

启用方式：

1. **方法一（推荐）**：通过 GUI 菜单 **配置 → ... → Broker**，勾选 **"启用实盘交易"**
2. **方法二**：直接修改 `.env`：

```env
# IS_LIVE 并不存在——实盘由 BROKER_TYPE 控制
BROKER_TYPE=easytrader
```

> 实盘启用后，GUI 状态栏会显示 **🔴 实盘模式** 红色警告标签。

---

## 八、故障排除

### 8.1 登录失败

```
EasyTrader 登录失败：账号(True)和客户端路径(True)不能为空
```

→ 检查 `.env` 中 `EASYTRADER_ACCOUNT` 和 `EASYTRADER_EXE_PATH` 是否正确填写。

### 8.2 连接不上客户端

```
EasyTrader 登录失败: [WinError 2] 系统找不到指定的文件
```

→ 确认 `EASYTRADER_EXE_PATH` 路径正确，且客户端已安装。

### 8.3 查询持仓为空

```
查询到的持仓为空
```

→ 这是正常情况——当前账户确实没有持仓，或当天尚未登录客户端。

### 8.4 查询失败

```
查询持仓失败: 'NoneType' object has no attribute 'position'
```

→ 客户端未成功连接，请先调用 `login()`。检查客户端是否已手动登录。

### 8.5 easytrader 导入失败

```
easytrader 未安装，实盘功能不可用。请执行: pip install easytrader
```

```powershell
pip install easytrader pywinauto
```

---

## 九、安全清单

- [ ] 实盘模式下定期检查日志，确认无异常下单
- [ ] 风控参数（止损/止盈/最大回撤）已设置
- [ ] 首次实盘时使用最小交易单位（100 股）
- [ ] 不在非交易时间运行自动交易引擎
- [ ] 定期检查 `.env` 文件权限，防止凭据泄露
