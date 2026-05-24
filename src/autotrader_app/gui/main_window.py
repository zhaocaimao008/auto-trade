from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from time import time

import pandas as pd
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from matplotlib.patches import Rectangle
from autotrader_app.gui.equity_curve_widget import EquityCurveWidget
from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtGui import QAction, QColor, QBrush
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
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
from autotrader_app.broker import create_broker
from autotrader_app.config import BASE_DIR, check_config, get_settings
from autotrader_app.data.providers import DataProviderFactory
from autotrader_app.risk.risk_manager import RiskManager
from autotrader_app.services.scheduler_service import SchedulerService
from autotrader_app.database import get_session
from autotrader_app.models import OrderSide
from autotrader_app.repositories import AccountRepository, OrderRepository, PositionRepository
from autotrader_app.services.trading_service import TradingService
from autotrader_app.utils import is_trading_day, is_trading_time, next_trading_segment_start


@dataclass(slots=True)
class StrategyDefinition:
    """GUI 侧的策略描述对象，驱动参数面板的显示与编辑。"""

    name: str
    # ── 策略类型，决定运行时使用哪个策略类 ──────────────────
    # 合法值："双均线" | "MACD趋势"
    strategy_type: str = "双均线"
    # ── 双均线 / MACD 快慢线共用参数 ────────────────────────
    fast_window: int = 5        # 双均线：短期均线；MACD：fast_period（12）
    slow_window: int = 20       # 双均线：长期均线；MACD：slow_period（26）
    # ── MACD 专用参数 ────────────────────────────────────────
    signal_period: int = 9      # MACD 信号线周期（DEA 的 EMA 周期）
    use_ma60_filter: bool = False  # 买入时是否要求价格高于 MA60
    # ── 公共参数 ─────────────────────────────────────────────
    lot_size: int = 100
    enabled: bool = True


