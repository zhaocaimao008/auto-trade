from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from time import time

import pandas as pd
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from matplotlib.patches import Rectangle
from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtGui import QAction, QColor, QBrush
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenuBar,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QSplitter,
    QStatusBar,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QTabWidget,
    QHeaderView,
)

from autotrader_app.backtest.engine import BacktestEngine
from autotrader_app.broker.mock_broker import MockBroker
from autotrader_app.config import BASE_DIR, get_settings
from autotrader_app.data.providers import DataProviderFactory
from autotrader_app.database import get_session
from autotrader_app.models import OrderSide
from autotrader_app.repositories import AccountRepository, OrderRepository, PositionRepository
from autotrader_app.services.trading_service import TradingService
from autotrader_app.utils import is_trading_time, next_trading_segment_start


@dataclass(slots=True)
class StrategyDefinition:
    name: str
    fast_window: int = 5
    slow_window: int = 20
    lot_size: int = 100
    enabled: bool = True


class SettingsDialog(QDialog):
    """配置对话框。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("系统配置")
        self.resize(460, 220)

        settings = get_settings()
        self.tushare_token_input = QLineEdit(settings.tushare_token)
        self.broker_name_input = QLineEdit("MockBroker")
        self.broker_account_input = QLineEdit("SIM-001")
        self.data_source_input = QLineEdit(settings.default_data_source)

        form = QFormLayout()
        form.addRow("Tushare Token", self.tushare_token_input)
        form.addRow("券商名称", self.broker_name_input)
        form.addRow("券商账户", self.broker_account_input)
        form.addRow("默认数据源", self.data_source_input)

        button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)

        layout = QVBoxLayout()
        layout.addLayout(form)
        layout.addWidget(button_box)
        self.setLayout(layout)


class MplKLineCanvas(FigureCanvasQTAgg):
    """嵌入式 K 线图。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        self.figure = Figure(figsize=(7.5, 4.8), tight_layout=True)
        super().__init__(self.figure)
        self.setParent(parent)
        self.axes = self.figure.add_subplot(111)
        self.figure.patch.set_facecolor("#111827")
        self.axes.set_facecolor("#111827")

    def plot_bars(self, bars: pd.DataFrame, title: str) -> None:
        self.axes.clear()
        self.axes.set_facecolor("#111827")
        self.axes.tick_params(colors="#e5e7eb")
        self.axes.spines["bottom"].set_color("#6b7280")
        self.axes.spines["top"].set_color("#6b7280")
        self.axes.spines["left"].set_color("#6b7280")
        self.axes.spines["right"].set_color("#6b7280")
        self.axes.set_title(title, color="#f9fafb", fontsize=12)

        if bars.empty:
            self.axes.text(0.5, 0.5, "暂无行情数据", transform=self.axes.transAxes,
                           ha="center", va="center", color="white")
            self.draw()
            return

        display = bars.tail(80).reset_index(drop=True).copy()
        for idx, row in display.iterrows():
            color = "#ef4444" if row["close"] >= row["open"] else "#10b981"
            self.axes.vlines(idx, row["low"], row["high"], color=color, linewidth=1)
            lower = min(row["open"], row["close"])
            height = abs(row["close"] - row["open"]) or 0.01
            self.axes.add_patch(Rectangle((idx - 0.3, lower), 0.6, height,
                                          facecolor=color, edgecolor=color))

        display["ma5"] = display["close"].rolling(5).mean()
        display["ma20"] = display["close"].rolling(20).mean()
        self.axes.plot(display.index, display["ma5"], color="#f59e0b", linewidth=1.1, label="MA5")
        self.axes.plot(display.index, display["ma20"], color="#60a5fa", linewidth=1.1, label="MA20")
        self.axes.legend(facecolor="#111827", edgecolor="#374151", labelcolor="#e5e7eb")
        self.axes.grid(color="#374151", linestyle="--", alpha=0.35)
        self.draw()


