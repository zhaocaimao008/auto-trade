"""EasyTrader 实盘下单测试脚本（谨慎版）。

⚠️  本脚本用于在实盘环境下测试 easytrader 下单功能。

安全设计：
  · 默认 is_live=False，需要用户手动确认后才执行真单
  · 股票代码和价格由用户输入，不下预设单
  · 首次测试建议输入 100 股的低价股票
  · 每一步都需要用户确认
  · 可在任意步骤按 Ctrl+C 终止

使用方式（Windows）：
    .venv\Scripts\activate
    python tests/test_easytrader_order.py
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_SRC_DIR = _PROJECT_ROOT / "src"
for p in (_SRC_DIR, _PROJECT_ROOT):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))


def sep(title: str = "") -> None:
    width = 68
    if title:
        side = (width - len(title) - 2) // 2
        print(f"\n{'=' * side} {title} {'=' * (width - side - len(title) - 2)}")
    else:
        print("=" * width)


def confirm(prompt: str, default: bool = False) -> bool:
    hint = "y/N" if not default else "Y/n"
    try:
        resp = input(f"  ▶ {prompt} [{hint}]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print("\n  ⚠️  用户中断，退出。")
        sys.exit(0)
    if default:
        return resp not in ("n", "no")
    return resp in ("y", "yes", "yes ")


# ════════════════════════════════════════════════════════════════
# 步骤 1: 配置加载
# ════════════════════════════════════════════════════════════════
sep("Step 1/5 — 加载配置")

from autotrader_app.config import get_settings

settings = get_settings()
print(f"\n  BROKER_TYPE            = {settings.broker_type!r}")
print(f"  EASYTRADER_BROKER_TYPE  = {settings.easytrader_broker_type!r}")
print(f"  EASYTRADER_ACCOUNT      = {settings.easytrader_account!r}")
print(f"  EASYTRADER_EXE_PATH     = {settings.easytrader_exe_path!r}")

if not settings.easytrader_account or not settings.easytrader_exe_path:
    print("\n  ❌ 配置不完整，请先在 .env 中填写。")
    sys.exit(1)

# ════════════════════════════════════════════════════════════════
# 步骤 2: 连接客户端
# ════════════════════════════════════════════════════════════════
sep("Step 2/5 — 连接客户端")

print("\n  ⚠️  请确认券商客户端已打开并手动登录。")
if not confirm("是否继续？", default=True):
    print("  退出。")
    sys.exit(0)

from autotrader_app.broker.easytrader_broker import EasyTraderBroker

broker = EasyTraderBroker(is_live=False)  # 先不下真单
print("  ⏳ 正在连接...")

try:
    success = broker.login(
        account=settings.easytrader_account,
        password=settings.easytrader_password,
        exe_path=settings.easytrader_exe_path,
        broker_type=settings.easytrader_broker_type or "ht",
    )
except Exception as exc:
    print(f"\n  ❌ 连接失败：{exc}")
    sys.exit(1)

if not success:
    print("\n  ❌ 登录返回 False。")
    print("  请检查客户端是否已启动并保持登录状态。")
    sys.exit(1)

print("  ✅ 客户端连接成功！")

# ════════════════════════════════════════════════════════════════
# 步骤 3: 查询确认
# ════════════════════════════════════════════════════════════════
sep("Step 3/5 — 查询确认")

print("\n  ⏳ 正在查询账户和持仓...")
account = broker.get_account()
positions = broker.get_positions()

print(f"\n  📊 账户资金")
print(f"     可用资金：{account.get('cash', 0):>10,.2f}")
print(f"     持仓市值：{account.get('market_value', 0):>10,.2f}")
print(f"     总资产：  {account.get('total_assets', 0):>10,.2f}")

if positions is not None and not positions.empty:
    print(f"\n  📊 持仓（{len(positions)} 只）")
    for _, row in positions.iterrows():
        print(f"     {row.get('symbol', '?'):>8}  x{int(row.get('quantity', 0))}  "
              f"@{float(row.get('avg_price', 0)):.2f}")
else:
    print("\n  📊 持仓：空")

print("\n  ✅ 查询正常。请确认以上数据与券商客户端显示一致。")
if not confirm("数据是否正确？", default=True):
    print("  请排查问题后再试。")
    sys.exit(1)

# ════════════════════════════════════════════════════════════════
# 步骤 4: 模拟下单（不下真单，验证链路完整）
# ════════════════════════════════════════════════════════════════
sep("Step 4/5 — 模拟下单（不下真单）")

from autotrader_app.models import OrderRequest, OrderSide, OrderType

print("\n  输入测试股票信息（仅链路测试，不会发送真实委托）")
symbol = input("  ▶ 股票代码（默认 000001）：").strip() or "000001"
price_input = input("  ▶ 委托价格（默认 1.00）：").strip()
price = float(price_input) if price_input else 1.00
qty_input = input("  ▶ 委托数量（默认 100）：").strip()
qty = int(qty_input) if qty_input else 100

print(f"\n  ⏳ 模拟买入委托：{symbol} x{qty} @ {price:.2f}（is_live=False，不下真单）")

order = OrderRequest(
    symbol=symbol,
    side=OrderSide.BUY,
    quantity=qty,
    price=price,
    order_type=OrderType.LIMIT,
    strategy_name="manual_test",
)

result, fills = broker.place_order(order)
print(f"\n  📄 模拟下单结果")
print(f"     订单号：{result.order_id}")
print(f"     状态：  {result.status.value}")
print(f"     原因：  {result.reason}")
print(f"     注意：  is_live=False，未发送真实委托")

if not confirm("\n是否确认模拟下单链路正常？", default=True):
    print("  请排查问题后再试。")
    sys.exit(1)

# ════════════════════════════════════════════════════════════════
# 步骤 5: 实盘下单（需要多次确认）
# ════════════════════════════════════════════════════════════════
sep("⚠️  Step 5/5 — 实盘下单（发送真实委托）")

print("""
  ┌─────────────────────────────────────────────────────────┐
  │  ⚠️  此步骤将向券商发送真实交易委托！                    │
  │                                                        │
  │  · 请确认账户资金充足                                  │
  │  · 请确认股票代码正确                                  │
  │  · 首单建议 100 股低价股                               │
  │  · 可随时在券商客户端手动撤单                          │
  └─────────────────────────────────────────────────────────┘
