"""回测报告导出工具。

支持将回测结果导出为 CSV 数据文件和 PDF 报告。
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


@dataclass
class BacktestReport:
    """回测报告数据类。"""

    # ── 基本信息 ─────────────────────────────────────────
    symbol: str = ""
    strategy_name: str = ""
    start_date: str = ""
    end_date: str = ""
    parameters: dict[str, Any] = field(default_factory=dict)

    # ── 收益指标 ─────────────────────────────────────────
    starting_cash: float = 100_000.0
    final_value: float = 100_000.0
    total_pnl: float = 0.0
    total_return_pct: float = 0.0
    annual_return_pct: float = 0.0
    max_drawdown_pct: float = 0.0

    # ── 交易统计 ─────────────────────────────────────────
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0
    avg_win_pct: float = 0.0
    avg_loss_pct: float = 0.0
    profit_factor: float = 0.0
    sharpe_ratio: float = 0.0

    # ── 持仓记录 ─────────────────────────────────────────
    trades: list[dict] = field(default_factory=list)
    equity_curve: list[dict] = field(default_factory=list)


def compute_report(
    trades: list[dict],
    equity_curve: pd.DataFrame | list[dict],
    starting_cash: float,
    symbol: str = "",
    strategy_name: str = "",
    parameters: dict[str, Any] | None = None,
) -> BacktestReport:
    """从交易记录和权益曲线计算完整回测报告。

    trades 每项字段: entry_date, exit_date, direction, entry_price, exit_price,
                     quantity, pnl, pnl_pct
    equity_curve 为 DataFrame(created_at, total_assets) 或同等 dict 列表
    """
    report = BacktestReport(
        symbol=symbol,
        strategy_name=strategy_name,
        starting_cash=starting_cash,
        parameters=parameters or {},
    )

    if isinstance(equity_curve, pd.DataFrame):
        ec = equity_curve
    elif isinstance(equity_curve, list) and equity_curve:
        ec = pd.DataFrame(equity_curve)
    else:
        ec = pd.DataFrame()

    # ── 收益指标 ──────────────────────────────────────────
    report.final_value = float(ec["total_assets"].iloc[-1]) if not ec.empty else starting_cash
    report.total_pnl = report.final_value - starting_cash
    report.total_return_pct = (report.total_pnl / starting_cash) * 100

    # 年化收益率
    if not ec.empty and len(ec) > 1:
        days = (pd.to_datetime(ec["created_at"].iloc[-1]) - pd.to_datetime(ec["created_at"].iloc[0])).days
        if days > 0:
            report.annual_return_pct = ((report.final_value / starting_cash) ** (365.0 / days) - 1) * 100
        # 最大回撤
        equity = ec["total_assets"].astype(float).values
        peak = np.maximum.accumulate(equity)
        dd = (peak - equity) / peak * 100
        report.max_drawdown_pct = float(np.max(dd)) if len(dd) > 0 else 0.0

    # ── 交易统计 ──────────────────────────────────────────
    report.trades = trades
    report.total_trades = len(trades)

    if trades:
        winning = [t for t in trades if t.get("pnl", 0) > 0]
        losing = [t for t in trades if t.get("pnl", 0) <= 0]
        report.winning_trades = len(winning)
        report.losing_trades = len(losing)
        report.win_rate = (len(winning) / len(trades) * 100) if trades else 0

        if winning:
            report.avg_win_pct = float(np.mean([t.get("pnl_pct", 0) for t in winning]))
        if losing:
            report.avg_loss_pct = float(np.mean([t.get("pnl_pct", 0) for t in losing]))

        # 盈亏比
        total_win = sum(t.get("pnl", 0) for t in winning)
        total_loss = abs(sum(t.get("pnl", 0) for t in losing))
        report.profit_factor = total_win / total_loss if total_loss > 0 else 0.0

        # 夏普比率（简化：用每笔交易收益率计算）
        returns = [t.get("pnl_pct", 0) / 100 for t in trades]
        if len(returns) > 1 and np.std(returns) > 0:
            report.sharpe_ratio = float(np.mean(returns) / np.std(returns) * np.sqrt(252))

    if not ec.empty:
        report.start_date = str(pd.to_datetime(ec["created_at"].iloc[0]).strftime("%Y-%m-%d"))
        report.end_date = str(pd.to_datetime(ec["created_at"].iloc[-1]).strftime("%Y-%m-%d"))

    report.equity_curve = ec.to_dict("records") if not ec.empty else []
    return report


def export_report_csv(report: BacktestReport, path: str | Path) -> Path:
    """导出回测报告为 CSV 文件（含摘要 + 交易明细）。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)

        # 摘要
        writer.writerow(["回测报告", f"{report.symbol} - {report.strategy_name}"])
        writer.writerow(["生成时间", datetime.now().strftime("%Y-%m-%d %H:%M:%S")])
        writer.writerow([])
        writer.writerow(["指标", "值"])
        writer.writerow(["起始日期", report.start_date])
        writer.writerow(["结束日期", report.end_date])
        writer.writerow(["初始资金", f"{report.starting_cash:,.2f}"])
        writer.writerow(["最终价值", f"{report.final_value:,.2f}"])
        writer.writerow(["总盈亏", f"{report.total_pnl:+,.2f}"])
        writer.writerow(["总收益率", f"{report.total_return_pct:+.2f}%"])
        writer.writerow(["年化收益率", f"{report.annual_return_pct:+.2f}%"])
        writer.writerow(["最大回撤", f"{report.max_drawdown_pct:.2f}%"])
        writer.writerow(["交易次数", str(report.total_trades)])
        writer.writerow(["胜率", f"{report.win_rate:.1f}%"])
        writer.writerow(["平均盈利", f"{report.avg_win_pct:+.2f}%"])
        writer.writerow(["平均亏损", f"{report.avg_loss_pct:+.2f}%"])
        writer.writerow(["盈亏比", f"{report.profit_factor:.2f}"])
        writer.writerow(["夏普比率", f"{report.sharpe_ratio:.2f}"])
        writer.writerow([])

        # 参数
        writer.writerow(["策略参数"])
        for k, v in report.parameters.items():
            writer.writerow([k, str(v)])
        writer.writerow([])

        # 交易明细
        if report.trades:
            writer.writerow(["交易明细"])
            writer.writerow(["#", "买入日期", "卖出日期", "方向", "买入价", "卖出价", "数量", "盈亏", "盈亏%"])
            for i, t in enumerate(report.trades, 1):
                writer.writerow([
                    i,
                    t.get("entry_date", ""),
                    t.get("exit_date", ""),
                    t.get("direction", ""),
                    f"{t.get('entry_price', 0):.2f}",
                    f"{t.get('exit_price', 0):.2f}",
                    t.get("quantity", 0),
                    f"{t.get('pnl', 0):+.2f}",
                    f"{t.get('pnl_pct', 0):+.2f}%",
                ])

    return path


