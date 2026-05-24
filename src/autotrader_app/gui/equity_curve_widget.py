"""权益曲线独立 Widget（多策略对比增强版）。

职责：给定净值历史 DataFrame + 各策略净值序列，完成绘图和绩效指标展示。
外部只需调用一个方法即可完成所有刷新：

    widget = EquityCurveWidget(initial_cash=100_000.0, parent=self)
    widget.update_equity_data(
        equity_df,
        fills_df=fills_df,
        current_assets=broker.account.total_assets,
        strategy_series={"双均线策略": df1, "MACD趋势策略": df2},
    )

与 MockBroker / MainWindow 零耦合，可单独测试和复用。

多策略支持：
· 总权益曲线：白色粗线 + 绿/红填充（基线 1.0）
· 各策略净值：彩色虚线（无填充，基于已实现盈亏）
· 显示/隐藏 CheckBox：各策略独立控制
· 鼠标悬停十字线：竖线 + 弹出各系列 NAV 数值
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

import matplotlib.dates as mdates
import numpy as np
import pandas as pd
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT
from matplotlib.figure import Figure
from matplotlib.lines import Line2D
from PyQt6.QtWidgets import (
    QCheckBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)


class EquityCurveWidget(QWidget):
    """权益净值曲线组件（支持多策略对比）。

    布局（从上到下）：
    ┌──────────────────────────────────────┐
    │  绩效指标（6 格网格）                 │
    ├──────────────────────────────────────┤
    │  显示/隐藏 CheckBox 行（有策略时出现）│
    ├──────────────────────────────────────┤
    │  NavigationToolbar（缩放/平移/保存） │
    ├──────────────────────────────────────┤
    │  matplotlib 多线净值曲线图            │
    └──────────────────────────────────────┘

    视觉风格：暗色背景（#111827），与 K 线图主题一致。
    """

    # ── 调色盘 ─────────────────────────────────────────────
    _BG         = "#111827"
    _GRID_CLR   = "#374151"
    _TEXT       = "#e5e7eb"
    _GREEN      = "#22c55e"
    _RED        = "#ef4444"
    _MUTED      = "#6b7280"
    _LABEL_STYLE = "color:#9ca3af; font-size:11px;"
    _VALUE_STYLE = "font-weight:bold; font-size:12px;"

    # ── 策略专属颜色（总权益 + 已知策略名）────────────────
    SERIES_COLORS: dict[str, str] = {
        "_total":       "#f9fafb",   # 总权益  —— 白色粗线
        "双均线策略":   "#22c55e",   # 双均线  —— 绿色
        "MACD趋势策略": "#60a5fa",   # MACD   —— 蓝色
    }
    # 未知策略按此顺序轮换
    _FALLBACK_COLORS = ["#f59e0b", "#a78bfa", "#f472b6", "#34d399", "#fb923c"]

    def __init__(
        self,
        initial_cash: float = 100_000.0,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._initial_cash: float = initial_cash

        # ── 数据缓存 ──────────────────────────────────────
        self._equity_df: pd.DataFrame = pd.DataFrame()
        self._strategy_series: dict[str, pd.DataFrame] = {}

        # ── 可见性状态（series_key -> bool）──────────────
        self._visible: dict[str, bool] = {"_total": True}

        # ── 绘制缓存（用于 hover 插值）────────────────────
        # key -> (dates_num_array, nav_array)
        self._drawn_series: dict[str, tuple[np.ndarray, np.ndarray]] = {}

        # ── 图线引用（key -> Line2D）──────────────────────
        self._lines: dict[str, Line2D] = {}

        # ── fill_between 面积块（用于 _total 显隐切换）───
        self._fill_patches: list[Any] = []

        # ── 十字线元素（hover 时惰性创建，clear 后置 None）
        self._crosshair_vline: Any = None
        self._crosshair_annot: Any = None

        # ── matplotlib ────────────────────────────────────
        self._figure = Figure(figsize=(6, 3.5), tight_layout=True)
        self._figure.patch.set_facecolor(self._BG)
        self._canvas = FigureCanvasQTAgg(self._figure)
        self._ax = self._figure.add_subplot(111)
        self._ax.set_facecolor(self._BG)

        # ── 工具栏 ────────────────────────────────────────
        self._toolbar = NavigationToolbar2QT(self._canvas, self)

        # ── 绩效标签 ──────────────────────────────────────
        self._lbl_total_return   = QLabel("--")
        self._lbl_today_pnl      = QLabel("--")
        self._lbl_max_drawdown   = QLabel("--")
        self._lbl_win_rate       = QLabel("--")
        self._lbl_trade_count    = QLabel("--")
        self._lbl_current_assets = QLabel("--")

        self._build_layout()
        self._draw_placeholder()

        # ── 鼠标事件连接 ──────────────────────────────────
        self._canvas.mpl_connect("motion_notify_event", self._on_mouse_move)
        self._canvas.mpl_connect("axes_leave_event",    self._on_mouse_leave)

    # ─────────────────────────────────────────────────────
    # 公开 API
    # ─────────────────────────────────────────────────────

    def update_equity_data(
        self,
        equity_df: pd.DataFrame,
        initial_cash: float | None = None,
        fills_df: pd.DataFrame | None = None,
        current_assets: float | None = None,
        strategy_series: dict[str, pd.DataFrame] | None = None,
    ) -> None:
        """一次调用完成所有刷新：重绘曲线 + 更新绩效指标。

        Args:
            equity_df:        账户净值历史（含 created_at / total_assets 列），
                              由 MockBroker.get_equity_history() 提供。
            initial_cash:     初始资金；None 时沿用上次值。
            fills_df:         成交记录（胜率计算）；None 时跳过。
            current_assets:   当前实时总资产；None 时从 equity_df 末行读取。
            strategy_series:  各策略净值序列 dict；
                              每个 value 是 DataFrame(created_at, nav 或 total_assets)。
                              None 时只显示总权益曲线。
        """
        if initial_cash is not None:
            self._initial_cash = initial_cash

        self._equity_df = equity_df
        self._strategy_series = strategy_series or {}

        if current_assets is None:
            current_assets = (
                float(equity_df["total_assets"].iloc[-1])
                if not equity_df.empty
                else self._initial_cash
            )

        self._rebuild_series_controls(list(self._strategy_series.keys()))
        self._draw_chart()
        self._update_stats(current_assets, fills_df)

    # ─────────────────────────────────────────────────────
    # 静态工具：FIFO 胜率计算（可独立复用）
    # ─────────────────────────────────────────────────────

    @staticmethod
    def compute_trade_stats(fills_df: pd.DataFrame) -> tuple[int, int]:
        """用 FIFO 配对法统计盈利交易次数和总平仓次数。

        正确处理部分平仓（一次买 200 分两次卖 100 的情形）。

        Returns:
            (wins, total)  —— 盈利平仓次数 / 总平仓次数
        """
        if fills_df is None or fills_df.empty:
            return 0, 0

        df = fills_df.sort_values("filled_at").copy()
        open_lots: dict[str, list[tuple[int, float]]] = {}
        wins = total = 0

        for _, row in df.iterrows():
            symbol = str(row["symbol"])
            qty    = int(row["quantity"])
            price  = float(row["price"])
            side   = str(row["side"])

            if side == "BUY":
                open_lots.setdefault(symbol, []).append((qty, price))
            elif side == "SELL":
                queue = open_lots.get(symbol, [])
                if not queue:
                    continue
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
                        queue.pop(0)
                    else:
                        queue[0] = (lot_qty - take, lot_price)
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
            row, col = divmod(i, 2)
            grid.addWidget(name_lbl,  row, col * 2)
            grid.addWidget(value_lbl, row, col * 2 + 1)

        stats_box.setLayout(grid)
        root.addWidget(stats_box)

        # ── 策略 CheckBox 行（动态，初始隐藏）────────────
        self._ctrl_container = QWidget()
        self._ctrl_container.setMaximumHeight(36)
        self._ctrl_layout = QHBoxLayout(self._ctrl_container)
        self._ctrl_layout.setContentsMargins(4, 2, 4, 2)
        self._ctrl_layout.setSpacing(12)
        self._ctrl_container.setVisible(False)
        root.addWidget(self._ctrl_container)

        # ── 工具栏 + 画布 ─────────────────────────────────
        root.addWidget(self._toolbar)
        root.addWidget(self._canvas, stretch=1)

    # ─────────────────────────────────────────────────────
    # 私有：动态 CheckBox 面板
    # ─────────────────────────────────────────────────────

    def _rebuild_series_controls(self, strategy_names: list[str]) -> None:
        """根据当前策略列表重建 CheckBox 控件行。

        每次 update_equity_data 调用时重建，保证 CheckBox 与策略列表同步。
        无策略时隐藏整行。
        """
        layout = self._ctrl_layout

        # 清空旧控件
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        if not strategy_names:
            self._ctrl_container.setVisible(False)
            return

        self._ctrl_container.setVisible(True)

        tip = QLabel("显示：")
        tip.setStyleSheet("color:#9ca3af; font-size:10px;")
        layout.addWidget(tip)

        # 总权益 CheckBox（始终存在）
        cb_total = QCheckBox("总权益")
        cb_total.setChecked(self._visible.get("_total", True))
        cb_total.setStyleSheet(f"color:{self.SERIES_COLORS['_total']}; font-size:10px;")
        cb_total.toggled.connect(lambda checked: self._on_series_toggled("_total", checked))
        layout.addWidget(cb_total)

        # 各策略 CheckBox
        for name in strategy_names:
            color = self._get_series_color(name)
            cb = QCheckBox(name)
            cb.setChecked(self._visible.get(name, True))
            cb.setStyleSheet(f"color:{color}; font-size:10px;")
            # 用默认参数捕获变量（避免 Python 闭包陷阱）
            cb.toggled.connect(lambda checked, n=name: self._on_series_toggled(n, checked))
            layout.addWidget(cb)

        layout.addStretch()

    def _on_series_toggled(self, name: str, checked: bool) -> None:
        """CheckBox 切换：更新可见性并刷新图层（无需完整重绘）。"""
        self._visible[name] = checked

        line = self._lines.get(name)
        if line is not None:
            line.set_visible(checked)

        # 总权益还需同步 fill_between 面积块
        if name == "_total":
            for patch in self._fill_patches:
                patch.set_visible(checked)

        self._canvas.draw_idle()

    # ─────────────────────────────────────────────────────
    # 私有：颜色分配
    # ─────────────────────────────────────────────────────

    def _get_series_color(self, name: str) -> str:
        if name in self.SERIES_COLORS:
            return self.SERIES_COLORS[name]
        keys = list(self._strategy_series.keys())
        idx = keys.index(name) if name in keys else 0
        return self._FALLBACK_COLORS[idx % len(self._FALLBACK_COLORS)]

    # ─────────────────────────────────────────────────────
    # 私有：净值曲线绘制
    # ─────────────────────────────────────────────────────

    def _draw_chart(self) -> None:
        """根据缓存数据重绘多线净值图。"""
        self._figure.clf()
        self._ax = self._figure.add_subplot(111)
        ax = self._ax
        ax.set_facecolor(self._BG)

        self._lines.clear()
        self._drawn_series.clear()
        self._fill_patches.clear()
        self._crosshair_vline = None
        self._crosshair_annot = None

        df = self._equity_df
        if df.empty or self._initial_cash <= 0:
            self._draw_placeholder()
            return

        times  = pd.to_datetime(df["created_at"])
        equity = df["total_assets"].astype(float)
        nav    = equity / self._initial_cash

        final_color   = self._GREEN if float(equity.iloc[-1]) >= self._initial_cash else self._RED
        total_color   = self.SERIES_COLORS["_total"]
        visible_total = self._visible.get("_total", True)

        # ── 总权益曲线（粗白线）──────────────────────────
        line_total, = ax.plot(
            times, nav,
            color=total_color, linewidth=2.2, zorder=4,
            label="总权益", visible=visible_total,
        )
        self._lines["_total"] = line_total

        # hover 数据：转为 matplotlib date 数值（numpy array）
        times_num = mdates.date2num(times.dt.to_pydatetime())
        self._drawn_series["_total"] = (times_num, nav.to_numpy())

        # ── 面积填充（涨绿跌红）──────────────────────────
        p1 = ax.fill_between(
            times, 1.0, nav, where=(nav >= 1.0),
            color=self._GREEN, alpha=0.10, zorder=2, visible=visible_total,
        )
        p2 = ax.fill_between(
            times, 1.0, nav, where=(nav < 1.0),
            color=self._RED, alpha=0.12, zorder=2, visible=visible_total,
        )
        self._fill_patches = [p1, p2]

        # ── 末端净值数值标注 ──────────────────────────────
        last_nav = float(nav.iloc[-1])
        ax.annotate(
            f"  {last_nav:.4f}",
            xy=(times.iloc[-1], last_nav),
            color=final_color, fontsize=9, va="center", zorder=5,
        )

        # ── 各策略对比净值线（细虚线，无填充）────────────
        for name, sdf in self._strategy_series.items():
            if sdf is None or sdf.empty:
                continue
            try:
                s_times = pd.to_datetime(sdf["created_at"])
                if "nav" in sdf.columns:
                    s_nav = sdf["nav"].astype(float)
                elif "total_assets" in sdf.columns:
                    s_nav = sdf["total_assets"].astype(float) / self._initial_cash
                else:
                    continue

                color   = self._get_series_color(name)
                visible = self._visible.get(name, True)

                line, = ax.plot(
                    s_times, s_nav,
                    color=color, linewidth=1.2, alpha=0.85,
                    linestyle="--", zorder=3,
                    label=name, visible=visible,
                )
                self._lines[name] = line

                s_times_num = mdates.date2num(s_times.dt.to_pydatetime())
                self._drawn_series[name] = (s_times_num, s_nav.to_numpy())

            except Exception:
                pass  # 单策略异常不影响整体

        # ── 基准线（净值 = 1.0）───────────────────────────
        ax.axhline(
            1.0, color=self._MUTED, linewidth=0.9,
            linestyle=":", alpha=0.6, label="初始净值",
        )

        # ── X 轴时间格式（按跨度自动选精度）──────────────
        span_s = (times.iloc[-1] - times.iloc[0]).total_seconds()
        if span_s < 86_400:
            x_fmt = mdates.DateFormatter("%H:%M")
        elif span_s < 86_400 * 30:
            x_fmt = mdates.DateFormatter("%m-%d %H:%M")
        else:
            x_fmt = mdates.DateFormatter("%Y-%m-%d")

        ax.xaxis.set_major_formatter(x_fmt)
        ax.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=4, maxticks=8))
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
        """无数据时显示占位提示。"""
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
    # 私有：鼠标悬停十字线
    # ─────────────────────────────────────────────────────

    def _on_mouse_move(self, event) -> None:
        """鼠标移动：绘制竖线 + 显示当前各系列 NAV。"""
        if event.inaxes is not self._ax:
            self._hide_crosshair()
            return
        if not self._drawn_series:
            return

        x_data = event.xdata
        if x_data is None:
            self._hide_crosshair()
            return

        ax = self._ax

        # 竖线
        if self._crosshair_vline is None:
            self._crosshair_vline = ax.axvline(
                x_data, color=self._MUTED,
                linewidth=0.8, linestyle="--", alpha=0.7, zorder=10,
            )
        else:
            self._crosshair_vline.set_xdata([x_data, x_data])
            self._crosshair_vline.set_visible(True)

        # 各系列插值 NAV
        label_map = {"_total": "总权益"}
        lines_text: list[str] = []
        for name, (xs, ys) in self._drawn_series.items():
            if not self._visible.get(name, True):
                continue
            if len(xs) == 0:
                continue
            idx = int(np.searchsorted(xs, x_data, side="left"))
            idx = max(0, min(idx, len(xs) - 1))
            nav_val = float(ys[idx])
            label = label_map.get(name, name)
            lines_text.append(f"{label}: {nav_val:.4f}")

        annot_text = "\n".join(lines_text)
        if not annot_text:
            return

        y_mid = sum(ax.get_ylim()) / 2

        if self._crosshair_annot is None:
            self._crosshair_annot = ax.annotate(
                annot_text,
                xy=(x_data, y_mid),
                xytext=(8, 0),
                textcoords="offset points",
                fontsize=8,
                color=self._TEXT,
                bbox=dict(
                    boxstyle="round,pad=0.3",
                    facecolor=self._BG,
                    edgecolor=self._GRID_CLR,
                    alpha=0.88,
                ),
                zorder=20,
            )
        else:
            self._crosshair_annot.set_position((x_data, y_mid))
            self._crosshair_annot.set_text(annot_text)
            self._crosshair_annot.set_visible(True)

        self._canvas.draw_idle()

    def _on_mouse_leave(self, event) -> None:
        """鼠标离开坐标区时隐藏十字线。"""
        self._hide_crosshair()

    def _hide_crosshair(self) -> None:
        """隐藏竖线和标注（仅当可见时才触发重绘，减少不必要刷新）。"""
        changed = False
        if self._crosshair_vline is not None and self._crosshair_vline.get_visible():
            self._crosshair_vline.set_visible(False)
            changed = True
        if self._crosshair_annot is not None and self._crosshair_annot.get_visible():
            self._crosshair_annot.set_visible(False)
            changed = True
        if changed:
            self._canvas.draw_idle()

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

        # 当前总资产
        self._lbl_current_assets.setText(f"{current_assets:,.2f}")
        self._set_label_pnl_color(self._lbl_current_assets, current_assets - initial)

        if df.empty:
            for lbl in (
                self._lbl_total_return, self._lbl_today_pnl,
                self._lbl_max_drawdown, self._lbl_win_rate,
                self._lbl_trade_count,
            ):
                lbl.setText("--")
            return

        equity = df["total_assets"].astype(float)

        # 总收益率
        ret_pct = (current_assets - initial) / initial * 100
        self._lbl_total_return.setText(f"{ret_pct:+.2f}%")
        self._set_label_pnl_color(self._lbl_total_return, ret_pct)

        # 今日盈亏（今日首条快照 vs 当前）
        today    = datetime.now().date()
        df_today = df[pd.to_datetime(df["created_at"]).dt.date == today]
        day_start = (
            float(df_today["total_assets"].iloc[0])
            if len(df_today) >= 1 else initial
        )
        today_pnl = current_assets - day_start
        self._lbl_today_pnl.setText(f"{today_pnl:+,.2f}")
        self._set_label_pnl_color(self._lbl_today_pnl, today_pnl)

        # 最大回撤（净值序列滚动最大值法）
        nav    = equity / initial
        max_dd = float(((nav - nav.cummax()) / nav.cummax()).min()) * 100
        self._lbl_max_drawdown.setText(f"{max_dd:.2f}%")
        self._lbl_max_drawdown.setStyleSheet(
            "font-weight:bold; font-size:12px; color:#ef4444;"
            if max_dd < -0.5
            else self._VALUE_STYLE
        )

        # 胜率 / 交易次数（FIFO 配对）
        if fills_df is not None and not fills_df.empty:
            wins, total = self.compute_trade_stats(fills_df)
            win_rate = wins / total * 100 if total > 0 else 0.0
            self._lbl_win_rate.setText(
                f"{win_rate:.1f}%  ({wins}/{total})" if total else "--"
            )
            self._lbl_trade_count.setText(str(total) if total else "--")
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
