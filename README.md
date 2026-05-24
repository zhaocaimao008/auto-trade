# 自动交易软件

Python 3.11+ 开发的 A 股自动交易系统，支持模拟交易与 **EasyTrader 实盘交易**。

## 快速安装

```bash
cd /root/自动交易软件
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -r requirements.txt
pip install -e .
cp .env.example .env
```

## 启动 GUI

```bash
cd /root/自动交易软件
source .venv/bin/activate
python -m autotrader_app.main
```

## 目录结构

```text
自动交易软件/
  .env.example           # 环境配置模板
  .env                   # 实际配置（不提交）
  pyproject.toml
  requirements.txt
  src/autotrader_app/
    main.py              # 入口
    config.py            # 配置管理
    broker/
      broker_base.py     # Broker 抽象接口
      mock_broker.py     # 模拟交易柜台
      easytrader_broker.py  # EasyTrader 实盘柜台（可选安装）
      trade_executor.py  # 统一交易执行器
    strategies/           # 双均线、MACD 等策略
    risk/                 # 风控管理器
    gui/                  # PyQt6 图形界面
    backtest/             # backtrader 回测引擎
    data/                 # 行情数据源（AKShare / Tushare）
```

## 配置

复制 `.env.example` 为 `.env` 并填写配置项。

### 数据源

默认使用 **AKShare**（无需 Token）：

```env
TUSHARE_TOKEN=
DEFAULT_DATA_SOURCE=akshare
```

如果使用 **Tushare Pro**：

```env
TUSHARE_TOKEN=你的token
DEFAULT_DATA_SOURCE=tushare
```

### 数据源

详见 [数据源配置](docs/data_sources.md)。

### EasyTrader 实盘交易

> ⚠️ **易确认风险**：实盘交易涉及真实资金，使用前请充分测试。

详见 [EasyTrader 实盘交易指南](docs/easytrader_guide.md)。

## 当前能力

- 获取 A 股历史行情（AKShare / Tushare）
- 双均线策略、MACD 趋势策略
- 多策略并行评估与自动执行
- 模拟交易（完整资金/持仓管理）
- 风控管理（止损/止盈/仓位上限/全局回撤熔断）
- 实时 K 线图 + 权益曲线（多策略对比）
- 回测引擎（backtrader）
- **EasyTrader 实盘接入**（可选安装，默认关闭）
- SQLite 持久化（委托/成交/持仓/账户快照）

