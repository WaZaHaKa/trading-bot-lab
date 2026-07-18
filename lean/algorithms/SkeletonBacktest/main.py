# region imports
from AlgorithmImports import *
# endregion


class SkeletonBacktest(QCAlgorithm):
    """No-trade LEAN skeleton.

    This algorithm subscribes to a small stock/crypto universe but intentionally
    places no orders. Use it only to verify that local LEAN backtesting works.
    """

    def Initialize(self):
        self.SetStartDate(2023, 1, 1)
        self.SetEndDate(2023, 3, 31)
        self.SetCash(100000)

        self.spy = self.AddEquity("SPY", Resolution.Daily).Symbol
        self.btc = self.AddCrypto("BTCUSD", Resolution.Daily).Symbol

        self.Debug("SkeletonBacktest initialized. No orders will be submitted.")

    def OnData(self, data: Slice):
        # Intentionally no trading logic in the starter scaffold.
        return
