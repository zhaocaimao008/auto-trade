# 自动交易软件

Python 3.11+ 开发的 A 股自动交易系统骨架，包含：

- `PyQt6` 图形界面
- `SQLite + SQLAlchemy` 数据存储
- `Tushare Pro / AKShare` 数据源抽象
- `MockBroker` 模拟交易
- `backtrader` 回测入口
- 双均线策略示例

## 目录结构

```text
自动交易软件/
  .env.example
  pyproject.toml
  requirements.txt
  src/autotrader_app/
```

## 安装

```bash
cd /root/自动交易软件
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -r requirements.txt
pip install -e .
cp .env.example .env
```

如果要使用 `Tushare Pro`，请在 `.env` 里填写：

```env
TUSHARE_TOKEN=你的token
DEFAULT_DATA_SOURCE=tushare
```

默认使用 `AKShare`。

## 启动 GUI

```bash
cd /root/自动交易软件
source .venv/bin/activate
python -m autotrader_app.main
```

## 当前能力

- 获取 A 股历史行情
- 模拟买入 / 卖出
- 记录持仓和订单到 SQLite
- 运行双均线回测
- 在 GUI 中查看行情、策略信号、订单和持仓

## 下一步建议

- 增加更多策略：MACD、RSI、布林带、突破策略
- 加入实盘接口适配层：`easytrader` / 券商 SDK
- 增加账户权益曲线、K 线图、回测统计页
- 把 `schedule` 接到自动扫描和自动下单任务
