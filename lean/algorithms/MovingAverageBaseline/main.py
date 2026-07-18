# region imports
from AlgorithmImports import *
# endregion


class MovingAverageBaseline(QCAlgorithm):
    """Experimental long-only moving-average baseline.

    This algorithm is for local backtesting smoke tests only. It is not optimized,
    does not claim profitability, and must not be used for live trading.
    """

    target_weight = 0.10
    max_daily_loss_pct = 0.02
    max_drawdown_pct = 0.05

    def Initialize(self):
        self.SetStartDate(2020, 1, 1)
        self.SetEndDate(2021, 1, 1)
        self.SetCash(100000)

        self.spy = self.AddEquity("SPY", Resolution.Daily).Symbol
        self.fast = self.SMA(self.spy, 20, Resolution.Daily)
        self.slow = self.SMA(self.spy, 50, Resolution.Daily)
        self.SetWarmUp(60, Resolution.Daily)
        self.SetBenchmark(self.spy)

        self._last_session_date = None
        self._start_of_day_equity = self.Portfolio.TotalPortfolioValue
        self._peak_equity = self.Portfolio.TotalPortfolioValue
        self._risk_halted = False

        self.Debug(
            "MovingAverageBaseline initialized for local backtesting only. "
            "Long-only SPY target capped at 10%."
        )

    def OnData(self, data):
        if self.IsWarmingUp or not data.ContainsKey(self.spy):
            return

        self._update_risk_state()
        if self._risk_halted:
            if self.Portfolio[self.spy].Invested:
                self.Liquidate(self.spy, "risk halt")
            return

        if not self.fast.IsReady or not self.slow.IsReady:
            return

        if self.fast.Current.Value > self.slow.Current.Value:
            self.SetHoldings(self.spy, self.target_weight)
        elif self.Portfolio[self.spy].Invested:
            self.Liquidate(self.spy, "moving-average exit")

    def _update_risk_state(self):
        current_date = self.Time.date()
        current_equity = self.Portfolio.TotalPortfolioValue

        if self._last_session_date != current_date:
            self._last_session_date = current_date
            self._start_of_day_equity = current_equity

        self._peak_equity = max(self._peak_equity, current_equity)

        daily_loss_pct = 0.0
        if self._start_of_day_equity > 0:
            daily_loss_pct = max(0.0, self._start_of_day_equity - current_equity)
            daily_loss_pct /= self._start_of_day_equity

        drawdown_pct = 0.0
        if self._peak_equity > 0:
            drawdown_pct = max(0.0, self._peak_equity - current_equity) / self._peak_equity

        if daily_loss_pct >= self.max_daily_loss_pct:
            self._risk_halted = True
            self.Debug("Daily loss halt reached; no new orders will be submitted.")

        if drawdown_pct >= self.max_drawdown_pct:
            self._risk_halted = True
            self.Debug("Drawdown halt reached; no new orders will be submitted.")
