"""EasyTrader 实盘连接测试脚本（交互式）。

使用方法（Windows）：
    .venv/Scripts/activate
    python tests/test_easytrader_login.py

逐步引导测试：
  1. 加载配置
  2. 连接券商客户端
  3. 查询账户资金
  4. 查询持仓
  5. 查询最新价
  6. 模拟下单（不下真单）

安全说明：
  · 所有下单操作均在 is_live=False 模式下执行
  · 所有查询操作仅读取数据，不发送任何交易指令
  · 每个测试步骤都需要用户确认后才执行
"""
from __future__ import annotations

import sys
from pathlib import Path

# 确保项目根目录在 Python 路径中
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# 确保 src 也在路径中（package 模式）
_SRC_DIR = _PROJECT_ROOT / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))


def _print_sep(title: str = "") -> None:
    """打印分隔线。"""
    width = 70
    if title:
        side = (width - len(title) - 2) // 2
        print(f"\n{'=' * side} {title} {'=' * (width - side - len(title) - 2)}")
    else:
        print("=" * width)


def _confirm(prompt: str, default: bool = True) -> bool:
    """等待用户确认。

    Args:
        prompt: 提示信息。
        default: 默认行为（True=回车继续，False=回车跳过）。

    Returns:
        True 继续，False 跳过。
    """
    hint = "Y/n" if default else "y/N"
    try:
        resp = input(f"  ▶ {prompt} [{hint}]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print("\n  ⚠️  用户中断，退出测试。")
        sys.exit(0)

    if default:
        return resp not in ("n", "no")
    return resp in ("y", "yes")


def step_1_load_config() -> tuple[str, str, str, str]:
    """Step 1: 加载并显示 EasyTrader 配置。"""
    _print_sep("Step 1 / 6 — 加载配置")

    from autotrader_app.config import get_settings

    settings = get_settings()
    print(f"\n  BROKER_TYPE            = {settings.broker_type!r}")
    print(f"  EASYTRADER_BROKER_TYPE  = {settings.easytrader_broker_type!r}")
    print(f"  EASYTRADER_ACCOUNT      = {settings.easytrader_account!r}")
    print(f"  EASYTRADER_EXE_PATH     = {settings.easytrader_exe_path!r}")
    print(f"  IS_LIVE (from .env)     = {settings.is_live_trading!r}")

    errors: list[str] = []
    if not settings.easytrader_account:
        errors.append("  ❌ EASYTRADER_ACCOUNT 未设置")
    if not settings.easytrader_exe_path:
        errors.append("  ❌ EASYTRADER_EXE_PATH 未设置")

    if errors:
        print("\n" + "\n".join(errors))
        print("\n  ⚠️  请先在 .env 中填写配置，或通过 GUI 菜单 配置 → Broker 页面设置。")

    if settings.is_live_trading:
        print("\n  ⚠️  BROKER_TYPE=easytrader 表示实盘模式已配置。")
        print("  ⚠️  本测试将使用 is_live=False，不会实际下单。")

    return (
        settings.easytrader_broker_type or "ht",
        settings.easytrader_account,
        settings.easytrader_password,
        settings.easytrader_exe_path,
    )


def step_2_connect(broker_type: str, account: str, password: str, exe_path: str) -> any:
    """Step 2: 创建 EasyTraderBroker 并连接客户端。"""
    _print_sep("Step 2 / 6 — 连接券商客户端")

    print("\n  ⚠️  请在执行此步骤前确认：")
    print(f"  • 券商客户端已打开并手动登录（{broker_type}）")
    print(f"  • 客户端路径：{exe_path}")
    print(f"  • 账号：{account}")

    if not _confirm("是否继续连接客户端？", default=True):
        print("  ⏭ 跳过连接，无法进行后续测试。")
        return None

    from autotrader_app.broker.easytrader_broker import EasyTraderBroker

    print("\n  ⏳ 正在创建 EasyTraderBroker 实例（is_live=False）...")
    broker = EasyTraderBroker(is_live=False)
    print(f"  ✓ Broker 实例创建完成，is_live={broker.is_live}")

    print(f"\n  ⏳ 正在连接客户端（{broker_type}）...")
    try:
        success = broker.login(
            account=account or "",
            password=password or "",
            exe_path=exe_path or "",
            broker_type=broker_type,
        )
    except RuntimeError as exc:
        print(f"\n  ❌ easytrader 导入失败：{exc}")
        print("  💡 请执行: pip install easytrader pywinauto")
        return None
    except Exception as exc:
        print(f"\n  ❌ 连接过程发生异常：{exc}")
        print("  💡 请检查：")
        print("     1. 券商客户端是否已打开并登录")
        print(f"     2. exe 路径是否正确：{exe_path}")
        print("     3. 券商类型是否正确：" + broker_type)
        return None

    if success:
        print("\n  ✅ 客户端连接成功！已进入实盘查询模式（is_live=False，不会真实下单）")
        return broker
    else:
        print("\n  ❌ 客户端连接失败")
        print("  💡 请检查：")
        print("     1. 券商客户端是否已启动并保持登录状态")
        print("     2. exe 路径是否正确")
        print("     3. 是否有多个客户端进程在运行（关闭多余的实例）")
        print("     4. 券商类型是否正确（ht/yjb/gf 等）")
        return None


def step_3_query_account(broker: any) -> None:
    """Step 3: 查询账户资金。"""
    _print_sep("Step 3 / 6 — 查询账户资金")

    if broker is None:
        print("  ⚠️  客户端未连接，跳过查询。")
        return

    if not _confirm("是否查询账户资金信息？", default=True):
        print("  ⏭ 跳过查询。")
        return

    print("  ⏳ 正在查询账户...")
    account = broker.get_account()

    print(f"\n  📊 账户资金")
    print(f"     可用资金：    {account.get('cash', 0):>12,.2f}")
    print(f"     持仓市值：    {account.get('market_value', 0):>12,.2f}")
    print(f"     总资产：      {account.get('total_assets', 0):>12,.2f}")

    total = account.get("total_assets", 0)
    if total == 0:
        print("\n  ⚠️  总资产为 0，可能原因：")
        print("     1. 客户端连接异常，数据读取失败")
        print("     2. 账户确实为空（新账户或已清仓）")
        print("     3. 当天未登录客户端或未初始化数据")
        print("  💡 建议：检查客户端窗口是否正常显示数据")


def step_4_query_positions(broker: any) -> None:
    """Step 4: 查询持仓。"""
    _print_sep("Step 4 / 6 — 查询持仓")

    if broker is None:
        print("  ⚠️  客户端未连接，跳过查询。")
        return

    if not _confirm("是否查询当前持仓？", default=True):
        print("  ⏭ 跳过查询。")
        return

    import pandas as pd

    print("  ⏳ 正在查询持仓...")
    try:
        positions = broker.get_positions()
    except Exception as exc:
        print(f"\n  ❌ 查询持仓异常：{exc}")
        print("  💡 客户端可能未完全就绪，请确认客户端窗口已打开")
        return

    if positions is None or positions.empty:
        print("\n  📊 当前持仓：空（无持仓或数据未就绪）")
        print("  💡 如果预期有持仓但显示为空：")
        print("     1. 确认客户端已成功登录并显示了持仓数据")
        print("     2. 尝试重新调用 broker.login()")
        return

    print(f"\n  📊 当前持仓（共 {len(positions)} 只股票）\n")
    print(f"  {'代码':<10} {'数量':<10} {'均价':<12}")
    print(f"  {'-'*8}  {'-'*8}  {'-'*10}")

    for _, row in positions.iterrows():
        symbol = str(row.get("symbol", "?"))
        qty = int(row.get("quantity", 0))
        price = float(row.get("avg_price", 0))
        print(f"  {symbol:<10} {qty:<10} {price:<12.3f}")


def step_5_query_price(broker: any) -> None:
    """Step 5: 查询股票最新价。"""
    _print_sep("Step 5 / 6 — 查询最新价")

    if broker is None:
        print("  ⚠️  客户端未连接，跳过查询。")
        return

    if not _confirm("是否查询单只股票最新价？", default=True):
        print("  ⏭ 跳过查询。")
        return

    symbol = input("  ▶ 请输入股票代码（例如 000001）：").strip()
    if not symbol:
        symbol = "000001"
        print(f"  ⏩ 输入为空，使用默认代码 {symbol}")

    print(f"  ⏳ 正在查询 {symbol} 最新价...")
    try:
        price = broker.get_latest_price(symbol)
    except Exception as exc:
        print(f"\n  ❌ 查询最新价异常：{exc}")
        print("  💡 部分券商客户端不支持通过 get_latest_price 查询行情")
        print("  💡 行情数据仍可通过 AKShare 自动获取，不影响交易")
        return

    if price is not None and price > 0:
        print(f"\n  📊 {symbol} 最新价：{price:.3f}")
    else:
        print(f"\n  ⚠️  无法获取 {symbol} 最新价")
        print("  💡 部分券商客户端不支持此查询，这是正常的")
        print("  💡 行情数据将通过 AKShare 获取，不影响交易功能")


def step_6_simulate_order(broker: any) -> None:
    """Step 6: 模拟下单（is_live=False，不下真单）。"""
    _print_sep("Step 6 / 6 — 模拟下单（不发送真实委托）")

    if broker is None:
        print("  ⚠️  客户端未连接，跳过模拟下单。")
        return

    print("\n  🔒 is_live=False，本次操作仅打印日志，不会发送真实委托！")

    if not _confirm("是否进行模拟买入测试？", default=True):
        print("  ⏭ 跳过模拟下单。")
        return

    from autotrader_app.models import OrderRequest, OrderSide, OrderType

    symbol = input("  ▶ 请输入股票代码（例如 000001）：").strip()
    if not symbol:
        symbol = "000001"
        print(f"  ⏩ 输入为空，使用默认代码 {symbol}")

    print(f"\n  ⏳ 模拟下单：买入 {symbol} 100 股（市价单）...")
    order = OrderRequest(
        symbol=symbol,
        side=OrderSide.BUY,
        quantity=100,
        price=0.0,
        order_type=OrderType.MARKET,
        strategy_name="manual_test",
    )

    try:
        result, fills = broker.place_order(order)
    except Exception as exc:
        print(f"\n  ❌ 模拟下单异常：{exc}")
        return

    print(f"\n  📄 订单结果")
    print(f"     订单号：   {result.order_id}")
    print(f"     状态：     {result.status.value}")
    print(f"     原因：     {result.reason}")
    print(f"     成交笔数： {len(fills)}")

    if result.status.value == "FILLED":
        print("\n  ✅ 模拟下单成功（is_live=False，未发送真实委托）")
    else:
        print(f"\n  ⚠️  下单状态非预期：{result.status.value}")
        print("  💡 这通常是因为 is_live=False，下单已被拦截")


def main() -> None:
    """主入口：逐步执行所有测试。"""
    _print_sep("EasyTrader 实盘连接测试脚本")
    print("""
  本脚本将测试 EasyTrader 与券商客户端的连接。

  安全声明：
  · 所有下单操作在 is_live=False 模式下执行
  · 不会向券商发送任何真实委托
  · 每步操作需要用户确认后才会执行

  准备条件：
  1. Windows 系统（easytrader 仅支持 Windows）
  2. 券商客户端已安装并手动登录
  3. .env 文件已正确配置

  按 Enter 开始测试，或 Ctrl+C 退出。
        """)

    try:
        input("  ▶ 按 Enter 键开始测试...")
    except (EOFError, KeyboardInterrupt):
        print("\n  ⚠️  用户中断，退出测试。")
        sys.exit(0)

    # ── 执行步骤 ────────────────────────────────────────────
    broker_type, account, password, exe_path = step_1_load_config()

    if not _confirm("是否继续连接测试？", default=True):
        print("\n  ⏭ 用户选择退出测试。")
        return

    broker = step_2_connect(broker_type, account, password, exe_path)

    step_3_query_account(broker)
    step_4_query_positions(broker)
    step_5_query_price(broker)
    step_6_simulate_order(broker)

    # ── 完成 ────────────────────────────────────────────────
    _print_sep("测试完成")
    print(f"""
  测试摘要：
  {'✅ 客户端已连接' if broker and broker.is_connected else '❌ 客户端未连接'}
  {'✅ 账户查询已完成' if broker else '⏭ 已跳过'}
  {'✅ 持仓查询已完成' if broker else '⏭ 已跳过'}
  {'✅ 模拟下单已完成（未发送真单）' if broker else '⏭ 已跳过'}

  下一步：
  1. 确认查询结果正确后，可在 GUI 中通过配置界面切换 Broker 类型
  2. 先运行模拟交易验证策略逻辑
  3. 确认无误后再考虑启用 is_live=True
    """)


if __name__ == "__main__":
    main()