class MainWindow(QMainWindow):
    """A 股自动交易系统主界面。"""

    SIGNAL_COLORS = {
        "BUY": QColor("#22c55e"),    # 绿色
        "SELL": QColor("#ef4444"),   # 红色
        "HOLD": QColor("#9ca3af"),   # 灰色
    }

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("A股自动交易系统")
        self.resize(1680, 980)

        self.provider = DataProviderFactory.create(get_settings().default_data_source)
        self.broker = MockBroker()
        self.trading_service = TradingService(provider=self.provider, broker=self.broker)
        self.backtest_engine = BacktestEngine()

        self.strategy_definitions: list[StrategyDefinition] = [
            StrategyDefinition(name="双均线策略", fast_window=5, slow_window=20, lot_size=100),
            StrategyDefinition(name="趋势突破策略", fast_window=10, slow_window=30, lot_size=200, enabled=False),
        ]
        self.watchlist: list[str] = ["000001", "600519", "000858", "601318"]
        self.latest_bar_cache: dict[str, pd.DataFrame] = {}
        self.signal_cache: dict[str, dict] = {}  # symbol -> latest signal info
        self.is_running = False
        self.is_paused = False
        self._chart_symbol: str | None = None
        self._market_refresh_tick = 0  # 计数用
        self._position_cache: dict[str, dict] = {}  # symbol -> {quantity, avg_price}
        self._position_cache_ts: float = 0.0  # 上一次刷新持仓的时间戳

        self._build_menu()
        self._build_status_bar()
        self._build_widgets()
        self._build_layout()
        self._bind_events()
        self._setup_timers()

        # 初始加载
        self.refresh_strategy_list()
        self.refresh_watchlist_table()
        self.refresh_account_views()
        self.refresh_signal_table()
        self.log("系统启动完成。初始监控: " + ", ".join(self.watchlist))

    # ── 菜单 ────────────────────────────────────────────────

    def _build_menu(self) -> None:
        menu_bar = QMenuBar(self)
        self.setMenuBar(menu_bar)

        config_menu = menu_bar.addMenu("配置")
        cfg = QAction("Tushare Token / 券商设置", self)
        cfg.triggered.connect(self.open_settings_dialog)
        config_menu.addAction(cfg)

        bt_menu = menu_bar.addMenu("回测")
        bt_act = QAction("运行当前股票回测", self)
        bt_act.triggered.connect(self.run_backtest_for_selected_symbol)
        bt_menu.addAction(bt_act)

        ex_menu = menu_bar.addMenu("导出")
        ex_act = QAction("导出日志", self)
        ex_act.triggered.connect(self.export_logs)
        ex_menu.addAction(ex_act)

    # ── 状态栏 ────────────────────────────────────────────

    def _build_status_bar(self) -> None:
        status = QStatusBar(self)
        self.setStatusBar(status)
        self.market_status_label = QLabel("状态：待命")
        self.provider_status_label = QLabel(f"数据源：{get_settings().default_data_source}")
        self.system_time_label = QLabel(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        status.addPermanentWidget(self.market_status_label)
        status.addPermanentWidget(self.provider_status_label)
        status.addPermanentWidget(self.system_time_label)

    # ── 控件创建 ───────────────────────────────────────────

    def _build_widgets(self) -> None:
        # ── 左侧：策略 ──
        self.strategy_list = QListWidget()
        self.strategy_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.add_strategy_button = QPushButton("添加策略")
        self.edit_strategy_button = QPushButton("编辑参数")
        self.delete_strategy_button = QPushButton("删除策略")

        self.fast_window_input = QLineEdit("5")
        self.slow_window_input = QLineEdit("20")
        self.lot_size_input = QLineEdit("100")
        self.strategy_symbol_pool_input = QLineEdit(",".join(self.watchlist))
        self.strategy_note_input = QLineEdit("主监控股票池")

        # ── 中间：行情和 K 线 ──
        self.provider_selector = QLineEdit(get_settings().default_data_source)
        self.provider_selector.setReadOnly(True)
        self.symbol_input = QLineEdit("000001")
        self.add_symbol_button = QPushButton("加入监控")
        self.remove_symbol_button = QPushButton("移除选中")
        self.refresh_market_button = QPushButton("刷新行情")

        self.market_table = QTableWidget()
        self.market_table.setColumnCount(8)
        self.market_table.setHorizontalHeaderLabels(["代码", "日期", "开盘", "最高", "最低", "收盘", "涨跌幅", "成交量"])
        self.market_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.market_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)

        self.kline_canvas = MplKLineCanvas(self)

        # ── 右侧：信号 + 账户 + 订单 ──
        self.signal_table = QTableWidget()
        self.signal_table.setColumnCount(5)
        self.signal_table.setHorizontalHeaderLabels(["代码", "信号", "价格", "原因", "时间"])
        self.signal_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.signal_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.signal_table.horizontalHeader().setStretchLastSection(True)

        self.cash_label = QLabel("0.00")
        self.market_value_label = QLabel("0.00")
        self.total_assets_label = QLabel("0.00")
        self.position_count_label = QLabel("0")

        self.positions_table = QTableWidget()
        self.positions_table.setColumnCount(3)
        self.positions_table.setHorizontalHeaderLabels(["代码", "数量", "均价"])
        self.positions_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)

        self.orders_table = QTableWidget()
        self.orders_table.setColumnCount(6)
        self.orders_table.setHorizontalHeaderLabels(["订单号", "代码", "方向", "数量", "价格", "状态"])
        self.orders_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)

        self.fills_table = QTableWidget()
        self.fills_table.setColumnCount(5)
        self.fills_table.setHorizontalHeaderLabels(["时间", "代码", "方向", "数量", "价格"])
        self.fills_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)

        # ── 底部：控制 ──
        self.log_output = QPlainTextEdit()
        self.log_output.setReadOnly(True)
        self.start_button = QPushButton("▶ 启动引擎")
        self.pause_button = QPushButton("⏸ 暂停")
        self.stop_button = QPushButton("⏹ 停止")
        self.buy_button = QPushButton("模拟买入")
        self.sell_button = QPushButton("模拟卖出")
        self.pause_button.setEnabled(False)
        self.stop_button.setEnabled(False)

    # ── 布局 ────────────────────────────────────────────────

    def _build_layout(self) -> None:
        central = QWidget()
        root = QVBoxLayout(central)

        top_splitter = QSplitter(Qt.Orientation.Horizontal)
        top_splitter.addWidget(self._create_left_panel())
        top_splitter.addWidget(self._create_center_panel())
        top_splitter.addWidget(self._create_right_panel())
        top_splitter.setSizes([280, 780, 520])

        bottom_box = QGroupBox("运行日志与控制")
        bottom_layout = QVBoxLayout()
        bottom_layout.addWidget(self.log_output)
        button_row = QHBoxLayout()
        button_row.addWidget(self.start_button)
        button_row.addWidget(self.pause_button)
        button_row.addWidget(self.stop_button)
        button_row.addStretch(1)
        button_row.addWidget(self.buy_button)
        button_row.addWidget(self.sell_button)
        bottom_layout.addLayout(button_row)
        bottom_box.setLayout(bottom_layout)

        root.addWidget(top_splitter, 4)
        root.addWidget(bottom_box, 1)
        self.setCentralWidget(central)

    def _create_left_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)

        sb = QGroupBox("策略列表")
        sl = QVBoxLayout()
        sl.addWidget(self.strategy_list)
        sr = QHBoxLayout()
        sr.addWidget(self.add_strategy_button)
        sr.addWidget(self.edit_strategy_button)
        sr.addWidget(self.delete_strategy_button)
        sl.addLayout(sr)
        sb.setLayout(sl)

        pb = QGroupBox("参数设置")
        pf = QFormLayout()
        pf.addRow("快线周期", self.fast_window_input)
        pf.addRow("慢线周期", self.slow_window_input)
        pf.addRow("每次手数", self.lot_size_input)
        pf.addRow("股票池", self.strategy_symbol_pool_input)
        pf.addRow("备注", self.strategy_note_input)
        pb.setLayout(pf)

        layout.addWidget(sb, 3)
        layout.addWidget(pb, 2)
        return panel

    def _create_center_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)

        tb = QGroupBox("行情监控")
        tg = QGridLayout()
        tg.addWidget(QLabel("数据源"), 0, 0)
        tg.addWidget(self.provider_selector, 0, 1)
        tg.addWidget(QLabel("股票代码"), 0, 2)
        tg.addWidget(self.symbol_input, 0, 3)
        tg.addWidget(self.add_symbol_button, 0, 4)
        tg.addWidget(self.remove_symbol_button, 0, 5)
        tg.addWidget(self.refresh_market_button, 0, 6)
        tb.setLayout(tg)

        mb = QGroupBox("实时行情表")
        ml = QVBoxLayout()
        ml.addWidget(self.market_table)
        mb.setLayout(ml)

        cb = QGroupBox("K线图")
        cl = QVBoxLayout()
        cl.addWidget(self.kline_canvas)
        cb.setLayout(cl)

        layout.addWidget(tb, 0)
        layout.addWidget(mb, 2)
        layout.addWidget(cb, 3)
        return panel

    def _create_right_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)

        # 信号面板
        sig_box = QGroupBox("实时信号")
        sig_layout = QVBoxLayout()
        sig_layout.addWidget(self.signal_table)
        sig_box.setLayout(sig_layout)

        acct_box = QGroupBox("账户信息")
        af = QFormLayout()
        af.addRow("可用资金", self.cash_label)
        af.addRow("持仓市值", self.market_value_label)
        af.addRow("总资产", self.total_assets_label)
        af.addRow("持仓数量", self.position_count_label)
        acct_box.setLayout(af)

        pos_box = QGroupBox("持仓")
        pos_layout = QVBoxLayout()
        pos_layout.addWidget(self.positions_table)
        pos_box.setLayout(pos_layout)

        ord_box = QGroupBox("今日委托")
        ord_layout = QVBoxLayout()
        ord_layout.addWidget(self.orders_table)
        ord_box.setLayout(ord_layout)

        fill_box = QGroupBox("成交记录")
        fill_layout = QVBoxLayout()
        fill_layout.addWidget(self.fills_table)
        fill_box.setLayout(fill_layout)

        layout.addWidget(sig_box, 2)
        layout.addWidget(acct_box, 1)
        layout.addWidget(pos_box, 2)
        layout.addWidget(ord_box, 2)
        layout.addWidget(fill_box, 2)
        return panel

    # ── 事件绑定 ───────────────────────────────────────────

    def _bind_events(self) -> None:
        self.add_strategy_button.clicked.connect(self.add_strategy)
        self.edit_strategy_button.clicked.connect(self.edit_selected_strategy)
        self.delete_strategy_button.clicked.connect(self.delete_selected_strategy)
        self.strategy_list.currentRowChanged.connect(self.on_strategy_selected)

        self.add_symbol_button.clicked.connect(self.add_symbol_to_watchlist)
        self.remove_symbol_button.clicked.connect(self.remove_selected_symbol)
        self.refresh_market_button.clicked.connect(self.refresh_watchlist_table)
        self.market_table.itemSelectionChanged.connect(self.on_market_selection_changed)

        self.start_button.clicked.connect(self.start_trading)
        self.pause_button.clicked.connect(self.pause_trading)
        self.stop_button.clicked.connect(self.stop_trading)
        self.buy_button.clicked.connect(lambda: self.submit_manual_order("BUY"))
        self.sell_button.clicked.connect(lambda: self.submit_manual_order("SELL"))

    # ── 双定时器 ───────────────────────────────────────────

    def _setup_timers(self) -> None:
        # 5s 快速心跳：时钟、账户
        self.heartbeat_timer = QTimer(self)
        self.heartbeat_timer.setInterval(5000)
        self.heartbeat_timer.timeout.connect(self._on_heartbeat)
        self.heartbeat_timer.start()

        # 15s 行情 + 策略周期
        self.engine_timer = QTimer(self)
        self.engine_timer.setInterval(15000)
        self.engine_timer.timeout.connect(self._on_engine_tick)
        self.engine_timer.start()

    def _on_heartbeat(self) -> None:
        """5 秒心跳：只更新时钟和账户，轻量操作。"""
        self.system_time_label.setText(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        try:
            self.refresh_account_views()
        except Exception:
            pass  # 不阻塞心跳

    def _refresh_position_cache(self, force: bool = False) -> None:
        """缓存持仓数据，避免每个 tick 都查 DB。

        只在以下情况刷新：
        - force=True 强制刷新（下单后）
        - 距离上次刷新超过 60 秒
        """
        now = time()
        if not force and (now - self._position_cache_ts) < 60:
            return

        try:
            with get_session() as session:
                rows = PositionRepository(session).list_all()
            cache: dict[str, dict] = {}
            for p in rows:
                cache[p.symbol] = {"quantity": p.quantity, "avg_price": p.avg_price}
            self._position_cache = cache
            self._position_cache_ts = now
        except Exception:
            pass  # 缓存刷新失败不影响主流程

    def _on_engine_tick(self) -> None:
        """引擎周期：行情刷新 + 策略评估 + 自动执行。

        - 交易时间内：15s 全量运行
        - 非交易时间：60s 轻量维持，跳过策略评估
        """
        if not self.is_running or self.is_paused:
            return

        self._market_refresh_tick += 1

        now_bjt = datetime.now().astimezone(timezone(timedelta(hours=8)))
        in_session = is_trading_time(now_bjt)

        # 非交易时间：每隔 4 个 tick（~60s）才做一次全量刷新
        if not in_session:
            if self._market_refresh_tick % 4 != 0:
                return
            self.log(f"⏸ 非交易时间（{now_bjt.strftime('%H:%M')}），进入轻量维持模式")
            try:
                self.market_status_label.setText(
                    f"状态：⏸ 休市 — 下一交易段 {next_trading_segment_start(now_bjt) or '待定'}"
                )
                # 只更新一次行情供参考，不做策略
                self.refresh_watchlist_table()
                self._refresh_position_cache()
            except Exception as exc:
                self.log(f"轻量维持异常：{exc}")
            return

        try:
            # 1. 刷新行情
            self.refresh_watchlist_table()

            # 2. 运行策略（此时内建仓位缓存会在需要时按 60s 间隔自动刷新）
            self._evaluate_all_strategies()
        except Exception as exc:
            self.log(f"引擎周期异常：{exc}")

    # ── 策略 ────────────────────────────────────────────────

    def refresh_strategy_list(self) -> None:
        self.strategy_list.clear()
        for s in self.strategy_definitions:
            tag = "🟢" if s.enabled else "⚪"
            self.strategy_list.addItem(f"{tag} {s.name}")
        if self.strategy_definitions:
            self.strategy_list.setCurrentRow(0)

    def on_strategy_selected(self, row: int) -> None:
        if row < 0 or row >= len(self.strategy_definitions):
            return
        s = self.strategy_definitions[row]
        self.fast_window_input.setText(str(s.fast_window))
        self.slow_window_input.setText(str(s.slow_window))
        self.lot_size_input.setText(str(s.lot_size))
        self.strategy_note_input.setText("启用" if s.enabled else "停用")

    def add_strategy(self) -> None:
        name, ok = QInputDialog.getText(self, "添加策略", "策略名称")
        if not ok or not name.strip():
            return
        self.strategy_definitions.append(StrategyDefinition(name=name.strip()))
        self.refresh_strategy_list()
        self.log(f"新增策略：{name.strip()}")

    def edit_selected_strategy(self) -> None:
        row = self.strategy_list.currentRow()
        if row < 0:
            QMessageBox.information(self, "提示", "请先选择一个策略。")
            return
        s = self.strategy_definitions[row]
        try:
            s.fast_window = int(self.fast_window_input.text().strip())
            s.slow_window = int(self.slow_window_input.text().strip())
            s.lot_size = int(self.lot_size_input.text().strip())
            s.enabled = True
        except ValueError:
            QMessageBox.warning(self, "参数错误", "请填写有效的整数。")
            return
        self.refresh_strategy_list()
        self.log(f"策略已更新：{s.name} (MA{s.fast_window}/{s.slow_window}, 每手{s.lot_size}股)")

    def delete_selected_strategy(self) -> None:
        row = self.strategy_list.currentRow()
        if row < 0:
            return
        name = self.strategy_definitions[row].name
        self.strategy_definitions.pop(row)
        self.refresh_strategy_list()
        self.log(f"已删除策略：{name}")

    # ── 行情 ────────────────────────────────────────────────

    def add_symbol_to_watchlist(self) -> None:
        symbol = self.symbol_input.text().strip()
        if not symbol:
            return
        if symbol in self.watchlist:
            QMessageBox.information(self, "提示", f"{symbol} 已在监控列表中。")
            return
        self.watchlist.append(symbol)
        self.strategy_symbol_pool_input.setText(",".join(self.watchlist))
        self.refresh_watchlist_table()
        self.log(f"加入监控：{symbol}")

    def remove_selected_symbol(self) -> None:
        row = self.market_table.currentRow()
        if row < 0:
            return
        symbol = self.market_table.item(row, 0).text()
        if symbol in self.watchlist:
            self.watchlist.remove(symbol)
            self.strategy_symbol_pool_input.setText(",".join(self.watchlist))
            self.refresh_watchlist_table()
            self.log(f"移除监控：{symbol}")

    def refresh_watchlist_table(self) -> None:
        self.market_table.setRowCount(len(self.watchlist))
        for r, symbol in enumerate(self.watchlist):
            try:
                bars = self.trading_service.fetch_bars(symbol, days=90)
                self.latest_bar_cache[symbol] = bars
                if bars.empty:
                    self._set_market_row(r, [symbol, "-", "-", "-", "-", "-", "N/A", "-"])
                    continue
                last = bars.iloc[-1]
                prev_close = float(bars.iloc[-2]["close"]) if len(bars) > 1 else float(last["close"])
                pct = ((float(last["close"]) - prev_close) / prev_close * 100.0) if prev_close else 0.0
                row_vals = [
                    symbol,
                    pd.to_datetime(last["datetime"]).strftime("%m-%d"),
                    f"{float(last['open']):.2f}",
                    f"{float(last['high']):.2f}",
                    f"{float(last['low']):.2f}",
                    f"{float(last['close']):.2f}",
                    f"{pct:+.2f}%",
                    f"{float(last['volume']):.0f}",
                ]
                self._set_market_row(r, row_vals, pct)
            except Exception as exc:
                self._set_market_row(r, [symbol, "错误", "-", "-", "-", "-", "-", "-"])
                self.log(f"刷新 {symbol} 失败：{exc}")

        # 刷新后自动渲染第一个股票的 K 线
        if self.watchlist and self._chart_symbol is None:
            self.render_chart(self.watchlist[0])

    def on_market_selection_changed(self) -> None:
        row = self.market_table.currentRow()
        if row < 0:
            return
        item = self.market_table.item(row, 0)
        if item:
            self.render_chart(item.text())

    def render_chart(self, symbol: str) -> None:
        self._chart_symbol = symbol
        bars = self.latest_bar_cache.get(symbol, pd.DataFrame())
        self.kline_canvas.plot_bars(bars, f"{symbol} K线图 | 双均线(5/20)")

    # ── 引擎控制 ───────────────────────────────────────────

    def start_trading(self) -> None:
        if self.is_running and not self.is_paused:
            return
        self.is_running = True
        self.is_paused = False
        self.market_status_label.setText("状态：▶ 运行中")
        self.start_button.setEnabled(False)
        self.pause_button.setEnabled(True)
        self.stop_button.setEnabled(True)
        self.log("━━━━━ 交易引擎已启动 ━━━━━")

        # 立即执行一次行情 + 策略
        try:
            self.refresh_watchlist_table()
            self._evaluate_all_strategies()
        except Exception as exc:
            self.log(f"首次策略评估异常：{exc}")

    def pause_trading(self) -> None:
        if not self.is_running:
            return
        self.is_paused = not self.is_paused
        if self.is_paused:
            self.market_status_label.setText("状态：⏸ 已暂停")
            self.pause_button.setText("▶ 继续")
            self.log("交易引擎已暂停。")
        else:
            self.market_status_label.setText("状态：▶ 运行中")
            self.pause_button.setText("⏸ 暂停")
            self.log("交易引擎恢复运行。")

    def stop_trading(self) -> None:
        self.is_running = False
        self.is_paused = False
        self.market_status_label.setText("状态：⏹ 已停止")
        self.start_button.setEnabled(True)
        self.pause_button.setEnabled(False)
        self.stop_button.setEnabled(False)
        self.pause_button.setText("⏸ 暂停")
        self.log("━━━━━ 交易引擎已停止 ━━━━━")

    # ── 策略评估与自动执行 ─────────────────────────────────

    def _evaluate_all_strategies(self) -> None:
        """遍历所有启用的策略，对监控列表中的每只股票评估信号并自动执行。"""
        new_signals: dict[str, dict] = {}
        any_action = False

        from autotrader_app.strategies.base import StrategyContext, StrategySignal
        from autotrader_app.strategies.double_ma_strategy import DoubleMA_Strategy

        # 先确保缓存是最新的（60s 间隔，不会每个 tick 都查 DB）
        self._refresh_position_cache()

        # 从缓存构建持仓 + 市值的映射
        positions: dict[str, int] = {}
        market_value = 0.0
        for sym, info in self._position_cache.items():
            qty = int(info["quantity"])
            price = float(info["avg_price"])
            positions[sym] = qty
            market_value += qty * price

        for strategy in self.strategy_definitions:
            if not strategy.enabled:
                continue

            for symbol in self.watchlist:
                try:
                    bars = self.latest_bar_cache.get(symbol)
                    if bars is None or bars.empty:
                        continue

                    temp_strategy = DoubleMA_Strategy(
                        short_window=strategy.fast_window,
                        long_window=strategy.slow_window,
                        symbol_list=self.watchlist,
                        position_ratio=0.2,
                    )

                    latest_prices: dict[str, float] = {}
                    if not bars.empty:
                        latest_prices[symbol] = float(bars.iloc[-1]["close"])

                    total_assets = (
                        self.broker.account.total_assets
                        if self.broker.account.total_assets > 0
                        else self.broker.account.cash
                    )
                    context = StrategyContext(
                        total_capital=total_assets,
                        available_cash=self.broker.account.cash,
                        total_position_ratio=market_value / total_assets if total_assets > 0 else 0.0,
                        positions=positions,
                        latest_prices=latest_prices,
                    )

                    decision = temp_strategy.generate_signal(symbol, bars, context)

                    signal_info = {
                        "symbol": symbol,
                        "signal": decision.signal.value,
                        "price": decision.price,
                        "reason": decision.reason,
                        "time": datetime.now().strftime("%H:%M:%S"),
                        "strategy": strategy.name,
                    }
                    new_signals[symbol] = signal_info

                    # 引擎运行时自动执行
                    if self.is_running and not self.is_paused:
                        self._auto_execute(symbol, decision)

                    if decision.signal.value in ("BUY", "SELL"):
                        any_action = True

                except Exception as exc:
                    self.log(f"[{strategy.name}] {symbol} 评估失败：{exc}")

        # 更新信号缓存
        self.signal_cache.update(new_signals)
        self.refresh_signal_table()

    def _auto_execute(self, symbol: str, decision) -> None:
        """引擎运行时自动执行策略信号。"""
        from autotrader_app.strategies.base import StrategySignal

        if not hasattr(decision, "signal"):
            return

        if decision.signal == StrategySignal.BUY and decision.suggested_quantity > 0:
            try:
                result = self.trading_service.submit_manual_order(
                    symbol, "BUY", decision.suggested_quantity, decision.price
                )
                self.log(f"🤖 自动买入 {symbol} x{decision.suggested_quantity} @ {decision.price:.2f} → {result.status.value}")
                self.refresh_account_views()
                self._refresh_position_cache(force=True)
            except Exception as exc:
                self.log(f"🤖 自动买入 {symbol} 失败：{exc}")

        elif decision.signal == StrategySignal.SELL and decision.suggested_quantity > 0:
            try:
                result = self.trading_service.submit_manual_order(
                    symbol, "SELL", decision.suggested_quantity, decision.price
                )
                self.log(f"🤖 自动卖出 {symbol} x{decision.suggested_quantity} @ {decision.price:.2f} → {result.status.value}")
                self.refresh_account_views()
                self._refresh_position_cache(force=True)
            except Exception as exc:
                self.log(f"🤖 自动卖出 {symbol} 失败：{exc}")

    def refresh_signal_table(self) -> None:
        """刷新信号面板。"""
        if not self.signal_cache:
            self.signal_table.setRowCount(0)
            return

        symbols = list(self.signal_cache.keys())
        self.signal_table.setRowCount(len(symbols))
        for r, sym in enumerate(symbols):
            info = self.signal_cache[sym]
            sig = info.get("signal", "HOLD")
            color = self.SIGNAL_COLORS.get(sig, QColor("#9ca3af"))

            items = [
                QTableWidgetItem(sym),
                QTableWidgetItem(sig),
                QTableWidgetItem(f"{info.get('price', 0):.2f}"),
                QTableWidgetItem(info.get("reason", "")[:30]),
                QTableWidgetItem(info.get("time", "")),
            ]
            # 信号列染色
            items[1].setForeground(QBrush(color))
            items[1].setFont(self._bold_font())

            for c, item in enumerate(items):
                self.signal_table.setItem(r, c, item)

    # ── 手动下单 ───────────────────────────────────────────

    def submit_manual_order(self, side: str) -> None:
        symbol = self.get_selected_symbol()
        if symbol is None:
            QMessageBox.information(self, "提示", "请先在行情表中选择一只股票。")
            return
        bars = self.latest_bar_cache.get(symbol)
        if bars is None or bars.empty:
            QMessageBox.warning(self, "提示", "当前股票无行情数据，无法下单。")
            return

        last_price = float(bars.iloc[-1]["close"])
        strategy = self.current_strategy()
        lot_size = strategy.lot_size if strategy else 100

        try:
            result = self.trading_service.submit_manual_order(symbol, side, lot_size, last_price)
            self.log(f"手动 {side} {symbol} x{lot_size} @ {last_price:.2f} → {result.status.value} {result.reason}")
            self.refresh_account_views()
            self._refresh_position_cache(force=True)
        except Exception as exc:
            self._show_error(exc)

    # ── 账户 ────────────────────────────────────────────────

    def refresh_account_views(self) -> None:
        try:
            with get_session() as session:
                positions = PositionRepository(session).list_all()
                orders = OrderRepository(session).latest(limit=20)
                latest_snapshot = AccountRepository(session).latest()
        except Exception:
            positions = []
            orders = []
            latest_snapshot = None

        self.positions_table.setRowCount(len(positions))
        for r, p in enumerate(positions):
            self._set_row_items(self.positions_table, r, [p.symbol, str(p.quantity), f"{p.avg_price:.2f}"])

        self.orders_table.setRowCount(len(orders))
        for r, o in enumerate(orders):
            self._set_row_items(self.orders_table, r, [
                o.order_id[:8], o.symbol, o.side, str(o.quantity), f"{o.price:.2f}", o.status
            ])

        # 成交表：从 MockBroker 获取真实成交记录（只显示已成交的）
        fills_data = self.broker.get_fills()
        if not fills_data.empty:
            self.fills_table.setRowCount(len(fills_data))
            for r, (_, fr) in enumerate(fills_data.iterrows()):
                self._set_row_items(self.fills_table, r, [
                    pd.to_datetime(fr["filled_at"]).strftime("%H:%M:%S") if hasattr(fr["filled_at"], "strftime") else str(fr["filled_at"])[-8:],
                    str(fr["symbol"]),
                    str(fr["side"]),
                    str(int(fr["quantity"])),
                    f"{float(fr['price']):.2f}",
                ])
        else:
            self.fills_table.setRowCount(0)

        cash = latest_snapshot.cash if latest_snapshot else self.broker.account.cash
        mv = latest_snapshot.market_value if latest_snapshot else self.broker.account.market_value
        ta = latest_snapshot.total_assets if latest_snapshot else self.broker.account.total_assets
        self.cash_label.setText(f"{cash:,.2f}")
        self.market_value_label.setText(f"{mv:,.2f}")
        self.total_assets_label.setText(f"{ta:,.2f}")
        self.position_count_label.setText(str(len(positions)))

    # ── 回测 ────────────────────────────────────────────────

    def run_backtest_for_selected_symbol(self) -> None:
        symbol = self.get_selected_symbol() or self.symbol_input.text().strip() or "000001"
        try:
            bars = self.trading_service.fetch_bars(symbol, days=240)
            if bars.empty:
                raise RuntimeError("回测数据为空。")
            strategy = self.current_strategy()
            fast = strategy.fast_window if strategy else 5
            slow = strategy.slow_window if strategy else 20
            size = strategy.lot_size if strategy else 100
            result = self.backtest_engine.run_ma_cross(bars, fast=fast, slow=slow, size=size)
            self.log(
                f"📊 回测 [{symbol}] MA{fast}/{slow} | "
                f"起始{result.starting_cash:,.0f} → 终值{result.final_value:,.0f} "
                f"| PnL {result.pnl:+,.0f} ({result.pnl/result.starting_cash*100:+.1f}%)"
            )
        except Exception as exc:
            self._show_error(exc)

    # ── 设置 / 导出 ────────────────────────────────────────

    def open_settings_dialog(self) -> None:
        dialog = SettingsDialog(self)
        if dialog.exec():
            self.log("配置对话框已确认（未自动回写 .env）。")
            self.provider_status_label.setText(f"数据源：{dialog.data_source_input.text().strip() or 'akshare'}")

    def export_logs(self) -> None:
        target, _ = QFileDialog.getSaveFileName(self, "导出日志",
                                                 str(BASE_DIR / f"logs_{datetime.now():%Y%m%d_%H%M}.txt"),
                                                 "Text Files (*.txt)")
        if not target:
            return
        Path(target).write_text(self.log_output.toPlainText(), encoding="utf-8")
        self.log(f"日志已导出：{target}")

    # ── 工具方法 ───────────────────────────────────────────

    def current_strategy(self) -> StrategyDefinition | None:
        row = self.strategy_list.currentRow()
        if 0 <= row < len(self.strategy_definitions):
            return self.strategy_definitions[row]
        return None

    def get_selected_symbol(self) -> str | None:
        row = self.market_table.currentRow()
        if row < 0:
            return None
        item = self.market_table.item(row, 0)
        return item.text() if item else None

    def log(self, message: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log_output.appendPlainText(f"[{stamp}] {message}")

    @staticmethod
    def _bold_font():
        f = QApplication.font()
        f.setBold(True)
        return f

    def _set_market_row(self, row: int, values: list[str], pct: float | None = None) -> None:
        self._set_row_items(self.market_table, row, values)
        if pct is not None:
            color = Qt.GlobalColor.red if pct >= 0 else Qt.GlobalColor.darkGreen
            for c in range(self.market_table.columnCount()):
                item = self.market_table.item(row, c)
                if item:
                    item.setForeground(color)

    @staticmethod
    def _set_row_items(table: QTableWidget, row: int, values: list[str]) -> None:
        for c, val in enumerate(values):
            table.setItem(row, c, QTableWidgetItem(val))

    def _show_error(self, exc: Exception) -> None:
        QMessageBox.critical(self, "错误", str(exc))
        self.log(f"错误：{exc}")