""")

if not confirm("是否进入实盘下单测试？（默认否）", default=False):
    print("\n  ⏭ 已取消实盘下单。")
    print("  测试完成。")
    broker.is_live = False
    sys.exit(0)

# 第二层确认：输入价格和数量
print("\n  ⚠️  以下信息将用于发送真实委托！")
real_symbol = input("  ▶ 股票代码（默认 000001）：").strip() or "000001"
real_price = float(input("  ▶ 委托价格（默认 1.00）：").strip() or "1.00")
real_qty = int(input("  ▶ 委托数量（默认 100，建议 100）：").strip() or "100")

# 第三层确认：显示完整委托信息
print(f"""
  ┌─────────────────────────────────────────────────────────┐
  │  即将发送真实委托：                                    │
  │    股票：  {real_symbol}
  │    方向：  买入
  │    数量：  {real_qty} 股
  │    价格：  {real_price:.2f} 元
  │    类型：  限价单
  │                                                        │
  │  这将会从你的账户扣除资金！                            │
  └─────────────────────────────────────────────────────────┘
""")

if not confirm("确认发送真实委托？（输入 y 确认，默认否）", default=False):
    print("\n  ⏭ 已取消实盘下单。")
    sys.exit(0)

# 第四层确认：最后的保险
double_check = input('\n  ▶ 最后确认，请输入 YES 发送委托：').strip()
if double_check != "YES":
    print("\n  ⏭ 输入不正确，已取消。")
    sys.exit(0)

# ── 执行实盘下单 ────────────────────────────────────────────
print("\n  ⏳ 正在发送真实委托...")

# 切换为实盘模式
broker.is_live = True

real_order = OrderRequest(
    symbol=real_symbol,
    side=OrderSide.BUY,
    quantity=real_qty,
    price=real_price,
    order_type=OrderType.LIMIT,
    strategy_name="manual_live_test",
)

try:
    result, fills = broker.place_order(real_order)
    print(f"\n  📄 实盘下单结果")
    print(f"     订单号：{result.order_id}")
    print(f"     状态：  {result.status.value}")
    print(f"     原因：  {result.reason}")
    print(f"     成交笔数：{len(fills)}")
except Exception as exc:
    print(f"\n  ❌ 下单异常：{exc}")
    broker.is_live = False  # 恢复安全模式
    sys.exit(1)

# 恢复安全模式
broker.is_live = False

# ── 完成 ────────────────────────────────────────────────────
sep("测试完成")

print(f"""
  请登录券商客户端确认委托状态：
  · 如果委托已提交，可在客户端中查到委托记录
  · 如果未成交，可手动撤单
  · 如果不想等待成交，请立即在客户端撤单

  风险提示：
  · is_live 已自动恢复为 False
  · 后续操作不会发送真单
  · 请妥善处理已提交的委托
""")
