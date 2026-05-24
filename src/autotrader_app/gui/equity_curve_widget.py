"""权益曲线独立 Widget。

职责：给定净值历史 DataFrame + 成交记录，完成绘图和绩效指标展示。
外部只需调用一个方法即可完成所有刷新：

    widget = EquityCurveWidget(initial_cash=100_000.0, parent=self)
    widget.update_equity_data(equity_df, fills_df=fills_df, current_assets=broker.account.total_assets)

与 MockBroker / MainWindow 零耦合，可单独测试和复用。
"""
from __future__ import annotations

from datetime import datetime

import matplotlib.dates as mdates
import pandas as pd
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT
from matplotlib.figure import Figure
from PyQt6.QtWidgets import (
    QGridLayout,
    QGroupBox,
    QLabel,
    QVBoxLayout,
    QWidget,
)


class EquityCurveWidget(QWidget):
    """权益净值曲线组件。

    布局（从上到下）：
    ┌──────────────────────────────┐
    │  绩效指标（6 格网格）         │
    ├──────────────────────────────┤
    │  NavigationToolbar（缩放/平移）│
    ├──────────────────────────────┤
    │  matplotlib 净值曲线图        │
    └──────────────────────────────┘

    视觉风格：暗色背景（#111827），与 K 线图保持一致。

    信号说明：
    · 净值 = total_assets / initial_cash（以 1.0 为起点）
    · 净值 ≥ 1.0 时绿色填充；< 1.0 时红色填充
    · 灰色虚线为 1.0 基准线（初始净值）
    """

    # ── 调色盘（与 K 线图暗色主题保持一致）─────────────
    _BG       = "#111827"
    _GRID_CLR = "#374151"
    _TEXT     = "#e5e7eb"
    _GREEN    = "#22c55e"
    _RED      = "#ef4444"
    _MUTED    = "#6b7280"
    _LABEL_STYLE = "color:#9ca3af; font-size:11px;"
    _VALUE_STYLE = "font-weight:bold; font-size:12px;"

    def __init__(
        self,
        initial_cash: float = 100_000.0,
        parent: QWidget | None = None,
    ) -> None:
        """初始化组件。

        Args:
            initial_cash: 初始资金，作为净值归一化基准（可通过 update_equity_data 动态更新）。
            parent:       父 QWidget。
        """
        super().__init__(parent)
        self._initial_cash: float = initial_cash
        # 缓存最近一次传入的数据，方便 resize 时重绘
        self._equity_df: pd.DataFrame = pd.DataFrame()

        # ── matplotlib 画布 ───────────────────────────────
        self._figure = Figure(figsize=(6, 3.5), tight_layout=True)
        self._figure.patch.set_facecolor(self._BG)
        self._canvas = FigureCanvasQTAgg(self._figure)
        self._ax = self._figure.add_subplot(111)
        self._ax.set_facecolor(self._BG)

        # ── 缩放/平移/保存工具栏 ──────────────────────────
        self._toolbar = NavigationToolbar2QT(self._canvas, self)

        # ── 绩效指标标签（6 个值域）──────────────────────
        self._lbl_total_return   = QLabel("--")   # 总收益率
        self._lbl_today_pnl      = QLabel("--")   # 今日盈亏
        self._lbl_max_drawdown   = QLabel("--")   # 最大回撤
        self._lbl_win_rate       = QLabel("--")   # 胜率
        self._lbl_trade_count    = QLabel("--")   # 交易次数
        self._lbl_current_assets = QLabel("--")   # 当前总资产

        self._build_layout()
        self._draw_placeholder()   # 启动时先显示占位符

    # ─────────────────────────────────────────────────────
    # 公开 API
    # ─────────────────────────────────────────────────────

    def update_equity_data(
        self,
        equity_df: pd.DataFrame,
        initial_cash: float | None = None,
        fills_df: pd.DataFrame | None = None,
        current_assets: float | None = None,
    ) -> None:
        """一次调用完成所有刷新：重绘曲线 + 更新绩效指标。

        Args:
            equity_df:      账户净值历史（含 created_at / total_assets 列），
                            由 MockBroker.get_equity_history() 提供。
            initial_cash:   初始资金；None 时沿用上次的值。
            fills_df:       成交记录（含 symbol / side / quantity / price / filled_at），
                            由 MockBroker.get_fills() 提供；None 时跳过胜率计算。
            current_assets: 当前实时总资产（broker.account.total_assets）；
                            None 时从 equity_df 末行读取。
        """
        if initial_cash is not None:
            self._initial_cash = initial_cash

        self._equity_df = equity_df

        # 当前总资产：优先使用传入值，其次取历史末行
        if current_assets is None:
            current_assets = (
                float(equity_df["total_assets"].iloc[-1])
                if not equity_df.empty
                else self._initial_cash
            )

        self._draw_chart()
        self._update_stats(current_assets, fills_df)

    # ─────────────────────────────────────────────────────
    # 静态工具：FIFO 胜率计算（可独立复用）
    # ─────────────────────────────────────────────────────

    @staticmethod
    def compute_trade_stats(fills_df: pd.DataFrame) -> tuple[int, int]:
        """用 FIFO 配对法统计盈利交易次数和总交易次数。

        算法：
        1. 按时间排序所有成交；
        2. 每笔 BUY 入队；
        3. 每笔 SELL 从队首出仓（FIFO），计算本次卖出的平均成本；
        4. 若 sell_price > avg_cost → win。

        正确处理部分平仓（一次买 200 分两次卖 100 的情形）。

        Returns:
            (wins, total)  ——  盈利平仓次数 / 总平仓次数
        """
        if fills_df is None or fills_df.empty:
            return 0, 0

        df = fills_df.sort_values("filled_at").copy()
        # 每个股票的未平仓买入批次：[(qty, price), ...]
        open_lots: dict[str, list[tuple[int, float]]] = {}
        wins = total = 0

        for _, row in df.iterrows():
            symbol = str(row["symbol"])
            qty    = int(row["quantity"])
            price  = float(row["price"])
            side   = str(row["side"])

            if side == "BUY":
                # 买入：压入该股票的 FIFO 队列
                open_lots.setdefault(symbol, []).append((qty, price))

            elif side == "SELL":
                queue = open_lots.get(symbol, [])
                if not queue:
                    continue  # 无对应买入记录，跳过

                # 出仓：FIFO 消耗买入批次，累计平均成本
                remaining = qty
                cost_sum  = 0.0
                cost_qty  = 0

                while remaining > 0 and queue:
                    lot_qty, lot_price = queue[0]
                    take = min(remaining, lot_qty)
                    cost_sum += take * lot_price
                    cost_qty += take
                    remaining -= take
                    if take == lot_qty:
                        queue.pop(0)         # 该批次已全部出仓
                    else:
                        queue[0] = (lot_qty - take, lot_price)  # 部分出仓

                if cost_qty > 0:
                    avg_cost = cost_sum / cost_qty
                    pnl = (price - avg_cost) * cost_qty
                    total += 1
                    if pnl > 0:
                        wins += 1

        return wins, total

    # ─────────────────────────────────────────────────────
    # 私有：布局搭建
    # ─────────────────────────────────────────────────────

    def _build_layout(self) -> None:
        """搭建绩效网格 + 工具栏 + matplotlib 画布的垂直布局。"""
        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(4)

        # ── 绩效指标网格（3 行 × 2 列）───────────────────
        stats_box = QGroupBox("绩效指标")
        grid = QGridLayout()
        grid.setSpacing(6)
        grid.setContentsMargins(8, 4, 8, 4)

        stat_pairs = [
            ("总收益率",   self._lbl_total_return),
            ("今日盈亏",   self._lbl_today_pnl),
            ("最大回撤",   self._lbl_max_drawdown),
            ("胜率",       self._lbl_win_rate),
            ("交易次数",   self._lbl_trade_count),
            ("当前总资产", self._lbl_current_assets),
        ]
        for i, (text, value_lbl) in enumerate(stat_pairs):
            name_lbl = QLabel(text)
            name_lbl.setStyleSheet(self._LABEL_STYLE)
            value_lbl.setStyleSheet(self._VALUE_STYLE)
            row, col = divmod(i, 2)           # 每行 2 列
            grid.addWidget(name_lbl,  row, col * 2)
            grid.addWidget(value_lbl, row, col * 2 + 1)

        stats_box.setLayout(grid)
        root.addWidget(stats_box)

        # ── 工具栏 + 画布 ─────────────────────────────────
        root.addWidget(self._toolbar)
        root.addWidget(self._canvas, stretch=1)

    # ─────────────────────────────────────────────────────
    # 私有：净值曲线绘制
    # ─────────────────────────────────────────────────────

    def _draw_chart(self) -> None:
        """根据 self._equity_df 重绘净值曲线。"""
        ax = self._ax
        ax.clear()
        ax.set_facecolor(self._BG)

        df = self._equity_df
        if df.empty or self._initial_cash <= 0:
            self._draw_placeholder()
            return

        times  = pd.to_datetime(df["created_at"])
        equity = df["total_assets"].astype(float)
        nav    = equity / self._initial_cash     # 归一化：起点 ≈ 1.0

        # 末值颜色：盈利绿，亏损红
        final_color = (
            self._GREEN if float(equity.iloc[-1]) >= self._initial_cash
            else self._RED
        )

        # ── 净值曲线主线 ──────────────────────────────────
        ax.plot(
            times, nav,
            color=final_color, linewidth=1.6, zorder=4, label="净值",
        )

        # ── 面积填充：上涨段绿色，下跌段红色 ─────────────
        ax.fill_between(
            times, 1.0, nav,
            where=(nav >= 1.0),
            color=self._GREEN, alpha=0.12, zorder=2,
        )
        ax.fill_between(
            times, 1.0, nav,
            where=(nav < 1.0),
            color=self._RED, alpha=0.15, zorder=2,
        )

        # ── 基准线（净值=1.0 对应初始资金）───────────────
        ax.axhline(
            1.0,
            color=self._MUTED, linewidth=0.9,
            linestyle="--", alpha=0.7, label="初始净值",
        )

        # ── 末端净值数值标注 ──────────────────────────────
        last_nav = float(nav.iloc[-1])
        ax.annotate(
            f"  {last_nav:.4f}",
            xy=(times.iloc[-1], last_nav),
            color=final_color, fontsize=9, va="center",
        )

        # ── X 轴时间格式：按跨度自动选择精度 ──────────────
        span_s = (times.iloc[-1] - times.iloc[0]).total_seconds()
        if span_s < 86_400:                      # 日内（< 1 天）
            x_fmt = mdates.DateFormatter("%H:%M")
        elif span_s < 86_400 * 30:               # 近 30 天内
            x_fmt = mdates.DateFormatter("%m-%d %H:%M")
        else:                                     # 跨月
            x_fmt = mdates.DateFormatter("%Y-%m-%d")

        ax.xaxis.set_major_formatter(x_fmt)
        ax.xaxis.set_major_locator(
            mdates.AutoDateLocator(minticks=4, maxticks=8)
        )
        for tick_lbl in ax.get_xticklabels():
            tick_lbl.set_rotation(25)
            tick_lbl.set_ha("right")
            tick_lbl.set_color(self._TEXT)

        # ── 全局样式 ──────────────────────────────────────
        ax.set_ylabel("净值", color=self._TEXT, fontsize=9)
        ax.tick_params(colors=self._TEXT, labelsize=8)
        for spine in ax.spines.values():
            spine.set_color(self._GRID_CLR)
        ax.grid(True, color=self._GRID_CLR, linewidth=0.5, alpha=0.5)
        ax.legend(
            facecolor=self._BG, edgecolor=self._GRID_CLR,
            labelcolor=self._TEXT, fontsize=8, loc="upper left",
        )

        self._figure.tight_layout(pad=1.2)
        self._canvas.draw()

    def _draw_placeholder(self) -> None:
        """无数据时显示占位提示（首次启动 / 尚未有成交）。"""
        ax = self._ax
        ax.clear()
        ax.set_facecolor(self._BG)
        ax.text(
            0.5, 0.5,
            "暂无权益数据\n发生成交后自动更新",
            ha="center", va="center",
            color=self._MUTED, fontsize=11,
            transform=ax.transAxes,
        )
        ax.set_xticks([])
        ax.set_yticks([])
        for spine in ax.spines.values():
            spine.set_color(self._GRID_CLR)
        self._canvas.draw()

    # ─────────────────────────────────────────────────────
    # 私有：绩效指标标签更新
    # ─────────────────────────────────────────────────────

    def _update_stats(
        self,
        current_assets: float,
        fills_df: pd.DataFrame | None,
    ) -> None:
        """刷新全部绩效指标标签。"""
        initial = self._initial_cash
        df      = self._equity_df

        # ── 当前总资产 ────────────────────────────────────
        self._lbl_current_assets.setText(f"{current_assets:,.2f}")
        self._set_label_pnl_color(self._lbl_current_assets, current_assets - initial)

        # 若无历史数据，其余指标清空
        if df.empty:
            for lbl in (
                self._lbl_total_return, self._lbl_today_pnl,
                self._lbl_max_drawdown, self._lbl_win_rate,
                self._lbl_trade_count,
            ):
                lbl.setText("--")
            return

        equity = df["total_assets"].astype(float)

        # ── 总收益率 ──────────────────────────────────────
        ret_pct = (current_assets - initial) / initial * 100
        self._lbl_total_return.setText(f"{ret_pct:+.2f}%")
        self._set_label_pnl_color(self._lbl_total_return, ret_pct)

        # ── 今日盈亏（今日首条快照 vs 当前）──────────────
        today    = datetime.now().date()
        df_today = df[pd.to_datetime(df["created_at"]).dt.date == today]
        day_start = (
            float(df_today["total_assets"].iloc[0])
            if len(df_today) >= 1 else initial
        )
        today_pnl = current_assets - day_start
        self._lbl_today_pnl.setText(f"{today_pnl:+,.2f}")
        self._set_label_pnl_color(self._lbl_today_pnl, today_pnl)

        # ── 最大回撤（净值序列滚动最大值法）──────────────
        nav     = equity / initial
        max_dd  = float(((nav - nav.cummax()) / nav.cummax()).min()) * 100
        self._lbl_max_drawdown.setText(f"{max_dd:.2f}%")
        # 超过 0.5% 回撤才染红，避免微小波动持续高亮
        self._lbl_max_drawdown.setStyleSheet(
            "font-weight:bold; font-size:12px; color:#ef4444;"
            if max_dd < -0.5
            else self._VALUE_STYLE
        )

        # ── 胜率 / 交易次数（FIFO 配对）──────────────────
        if fills_df is not None and not fills_df.empty:
            wins, total = self.compute_trade_stats(fills_df)
            win_rate = wins / total * 100 if total > 0 else 0.0
            self._lbl_win_rate.setText(
                f"{win_rate:.1f}%  ({wins}/{total})" if total else "--"
            )
            self._lbl_trade_count.setText(str(total) if total else "--")
            # 胜率以 50% 为分界染色
            self._set_label_pnl_color(self._lbl_win_rate, win_rate - 50)
        else:
            self._lbl_win_rate.setText("--")
            self._lbl_trade_count.setText("--")

    @staticmethod
    def _set_label_pnl_color(label: QLabel, value: float) -> None:
        """正值绿色、负值红色、零值默认色。"""
        base = "font-weight:bold; font-size:12px;"
        if value > 0:
            label.setStyleSheet(base + " color:#22c55e;")
        elif value < 0:
            label.setStyleSheet(base + " color:#ef4444;")
        else:
            label.setStyleSheet(base)
