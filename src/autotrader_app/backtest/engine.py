from __future__ import annotations

from dataclasses import dataclass

import backtrader as bt
import pandas as pd


@dataclass(slots=True)
class BacktestResult:
    starting_cash: float
    final_value: float
    pnl: float


class _MaCrossBtStrategy(bt.Strategy):
    params = (("fast", 5), ("slow", 20), ("size", 100),)

    def __init__(self) -> None:
        ma_fast = bt.indicators.SimpleMovingAverage(self.data.close, period=self.params.fast)
        ma_slow = bt.indicators.SimpleMovingAverage(self.data.close, period=self.params.slow)
        self.crossover = bt.indicators.CrossOver(ma_fast, ma_slow)

    def next(self) -> None:
        if not self.position and self.crossover > 0:
            self.buy(size=self.params.size)
        elif self.position and self.crossover < 0:
            self.sell(size=self.params.size)


class BacktestEngine:
    """backtrader 回测引擎封装。"""

    def run_ma_cross(
        self,
        bars: pd.DataFrame,
        starting_cash: float = 100_000.0,
        fast: int = 5,
        slow: int = 20,
        size: int = 100,
    ) -> BacktestResult:
        cerebro = bt.Cerebro()
        cerebro.broker.set_cash(starting_cash)
        cerebro.addstrategy(_MaCrossBtStrategy, fast=fast, slow=slow, size=size)

        df = bars.copy()
        df = df.rename(columns={"datetime": "datetime"}).set_index("datetime")
        feed = bt.feeds.PandasData(dataname=df[["open", "high", "low", "close", "volume"]])
        cerebro.adddata(feed)

        cerebro.run()
        final_value = cerebro.broker.getvalue()
        return BacktestResult(
            starting_cash=starting_cash,
            final_value=final_value,
            pnl=final_value - starting_cash,
        )