def generate_report_html(report: BacktestReport) -> str:
    """生成 HTML 格式的回测报告（可用于打印/导出 PDF）。"""
    color_positive = "#22c55e"
    color_negative = "#ef4444"
    pnl_color = color_positive if report.total_pnl >= 0 else color_negative

    trades_rows = ""
    for i, t in enumerate(report.trades, 1):
        pnl = t.get("pnl", 0)
        color = color_positive if pnl >= 0 else color_negative
        trades_rows += f"""<tr>
            <td>{i}</td>
            <td>{t.get('entry_date', '')}</td>
            <td>{t.get('exit_date', '')}</td>
            <td>{t.get('direction', '')}</td>
            <td>{t.get('entry_price', 0):.2f}</td>
            <td>{t.get('exit_price', 0):.2f}</td>
            <td>{t.get('quantity', 0)}</td>
            <td style="color:{color}">{pnl:+.2f}</td>
            <td style="color:{color}">{t.get('pnl_pct', 0):+.2f}%</td>
        </tr>"""

    params_rows = ""
    for k, v in report.parameters.items():
        params_rows += f"<tr><td>{k}</td><td>{v}</td></tr>"

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="utf-8"><title>回测报告 - {report.symbol}</title>
<style>
body {{ font-family: 'Microsoft YaHei', sans-serif; margin: 20px; color: #1f2937; }}
h1 {{ color: #111827; border-bottom: 2px solid #374151; padding-bottom: 8px; }}
h2 {{ color: #374151; margin-top: 24px; }}
table {{ border-collapse: collapse; width: 100%; margin: 8px 0; }}
th, td {{ border: 1px solid #d1d5db; padding: 6px 10px; text-align: center; font-size: 13px; }}
th {{ background: #f3f4f6; font-weight: bold; }}
.summary {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; margin: 12px 0; }}
.card {{ background: #f9fafb; border: 1px solid #e5e7eb; border-radius: 6px; padding: 12px; text-align: center; }}
.card .value {{ font-size: 20px; font-weight: bold; }}
.card .label {{ font-size: 11px; color: #6b7280; }}
.footer {{ margin-top: 24px; font-size: 11px; color: #9ca3af; text-align: center; }}
</style></head>
<body>
<h1>📊 回测报告</h1>
<p style="color:#6b7280;">{report.symbol} — {report.strategy_name} | {report.start_date} ~ {report.end_date}</p>

<div class="summary">
<div class="card"><div class="value" style="color:{pnl_color}">{report.total_return_pct:+.2f}%</div><div class="label">总收益率</div></div>
<div class="card"><div class="value" style="color:{'#ef4444' if report.max_drawdown_pct > 5 else '#6b7280'}">{report.max_drawdown_pct:.2f}%</div><div class="label">最大回撤</div></div>
<div class="card"><div class="value">{report.win_rate:.1f}%</div><div class="label">胜率</div></div>
<div class="card"><div class="value">{report.sharpe_ratio:.2f}</div><div class="label">夏普比率</div></div>
<div class="card"><div class="value">{report.total_trades}</div><div class="label">交易次数</div></div>
<div class="card"><div class="value">{report.profit_factor:.2f}</div><div class="label">盈亏比</div></div>
<div class="card"><div class="value">{report.starting_cash:,.0f}</div><div class="label">初始资金</div></div>
<div class="card"><div class="value" style="color:{pnl_color}">{report.final_value:,.0f}</div><div class="label">最终价值</div></div>
</div>

<h2>策略参数</h2>
<table>{"".join(params_rows) if params_rows else "<tr><td colspan=2>无</td></tr>"}</table>

<h2>交易明细 ({report.total_trades} 笔)</h2>
<table><thead><tr><th>#</th><th>买入日期</th><th>卖出日期</th><th>方向</th><th>买入价</th><th>卖出价</th><th>数量</th><th>盈亏</th><th>盈亏%</th></tr></thead>
<tbody>{"".join(trades_rows) if trades_rows else "<tr><td colspan=9>无交易记录</td></tr>"}</tbody></table>

<div class="footer">生成时间: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")} | A股自动交易系统</div>
</body></html>"""


def export_report_html(report: BacktestReport, path: str | Path) -> Path:
    """导出回测报告为 HTML 文件（可用浏览器打开/打印 PDF）。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(generate_report_html(report), encoding="utf-8")
    return path