class SettingsDialog(QDialog):
    """系统配置对话框（数据源 + Broker + EasyTrader）。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("系统配置")
        self.resize(520, 420)

        settings = get_settings()

        # ── 数据源 ───────────────────────────────────────────
        self.data_source_input = QLineEdit(settings.default_data_source)
        self.tushare_token_input = QLineEdit(settings.tushare_token)

        # ── Broker ───────────────────────────────────────────
        self.broker_type_combo = QComboBox()
        self.broker_type_combo.addItems(["mock", "easytrader"])
        self.broker_type_combo.setCurrentText(settings.broker_type)

        # ── EasyTrader ──────────────────────────────────────
        self.et_broker_type_input = QLineEdit(settings.easytrader_broker_type)
        self.et_account_input = QLineEdit(settings.easytrader_account)
        self.et_password_input = QLineEdit(settings.easytrader_password)
        self.et_password_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.et_exe_path_input = QLineEdit(settings.easytrader_exe_path)
        self.et_exe_browse_button = QPushButton("浏览...")

        # 实盘开关
        self.live_checkbox = QCheckBox("启用实盘交易（本软件不承担任何损失）")
        self.live_checkbox.setChecked(settings.is_live_trading)
        self.live_checkbox.setStyleSheet("color:#ef4444; font-weight:bold;")

        # Broker 类型联动
        self.broker_type_combo.currentTextChanged.connect(self._on_broker_type_changed)
        self.et_exe_browse_button.clicked.connect(self._browse_exe)

        # ── 布局 ─────────────────────────────────────────────
        tabs = QTabWidget()

        # Tab 1: 数据源
        ds_tab = QWidget()
        ds_form = QFormLayout(ds_tab)
        ds_form.addRow("默认数据源", self.data_source_input)
        ds_form.addRow("Tushare Token", self.tushare_token_input)
        tabs.addTab(ds_tab, "数据源")

        # Tab 2: Broker
        broker_tab = QWidget()
        broker_form = QFormLayout(broker_tab)
        broker_form.addRow("Broker 类型", self.broker_type_combo)

        et_group = QGroupBox("EasyTrader 配置")
        et_form = QFormLayout(et_group)
        et_form.addRow("券商类型", self.et_broker_type_input)
        et_form.addRow("账号", self.et_account_input)
        et_form.addRow("密码", self.et_password_input)
        exe_row = QHBoxLayout()
        exe_row.addWidget(self.et_exe_path_input)
        exe_row.addWidget(self.et_exe_browse_button)
        et_form.addRow("客户端路径", exe_row)
        et_form.addRow("", self.live_checkbox)
        broker_form.addRow(et_group)
        tabs.addTab(broker_tab, "Broker")

        button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)

        layout = QVBoxLayout()
        layout.addWidget(tabs)
        layout.addWidget(button_box)
        self.setLayout(layout)

        self._on_broker_type_changed(self.broker_type_combo.currentText())

    def _on_broker_type_changed(self, broker_type: str) -> None:
        """Broker 类型切换时显示/隐藏 EasyTrader 配置区。"""
        is_et = broker_type == "easytrader"
        for w in self.findChildren(QGroupBox):
            if w.title() == "EasyTrader 配置":
                w.setVisible(is_et)
                break

    def _browse_exe(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "选择券商客户端", "", "Executable (*.exe);;All Files (*)")
        if path:
            self.et_exe_path_input.setText(path)


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
        self._initial_cash: float = 100_000.0          # 初始资金（绘制净值曲线基准）

        # Broker 创建：根据配置自动选择 Mock 或 EasyTrader
        settings = get_settings()
        self._is_live_trading = settings.is_live_trading
        self.broker = create_broker(
            broker_type="easytrader" if self._is_live_trading else "mock",
            is_live=self._is_live_trading,
            initial_cash=self._initial_cash,
        )
        self.broker_type_label = "实盘" if self._is_live_trading else "模拟"
        self.trading_service = TradingService(provider=self.provider, broker=self.broker)
        self.backtest_engine = BacktestEngine()
        self.risk_manager = RiskManager(initial_cash=self._initial_cash)

        self.strategy_definitions: list[StrategyDefinition] = [
            # 双均线策略：短期 MA5 上穿/下穿 MA20 产生信号
            StrategyDefinition(
                name="双均线策略",
                strategy_type="双均线",
                fast_window=5,
                slow_window=20,
                lot_size=100,
            ),
            # MACD 趋势策略：标准 12-26-9 参数，含 MA60 趋势过滤
            StrategyDefinition(
                name="MACD趋势策略",
                strategy_type="MACD趋势",
                fast_window=12,
                slow_window=26,
                signal_period=9,
                use_ma60_filter=False,
                lot_size=100,
            ),
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
        self._live_warning_label: QLabel | None = None
        self._trusted_mode: bool = False  # True 时实盘自动执行跳过二次确认

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
        self.refresh_equity_curve()          # 启动时加载历史权益曲线（如有）

        # 配置状态检查与实盘警告
        for warn in check_config():
            self.log(f"⚠️ {warn}")
        self._update_live_warning()

        # 定时调度服务（按 A 股交易时段自动启停引擎）
        self.scheduler = SchedulerService()
        self.scheduler.set_engine_callbacks(
            start=self.start_trading,
            pause=self._scheduled_pause,
            resume=self._scheduled_resume,
            stop=self.stop_trading,
        )
        self.scheduler.start()
        if self._is_live_trading:
            self.log("Scheduler: 交易时段自动启停已启用（09:20→11:30→12:55→15:05）")

        # 实盘自动登录 EasyTrader
        if self._is_live_trading:
            self._auto_login_easytrader()

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

        # ⚡ 安全菜单（紧急停止 + 清仓 + 风控）
        safety_menu = menu_bar.addMenu("⚡ 安全")
        emergency_act = QAction("🛑 紧急停止引擎", self)
        emergency_act.setShortcut("Ctrl+Shift+Escape")
        emergency_act.triggered.connect(self._emergency_stop)
        safety_menu.addAction(emergency_act)

        panic_sell_act = QAction("⚠️ 清仓所有持仓", self)
        panic_sell_act.triggered.connect(self._panic_sell_all)
        safety_menu.addAction(panic_sell_act)

        risk_cfg_act = QAction("风控参数设置", self)
        risk_cfg_act.triggered.connect(self._open_risk_config_dialog)
        safety_menu.addAction(risk_cfg_act)

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

        # Broker 类型与实盘警告
        self.broker_status_label = QLabel(f"Broker：{self.broker_type_label}")
        status.addPermanentWidget(self.broker_status_label)
        if self._is_live_trading:
            self.live_warning_label = QLabel("🔴 实盘模式")
            self.live_warning_label.setStyleSheet(
                "background-color:#ef4444; color:white; font-weight:bold; padding:2px 8px; border-radius:3px;"
            )
            status.addPermanentWidget(self.live_warning_label)

    # ── 控件创建 ───────────────────────────────────────────

    def _build_widgets(self) -> None:
        # ── 左侧：策略 ──
        self.strategy_list = QListWidget()
        self.strategy_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.add_strategy_button = QPushButton("添加策略")
        self.edit_strategy_button = QPushButton("编辑参数")
        self.delete_strategy_button = QPushButton("删除策略")

        # 策略类型下拉（双均线 / MACD趋势）
        self.strategy_type_combo = QComboBox()
        self.strategy_type_combo.addItems(["双均线", "MACD趋势"])

        # 公共参数输入框
        self.fast_window_input = QLineEdit("5")
        self.slow_window_input = QLineEdit("20")
        self.lot_size_input = QLineEdit("100")
        self.strategy_symbol_pool_input = QLineEdit(",".join(self.watchlist))
        self.strategy_note_input = QLineEdit("主监控股票池")

        # MACD 专用参数控件
        self.signal_period_input = QLineEdit("9")   # DEA 信号线 EMA 周期
        self.use_ma60_filter_checkbox = QCheckBox("启用")  # MA60 趋势过滤开关

        # 联动：切换策略类型时更新快慢线标签提示
        self.strategy_type_combo.currentTextChanged.connect(self._on_strategy_type_changed)
        # 动态标签（随策略类型改变文字）
        self.fast_window_label = QLabel("快线周期")
        self.slow_window_label = QLabel("慢线周期")

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

        # ── 权益曲线 Widget（独立组件，内含画布 + 工具栏 + 绩效面板）──
        self.equity_widget = EquityCurveWidget(
            initial_cash=self._initial_cash, parent=self
        )

        # 右侧面板用 QTabWidget 承载 账户/权益/持仓/委托/成交
        self.account_tab_widget = QTabWidget()

        # ── 底部：控制 ──
        self.log_output = QPlainTextEdit()
        self.log_output.setReadOnly(True)
        self.start_button = QPushButton("▶ 启动引擎")
        self.pause_button = QPushButton("⏸ 暂停")
        self.stop_button = QPushButton("⏹ 停止")
        self.trusted_checkbox = QCheckBox("自动执行免确认")
        self.trusted_checkbox.setToolTip("启用后，实盘模式下策略自动执行不再弹窗确认（风控仍生效）")
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
        button_row.addWidget(self.trusted_checkbox)
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
        pf.addRow("策略类型", self.strategy_type_combo)
        pf.addRow(self.fast_window_label, self.fast_window_input)
        pf.addRow(self.slow_window_label, self.slow_window_input)
        pf.addRow("信号线周期", self.signal_period_input)   # MACD 专用，双均线时忽略
        pf.addRow("MA60 过滤", self.use_ma60_filter_checkbox)  # MACD 专用
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

        # ── 顶部：实时信号（始终可见）────────────────────
        sig_box = QGroupBox("实时信号")
        sig_layout = QVBoxLayout()
        sig_layout.addWidget(self.signal_table)
        sig_box.setLayout(sig_layout)

        # ── 下方：TabWidget（账户/权益曲线/持仓/委托/成交）──
        self.account_tab_widget.addTab(self._create_account_info_tab(), "账户")
        self.account_tab_widget.addTab(self._create_equity_tab(), "📈 权益曲线")
        self.account_tab_widget.addTab(self._wrap_in_widget(self.positions_table), "持仓")
        self.account_tab_widget.addTab(self._wrap_in_widget(self.orders_table), "委托")
        self.account_tab_widget.addTab(self._wrap_in_widget(self.fills_table), "成交")

        layout.addWidget(sig_box, 2)
        layout.addWidget(self.account_tab_widget, 5)
        return panel

    def _create_account_info_tab(self) -> QWidget:
        """账户信息 Tab：资金/市值/总资产/持仓数。"""
        w = QWidget()
        af = QFormLayout(w)
        af.setContentsMargins(8, 8, 8, 8)
        af.setSpacing(6)
        af.addRow("可用资金", self.cash_label)
        af.addRow("持仓市值", self.market_value_label)
        af.addRow("总资产", self.total_assets_label)
        af.addRow("持仓数量", self.position_count_label)
        return w

    def _create_equity_tab(self) -> QWidget:
        """权益曲线 Tab：直接返回独立 EquityCurveWidget 实例。"""
        return self.equity_widget

    @staticmethod
    def _wrap_in_widget(table: "QTableWidget") -> QWidget:  # noqa: F821
        """将 QTableWidget 包进 QWidget，供 Tab 使用。"""
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(table)
        return w

    # ── 策略类型联动辅助 ──────────────────────────────────

    def _on_strategy_type_changed(self, strategy_type: str) -> None:
        """策略类型下拉切换时，更新快/慢线标签文字及默认参数提示。"""
        self._update_param_labels(strategy_type)
        if strategy_type == "MACD趋势":
            # 切到 MACD 时，如果参数还是双均线默认值（5/20）则自动替换成 MACD 标准参数
            if self.fast_window_input.text() == "5" and self.slow_window_input.text() == "20":
                self.fast_window_input.setText("12")
                self.slow_window_input.setText("26")
                self.signal_period_input.setText("9")
        else:
            # 切回双均线时，如果参数还是 MACD 默认值则自动还原
            if self.fast_window_input.text() == "12" and self.slow_window_input.text() == "26":
                self.fast_window_input.setText("5")
                self.slow_window_input.setText("20")

    def _update_param_labels(self, strategy_type: str) -> None:
        """根据策略类型修改参数面板中快/慢线的标签文字。"""
        if strategy_type == "MACD趋势":
            self.fast_window_label.setText("快线周期(EMA)")
            self.slow_window_label.setText("慢线周期(EMA)")
        else:
            self.fast_window_label.setText("快线周期(MA)")
            self.slow_window_label.setText("慢线周期(MA)")

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
        self.trusted_checkbox.toggled.connect(self._on_trusted_toggled)
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
        """5 秒心跳：更新时钟、账户、权益曲线（轻量操作）。"""
        self.system_time_label.setText(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        try:
            self.refresh_account_views()
        except Exception:
            pass

        self._heartbeat_counter = getattr(self, "_heartbeat_counter", 0) + 1

        # 权益曲线每 3 次心跳（≈15 s）刷新一次
        if self._heartbeat_counter % 3 == 0:
            self.refresh_equity_curve()

        # EasyTrader 心跳检查（每 60s = 12 次心跳）
        if self._is_live_trading and self._heartbeat_counter % 12 == 0:
            self._check_easytrader_connection()

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

    def _build_risk_context(self):
        """为风控检查构建 StrategyContext。"""
        from autotrader_app.strategies.base import StrategyContext

        positions: dict[str, int] = {}
        prices: dict[str, float] = {}
        market_value = 0.0

        for sym, info in self._position_cache.items():
            qty = int(info["quantity"])
            price = float(info["avg_price"])
            positions[sym] = qty
            market_value += qty * price

        for sym, bars in self.latest_bar_cache.items():
            if bars is not None and not bars.empty:
                prices[sym] = float(bars.iloc[-1]["close"])

        total = (
            self.broker.account.total_assets
            if hasattr(self.broker.account, 'total_assets') and self.broker.account.total_assets > 0
            else getattr(self.broker.account, 'cash', 100_000)
        )

        return StrategyContext(
            total_capital=total,
            available_cash=getattr(self.broker.account, 'cash', 0),
            total_position_ratio=market_value / total if total > 0 else 0.0,
            positions=positions,
            latest_prices=prices,
        )

    def _on_engine_tick(self) -> None:
        """引擎周期：行情刷新 + 策略评估 + 自动执行。

        - 交易时间内：15s 全量运行
        - 非交易时间：60s 轻量维持，跳过策略评估
        """
        if not self.is_running or self.is_paused:
            return

        now_bjt = datetime.now().astimezone(timezone(timedelta(hours=8)))

        # P0-3: 非交易日直接跳过行情和策略
        if not is_trading_day(now_bjt):
            self.market_status_label.setText("状态：⛔ 非交易日")
            return

        if not is_trading_time(now_bjt):
            self.market_status_label.setText("状态：⏸ 非交易时间")

        self._market_refresh_tick += 1

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
        """刷新左侧策略列表，显示启用状态和策略类型图标。"""
        self.strategy_list.clear()
        for s in self.strategy_definitions:
            status_tag = "🟢" if s.enabled else "⚪"
            # 用不同图标区分策略类型，方便一眼识别
            type_tag = "📊" if s.strategy_type == "MACD趋势" else "📈"
            self.strategy_list.addItem(f"{status_tag} {type_tag} {s.name}")
        if self.strategy_definitions:
            self.strategy_list.setCurrentRow(0)

    def on_strategy_selected(self, row: int) -> None:
        """策略选中时，将对应参数填入参数面板控件。"""
        if row < 0 or row >= len(self.strategy_definitions):
            return
        s = self.strategy_definitions[row]

        # 切换策略类型下拉（不触发联动信号，手动更新标签）
        idx = self.strategy_type_combo.findText(s.strategy_type)
        if idx >= 0:
            self.strategy_type_combo.setCurrentIndex(idx)
        self._update_param_labels(s.strategy_type)

        # 填入各项参数
        self.fast_window_input.setText(str(s.fast_window))
        self.slow_window_input.setText(str(s.slow_window))
        self.signal_period_input.setText(str(s.signal_period))
        self.use_ma60_filter_checkbox.setChecked(s.use_ma60_filter)
        self.lot_size_input.setText(str(s.lot_size))
        self.strategy_note_input.setText("启用" if s.enabled else "停用")

    def add_strategy(self) -> None:
        """添加策略：先选类型，再输入名称，按类型设置默认参数。"""
        # 第一步：选择策略类型
        strategy_types = ["双均线", "MACD趋势"]
        type_choice, ok = QInputDialog.getItem(
            self, "添加策略", "选择策略类型：", strategy_types, 0, False
        )
        if not ok:
            return

        # 第二步：输入策略名称
        default_name = "MACD趋势策略" if type_choice == "MACD趋势" else "双均线策略"
        name, ok = QInputDialog.getText(self, "添加策略", "策略名称：", text=default_name)
        if not ok or not name.strip():
            return

        # 根据类型设置合理默认参数
        if type_choice == "MACD趋势":
            defn = StrategyDefinition(
                name=name.strip(),
                strategy_type="MACD趋势",
                fast_window=12,
                slow_window=26,
                signal_period=9,
                use_ma60_filter=False,
                lot_size=100,
            )
        else:
            defn = StrategyDefinition(
                name=name.strip(),
                strategy_type="双均线",
                fast_window=5,
                slow_window=20,
                lot_size=100,
            )

        self.strategy_definitions.append(defn)
        self.refresh_strategy_list()
        # 自动选中新策略
        self.strategy_list.setCurrentRow(len(self.strategy_definitions) - 1)
        self.log(f"新增策略：{name.strip()}（{type_choice}）")

    def edit_selected_strategy(self) -> None:
        """将参数面板当前值保存回选中的 StrategyDefinition。"""
        row = self.strategy_list.currentRow()
        if row < 0:
            QMessageBox.information(self, "提示", "请先选择一个策略。")
            return
        s = self.strategy_definitions[row]
        try:
            s.strategy_type = self.strategy_type_combo.currentText()
            s.fast_window = int(self.fast_window_input.text().strip())
            s.slow_window = int(self.slow_window_input.text().strip())
            s.signal_period = int(self.signal_period_input.text().strip())
            s.use_ma60_filter = self.use_ma60_filter_checkbox.isChecked()
            s.lot_size = int(self.lot_size_input.text().strip())
            s.enabled = True
        except ValueError:
            QMessageBox.warning(self, "参数错误", "请填写有效的整数参数。")
            return

        self.refresh_strategy_list()
        # 构造参数摘要日志
        if s.strategy_type == "MACD趋势":
            param_desc = (
                f"MACD {s.fast_window}/{s.slow_window}/{s.signal_period}"
                + ("＋MA60过滤" if s.use_ma60_filter else "")
            )
        else:
            param_desc = f"MA{s.fast_window}/{s.slow_window}"
        self.log(f"策略已更新：{s.name} ({param_desc}，每手 {s.lot_size} 股)")

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
        try:
            if not self.heartbeat_timer.isActive():
                self.heartbeat_timer.start()
            if not self.engine_timer.isActive():
                self.engine_timer.start()
        except Exception:
            pass
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
        """停止交易引擎（停止所有定时器）。"""
        self.is_running = False
        self.is_paused = False
        self.market_status_label.setText("状态：⏹ 已停止")
        self.start_button.setEnabled(True)
        self.pause_button.setEnabled(False)
        self.stop_button.setEnabled(False)
        self.pause_button.setText("⏸ 暂停")
        try:
            self.heartbeat_timer.stop()
            self.engine_timer.stop()
        except Exception:
            pass
        self.log("━━━━━ 交易引擎已停止 ━━━━━")

    # ── 定时调度回调 ─────────────────────────────────────

    def _scheduled_pause(self) -> None:
        """定时调度调用的暂停（不修改 is_running 状态）。"""
        if not self.is_running or self.is_paused:
            return
        self.is_paused = True
        self.market_status_label.setText("状态：⏸ 午间休市")
        self.pause_button.setText("▶ 继续")
        self.log("⏸ 午间休市，引擎已暂停（11:30）")

    def _scheduled_resume(self) -> None:
        """定时调度调用的恢复。"""
        if not self.is_running or not self.is_paused:
            return
        self.is_paused = False
        self.market_status_label.setText("状态：▶ 运行中")
        self.pause_button.setText("⏸ 暂停")
        self.log("▶ 下午开盘，引擎已恢复（13:00）")

    # ── EasyTrader 自动登录与保活 ─────────────────────────

    def _auto_login_easytrader(self) -> None:
        """GUI 启动时自动连接 EasyTrader 客户端。"""
        if not self._is_live_trading:
            return
        if not hasattr(self.broker, 'login'):
            return

        s = get_settings()
        if not s.easytrader_account or not s.easytrader_exe_path:
            self.log("⚠️ 实盘模式但 EasyTrader 配置不完整，请到配置页面设置")
            return

        self.log("⏳ 正在自动连接 EasyTrader 客户端...")
        try:
            success = self.broker.login(
                account=s.easytrader_account,
                password=s.easytrader_password,
                exe_path=s.easytrader_exe_path,
                broker_type=s.easytrader_broker_type or "ht",
            )
            if success:
                self.log("✅ EasyTrader 客户端自动连接成功")
            else:
                self.log("❌ EasyTrader 客户端自动连接失败，请检查配置或手动登录")
        except Exception as exc:
            self.log(f"❌ EasyTrader 自动连接异常：{exc}")

    def _check_easytrader_connection(self) -> None:
        """心跳定期检查 EasyTrader 客户端连接状态。断开时重新连接。"""
        if not self._is_live_trading:
            return
        if not hasattr(self.broker, 'is_connected'):
            return
        try:
            if not self.broker.is_connected:
                self.log("⚠️ EasyTrader 连接已断开，尝试重新连接...")
                self._auto_login_easytrader()
        except Exception as exc:
            self.log(f"⚠️ EasyTrader 心跳检查异常：{exc}")

    # ── 紧急停止与安全 ───────────────────────────────────

    def _emergency_stop(self) -> None:
        """紧急停止引擎（快捷键 Ctrl+Shift+Escape）。

        效果：
        1. 停止所有定时器（heartbeat + engine）
        2. 停止调度器（SchedulerService）
        3. 实盘模式可选清仓
        """
        resp = QMessageBox.critical(
            self,
            "🛑 紧急停止",
            "是否立即停止交易引擎并关闭所有定时器？\n\n"
            "引擎停止后不会自动执行任何策略。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if resp != QMessageBox.StandardButton.Yes:
            return

        self.stop_trading()

        try:
            self.scheduler.stop()
        except Exception:
            pass

        if self._is_live_trading:
            sell_resp = QMessageBox.question(
                self,
                "⚠️ 实盘清仓",
                "是否同时清仓所有持仓？\n\n这将会发送真实卖出委托！",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if sell_resp == QMessageBox.StandardButton.Yes:
                self._panic_sell_all()

        from autotrader_app.logging_config import live_logger
        live_logger().warning("[紧急停止] 用户触发紧急停止，引擎和调度器已关闭")
        self.log("🛑 紧急停止已执行（引擎+调度器已停止）")

    def _panic_sell_all(self) -> None:
        """清仓所有持仓。"""
        try:
            with get_session() as session:
                positions = PositionRepository(session).list_all()
        except Exception:
            positions = []

        if not positions:
            QMessageBox.information(self, "提示", "当前无持仓。")
            return

        total = sum(p.quantity for p in positions)
        msg = (
            f"即将发起平仓委托：\n\n共 {len(positions)} 只股票，{total} 股\n\n"
            + ("\n⚠️ 实盘模式，将发送真实卖出委托！" if self._is_live_trading else "")
        )
        resp = QMessageBox.warning(
            self, "清仓确认", msg,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if resp != QMessageBox.StandardButton.Yes:
            return

        for pos in positions:
            try:
                bars = self.latest_bar_cache.get(pos.symbol)
                price = float(bars.iloc[-1]["close"]) if bars is not None and not bars.empty else pos.avg_price
                result = self.trading_service.submit_manual_order(
                    pos.symbol, "SELL", pos.quantity, price,
                    strategy_name="emergency",
                )
                self.log(f"清仓卖出 {pos.symbol} x{pos.quantity} @ {price:.2f} → {result.status.value}")
            except Exception as exc:
                self.log(f"清仓 {pos.symbol} 失败：{exc}")

    def _open_risk_config_dialog(self) -> None:
        """打开风控参数设置对话框。"""
        from autotrader_app.risk.risk_manager import RiskConfig

        dialog = QDialog(self)
        dialog.setWindowTitle("风控参数设置")
        dialog.resize(380, 320)

        default_cfg = RiskConfig()
        sl = QLineEdit(str(default_cfg.stop_loss_pct))
        tp = QLineEdit(str(default_cfg.take_profit_pct))
        md = QLineEdit(str(default_cfg.max_drawdown_pct))
        sp = QLineEdit(str(default_cfg.max_single_position_pct))
        tpct = QLineEdit(str(default_cfg.max_total_position_pct))
        enabled_cb = QCheckBox()
        enabled_cb.setChecked(default_cfg.enabled)

        form = QFormLayout()
        form.addRow("止损比例 (%)", sl)
        form.addRow("止盈比例 (%)", tp)
        form.addRow("最大回撤 (%)", md)
        form.addRow("单股仓位上限 (%)", sp)
        form.addRow("总仓位上限 (%)", tpct)
        form.addRow("风控启用", enabled_cb)

        note = QLabel("注：当前为演示面板，参数调整需重启生效。")
        note.setStyleSheet("color:#9ca3af; font-size:11px;")

        btn = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btn.accepted.connect(dialog.accept)
        btn.rejected.connect(dialog.reject)

        layout = QVBoxLayout(dialog)
        layout.addLayout(form)
        layout.addWidget(note)
        layout.addWidget(btn)
        dialog.exec()

    # ── 策略评估与自动执行 ─────────────────────────────────

    def _evaluate_all_strategies(self) -> None:
        """遍历所有启用的策略，对监控列表中的每只股票评估信号并自动执行。

        支持双均线与 MACD 趋势两种策略类型，按 StrategyDefinition.strategy_type
        动态选择策略类，复用同一套上下文构建、风控、下单流程。
        """
        from autotrader_app.strategies.base import StrategyContext, StrategySignal
        from autotrader_app.strategies.double_ma_strategy import DoubleMA_Strategy
        from autotrader_app.strategies.macd_strategy import MACDStrategy

        new_signals: dict[str, dict] = {}

        # 先确保持仓缓存是最新的（60s 间隔，不会每个 tick 都查 DB）
        self._refresh_position_cache()

        # 从缓存构建持仓映射与当前市值
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

                    # ── 按策略类型实例化对应策略类 ──────────
                    if strategy.strategy_type == "MACD趋势":
                        temp_strategy = MACDStrategy(
                            fast_period=strategy.fast_window,
                            slow_period=strategy.slow_window,
                            signal_period=strategy.signal_period,
                            use_ma60_filter=strategy.use_ma60_filter,
                            symbol_list=self.watchlist,
                            position_ratio=0.2,
                        )
                    else:
                        # 默认：双均线
                        temp_strategy = DoubleMA_Strategy(
                            short_window=strategy.fast_window,
                            long_window=strategy.slow_window,
                            symbol_list=self.watchlist,
                            position_ratio=0.2,
                        )

                    # ── 构建账户上下文 ───────────────────────
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

                    # ── 生成信号 ─────────────────────────────
                    decision = temp_strategy.generate_signal(symbol, bars, context)

                    # 用 "策略名+股票代码" 作 key，允许多策略同时监控同一只股票
                    cache_key = f"{strategy.name}|{symbol}"
                    new_signals[cache_key] = {
                        "symbol": symbol,
                        "signal": decision.signal.value,
                        "price": decision.price,
                        "reason": decision.reason,
                        "time": datetime.now().strftime("%H:%M:%S"),
                        "strategy": strategy.name,
                        "strategy_type": strategy.strategy_type,
                    }

                    # ── 引擎运行时自动执行 ───────────────────
                    if self.is_running and not self.is_paused:
                        self._auto_execute(symbol, decision, strategy_name=strategy.name)

                except Exception as exc:
                    self.log(f"[{strategy.name}] {symbol} 评估失败：{exc}")

        # 更新信号缓存并刷新 UI
        self.signal_cache.update(new_signals)
        self.refresh_signal_table()

    def _auto_execute(
        self,
        symbol: str,
        decision,
        strategy_name: str = "manual",
    ) -> None:
        """引擎运行时自动执行策略信号。

        执行前经过完整风控链路：
          1. 风控 check_entry（白名单/金额/间隔/仓位）
          2. 实盘二次确认（trusted 模式跳过）
          3. 成交后 record_trade 更新统计
        """
        from autotrader_app.strategies.base import StrategySignal

        if not hasattr(decision, "signal"):
            return

        if decision.signal == StrategySignal.HOLD:
            return

        desc = (
            f"[{strategy_name}] {'买入' if decision.signal == StrategySignal.BUY else '卖出'} "
            f"{symbol} x{decision.suggested_quantity} @ {decision.price:.2f}"
        )

        # ── 1. 风控前置检查（所有模式均生效）───────────────
        if decision.signal == StrategySignal.BUY and decision.suggested_quantity > 0:
            ctx = self._build_risk_context()
            risk_ok, risk_msg = self.risk_manager.check_entry(
                symbol, decision.price, decision.suggested_quantity, ctx,
            )
            if not risk_ok:
                self.log(f"🛡️ [{strategy_name}] 风控拦截买入 {symbol}：{risk_msg}")
                return

        # ── 2. 实盘二次确认 ──────────────────────────────
        if not self._confirm_live_order(desc, trusted=True):
            self.log(f"用户取消自动执行：{desc}")
            return

        if decision.signal == StrategySignal.BUY and decision.suggested_quantity > 0:
            try:
                result = self.trading_service.submit_manual_order(
                    symbol, "BUY", decision.suggested_quantity, decision.price,
                    strategy_name=strategy_name,
                )
                self.log(
                    f"🤖 [{strategy_name}] 自动买入 {symbol} "
                    f"x{decision.suggested_quantity} @ {decision.price:.2f} → {result.status.value}"
                )
                if result.status.value == "FILLED":
                    self.risk_manager.record_trade(symbol, "BUY", decision.suggested_quantity * decision.price)
                if self._is_live_trading:
                    from autotrader_app.logging_config import live_logger
                    live_logger().info(
                        "[自动买入] [{}] {} x{} @ {:.2f} → {}",
                        strategy_name, symbol, decision.suggested_quantity, decision.price, result.status.value,
                    )
                self.refresh_account_views()
                self._refresh_position_cache(force=True)
                self.refresh_equity_curve()
            except Exception as exc:
                self.log(f"🤖 自动买入 {symbol} 失败：{exc}")

        elif decision.signal == StrategySignal.SELL and decision.suggested_quantity > 0:
            try:
                result = self.trading_service.submit_manual_order(
                    symbol, "SELL", decision.suggested_quantity, decision.price,
                    strategy_name=strategy_name,
                )
                self.log(
                    f"🤖 [{strategy_name}] 自动卖出 {symbol} "
                    f"x{decision.suggested_quantity} @ {decision.price:.2f} → {result.status.value}"
                )
                if result.status.value == "FILLED":
                    self.risk_manager.record_trade(symbol, "SELL", decision.suggested_quantity * decision.price)
                if self._is_live_trading:
                    from autotrader_app.logging_config import live_logger
                    live_logger().info(
                        "[自动卖出] [{}] {} x{} @ {:.2f} → {}",
                        strategy_name, symbol, decision.suggested_quantity, decision.price, result.status.value,
                    )
                self.refresh_account_views()
                self._refresh_position_cache(force=True)
                self.refresh_equity_curve()          # 成交后立即刷新权益曲线
            except Exception as exc:
                self.log(f"🤖 自动卖出 {symbol} 失败：{exc}")

    def refresh_signal_table(self) -> None:
        """刷新信号面板。

        信号表列：代码 | 策略 | 信号 | 价格 | 原因（截断）| 时间
        """
        if not self.signal_cache:
            self.signal_table.setRowCount(0)
            return

        # 重新设为 6 列（新增"策略"列）
        if self.signal_table.columnCount() != 6:
            self.signal_table.setColumnCount(6)
            self.signal_table.setHorizontalHeaderLabels(
                ["代码", "策略", "信号", "价格", "原因", "时间"]
            )
            self.signal_table.horizontalHeader().setStretchLastSection(False)
            self.signal_table.horizontalHeader().setSectionResizeMode(
                4, QHeaderView.ResizeMode.Stretch
            )

        keys = list(self.signal_cache.keys())
        self.signal_table.setRowCount(len(keys))
        for r, key in enumerate(keys):
            info = self.signal_cache[key]
            sig = info.get("signal", "HOLD")
            color = self.SIGNAL_COLORS.get(sig, QColor("#9ca3af"))

            # 策略类型标识
            s_type = info.get("strategy_type", "")
            type_icon = "📊" if s_type == "MACD趋势" else "📈"
            strategy_label = f"{type_icon} {info.get('strategy', '')}"

            items = [
                QTableWidgetItem(info.get("symbol", key)),
                QTableWidgetItem(strategy_label),
                QTableWidgetItem(sig),
                QTableWidgetItem(f"{info.get('price', 0):.2f}"),
                QTableWidgetItem(info.get("reason", "")[:40]),
                QTableWidgetItem(info.get("time", "")),
            ]
            # 信号列（第 2 列）染色加粗
            items[2].setForeground(QBrush(color))
            items[2].setFont(self._bold_font())

            for c, item in enumerate(items):
                self.signal_table.setItem(r, c, item)

    # ── 手动下单 ───────────────────────────────────────────

    def _confirm_live_order(self, action_desc: str, trusted: bool = False) -> bool:
        """实盘模式二次确认弹窗。

        Args:
            action_desc: 操作描述（如"买入 000001 x100 @ 12.50"）。
            trusted:     True 时跳过弹窗（引擎自动执行且 _trusted_mode 启用）。

        Returns:
            True 用户确认或 trusted=True，False 取消。
        """
        if not self._is_live_trading:
            return True
        if trusted and self._trusted_mode:
            return True

        resp = QMessageBox.warning(
            self,
            "⚠️ 实盘交易确认",
            f"当前为实盘模式，即将执行：\n\n{action_desc}\n\n"
            "此操作将发送真实交易委托到券商！\n是否确认？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return resp == QMessageBox.StandardButton.Yes

    def _check_live_order_limit(self, price: float, quantity: int) -> tuple[bool, str]:
        """实盘下单金额限制检查。

        单笔最大金额硬限制 50,000 元，防止价格/数量错误导致巨大损失。
        """
        if not self._is_live_trading:
            return True, ""
        total = price * quantity
        if total > 50_000:
            return False, f"单笔委托金额 {total:.0f} 元超过上限 50,000 元"
        return True, ""

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
        desc = f"{'买入' if side.upper() == 'BUY' else '卖出'} {symbol} x{lot_size} @ {last_price:.2f}"

        # 实盘金额限制检查
        ok, limit_msg = self._check_live_order_limit(last_price, lot_size)
        if not ok:
            QMessageBox.warning(self, "金额超限", limit_msg)
            self.log(f"❌ {limit_msg}")
            return

        if not self._confirm_live_order(desc):
            self.log(f"用户取消：{desc}")
            return

        # 实盘资金影响预览
        if self._is_live_trading:
            try:
                with get_session() as session:
                    current_positions = PositionRepository(session).list_all()
                current_cash = float(getattr(self.broker.account, 'cash', 0))
                total_amount = lot_size * last_price
                preview = (
                    f"【资金影响预览】\n\n"
                    f"操作：{'买入' if side.upper() == 'BUY' else '卖出'} {symbol}\n"
                    f"数量：{lot_size} 股 × {last_price:.2f} = {total_amount:.2f} 元\n"
                    f"当前可用资金：{current_cash:,.2f}\n"
                )
                if side.upper() == 'BUY':
                    preview += f"预计成交后资金：{current_cash - total_amount:,.2f}\n"
                    preview += f"预计持仓市值增加：{total_amount:,.2f}"
                else:
                    preview += f"预计成交后资金：{current_cash + total_amount:,.2f}\n"
                    existing = next((p for p in current_positions if p.symbol == symbol), None)
                    preview += f"当前 {symbol} 持仓：{existing.quantity if existing else 0} 股"
                resp = QMessageBox.information(
                    self, "资金影响预览", preview,
                    QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
                    QMessageBox.StandardButton.Ok,
                )
                if resp != QMessageBox.StandardButton.Ok:
                    self.log("用户取消：资金影响预览")
                    return
            except Exception:
                pass

        try:
            result = self.trading_service.submit_manual_order(symbol, side, lot_size, last_price)
            self.log(f"手动 {side} {symbol} x{lot_size} @ {last_price:.2f} → {result.status.value} {result.reason}")
            if result.status.value == "FILLED":
                self.risk_manager.record_trade(symbol, side, lot_size * last_price)
            if self._is_live_trading:
                from autotrader_app.logging_config import live_logger
                live_logger().info(
                    "[手动下单] {} {} x{} @ {:.2f} → {} {}",
                    side, symbol, lot_size, last_price, result.status.value, result.reason,
                )
            self.refresh_account_views()
            self._refresh_position_cache(force=True)
            self.refresh_equity_curve()              # 手动下单后立即刷新权益曲线
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

    # ── 权益曲线 ──────────────────────────────────────────────

    def refresh_equity_curve(self) -> None:
        """刷新权益曲线：拉取数据后委托 EquityCurveWidget 完成绘图和指标更新。

        调用时机：
        · 心跳定时器（每 15 s 触发一次，见 _on_heartbeat）
        · 每次成交后立即强制刷新（_auto_execute / submit_manual_order）
        """
        try:
            equity_df      = self.broker.get_equity_history()
            fills_df       = self.broker.get_fills()
            current_assets = self.broker.account.total_assets
            # 多策略净值序列（无成交时返回 {}，widget 仅显示总权益）
            strategy_series = self.broker.get_strategy_equity_series(
                initial_cash=self._initial_cash
            )
            self.equity_widget.update_equity_data(
                equity_df,
                initial_cash=self._initial_cash,
                fills_df=fills_df,
                current_assets=current_assets,
                strategy_series=strategy_series,
            )
        except Exception as exc:
            self.log(f"权益曲线刷新失败：{exc}")

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
            env_path = BASE_DIR / ".env"
            settings = get_settings()

            # ── 收集表单值 ───────────────────────────────────
            ds = dialog.data_source_input.text().strip() or "akshare"
            token = dialog.tushare_token_input.text().strip()
            broker_type = dialog.broker_type_combo.currentText()
            et_type = dialog.et_broker_type_input.text().strip() or "ht"
            et_acct = dialog.et_account_input.text().strip()
            et_pwd = dialog.et_password_input.text().strip()
            et_exe = dialog.et_exe_path_input.text().strip()
            live_mode = "easytrader" if dialog.live_checkbox.isChecked() else "mock"

            try:
                # ── 读取 / 回写 .env ─────────────────────────
                lines: list[str] = []
                if env_path.exists():
                    lines = env_path.read_text(encoding="utf-8").splitlines()

                kv_map: dict[str, str] = {
                    "TUSHARE_TOKEN": token,
                    "DEFAULT_DATA_SOURCE": ds,
                    "BROKER_TYPE": broker_type,
                    "EASYTRADER_BROKER_TYPE": et_type,
                    "EASYTRADER_ACCOUNT": et_acct,
                    "EASYTRADER_PASSWORD": et_pwd,
                    "EASYTRADER_EXE_PATH": et_exe,
                }
                seen: set[str] = set()
                for i, line in enumerate(lines):
                    for key in kv_map:
                        if line.startswith(f"{key}="):
                            lines[i] = f"{key}={kv_map[key]}"
                            seen.add(key)
                for key, val in kv_map.items():
                    if key not in seen:
                        lines.append(f"{key}={val}")
                env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
                get_settings.cache_clear()
                self.log("配置已保存到 .env 文件。")
            except Exception as exc:
                self.log(f"保存配置失败：{exc}")

            # ── 更新 UI ─────────────────────────────────────
            self.provider_status_label.setText(f"数据源：{ds}")

            # ── 重建 Broker（若类型或 live 状态有变化）──────
            new_is_live = live_mode == "easytrader"
            if new_is_live != self._is_live_trading or broker_type != settings.broker_type:
                self._is_live_trading = new_is_live
                self.broker = create_broker(
                    broker_type="easytrader" if self._is_live_trading else "mock",
                    is_live=self._is_live_trading,
                    initial_cash=self._initial_cash,
                )
                self.broker_type_label = "实盘" if self._is_live_trading else "模拟"
                self.trading_service.broker = self.broker
                self.broker_status_label.setText(f"Broker：{self.broker_type_label}")
                self.log(f"Broker 已切换为: {self.broker_type_label}")
                self.refresh_account_views()

            # ── 实盘警告标签动态管理 ──────────────────────────
            self._update_live_warning()

    def _update_live_warning(self) -> None:
        """根据 _is_live_trading 状态显示/隐藏实盘警告标签。"""
        status = self.statusBar()
        if self._is_live_trading:
            if not hasattr(self, "_live_warning_label") or self._live_warning_label is None:
                lbl = QLabel("🔴 实盘模式")
                lbl.setStyleSheet(
                    "background-color:#ef4444; color:white; font-weight:bold; padding:2px 8px; border-radius:3px;"
                )
                status.addPermanentWidget(lbl)
                self._live_warning_label = lbl
            else:
                self._live_warning_label.setVisible(True)
        else:
            if hasattr(self, "_live_warning_label") and self._live_warning_label is not None:
                self._live_warning_label.setVisible(False)

    def _on_trusted_toggled(self, checked: bool) -> None:
        """免确认复选框切换：实盘模式下弹出最终警告。"""
        self._trusted_mode = checked
        if checked and self._is_live_trading:
            resp = QMessageBox.warning(
                self,
                "⚠️ 实盘自动执行已启用",
                "您已启用「自动执行免确认」模式。\n\n"
                "这意味着策略引擎产生的所有买卖信号将：\n"
                "  · 自动发送真实委托到券商\n"
                "  · 不再弹窗确认\n"
                "  · 但仍受风控规则保护\n\n"
                "请确认已理解风险并设置了合理的风控参数。",
                QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if resp != QMessageBox.StandardButton.Ok:
                self._trusted_mode = False
                self.trusted_checkbox.setChecked(False)
                self.log("用户取消启用自动执行免确认")
            else:
                self.log("⚠️ 自动执行免确认已启用，策略信号将自动执行")

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
