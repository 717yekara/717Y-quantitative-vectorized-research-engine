"""
ibkr_data.py
------------
"IBKR Historical Data" box.

Uses `ib_async` (the actively maintained community fork, https://github.com/ib-api-reloaded/ib_async).
Connects to IB Gateway, pulls historical OHLCV bars, and hands
them to database.upsert_bars() so every downstream stage reads from the
DB rather than hitting IBKR repeatedly.

Requires IB Gateway running locally with the API enabled
(Edit -> Global Configuration -> API -> Settings -> Enable ActiveX and
Socket Clients), matching config.IBKR.port.
"""
from __future__ import annotations

import logging
import time

import pandas as pd

import config
import database

logger = logging.getLogger(__name__)


class IBKRDataClient:
    def __init__(self, ibkr_config: config.IBKRConfig = config.IBKR):
        self.cfg = ibkr_config
        self._ib = None

    # ------------------------------------------------------------------
    def connect(self):
        from ib_async import IB  # imported lazily so the rest of the
                                  # pipeline can run without TWS installed
        self._ib = IB()
        self._ib.connect(
            self.cfg.host, self.cfg.port,
            clientId=self.cfg.client_id, readonly=self.cfg.readonly,
            timeout=self.cfg.timeout,
        )
        logger.info("Connected to IBKR at %s:%s (clientId=%s)",
                    self.cfg.host, self.cfg.port, self.cfg.client_id)
        return self

    def disconnect(self):
        if self._ib is not None and self._ib.isConnected():
            self._ib.disconnect()

    def __enter__(self):
        return self.connect()

    def __exit__(self, exc_type, exc, tb):
        self.disconnect()

    # ------------------------------------------------------------------
    def fetch_historical_bars(
        self,
        symbol: str,
        duration: str = config.HISTORICAL_DURATION,
        bar_size: str = config.BAR_SIZE,
        what_to_show: str = config.WHAT_TO_SHOW,
        use_rth: bool = config.USE_RTH,
        exchange: str = "SMART",
        currency: str = "USD",
    ) -> pd.DataFrame:
        """Pull one symbol's history straight from IBKR as a DataFrame."""
        from ib_async import Stock

        contract = Stock(symbol, exchange, currency)
        self._ib.qualifyContracts(contract)

        bars = self._ib.reqHistoricalData(
            contract,
            endDateTime="",
            durationStr=duration,
            barSizeSetting=bar_size,
            whatToShow=what_to_show,
            useRTH=use_rth,
            formatDate=1,
        )
        if not bars:
            logger.warning("No bars returned for %s", symbol)
            return pd.DataFrame()

        df = pd.DataFrame(
            [{"date": b.date, "open": b.open, "high": b.high,
              "low": b.low, "close": b.close, "volume": b.volume} for b in bars]
        ).set_index("date")
        df.index = pd.to_datetime(df.index)
        return df

    # ------------------------------------------------------------------
    def sync_universe(self, symbols: list[str] | None = None,
                       pace_seconds: float = 1.0) -> dict[str, int]:
        """
        Fetch + store history for every symbol in the universe.
        Respects a small pacing delay to stay well under IBKR's
        historical-data request-rate limits.
        """
        symbols = symbols or config.UNIVERSE
        database.init_db()
        results = {}
        for sym in symbols:
            df = self.fetch_historical_bars(sym)
            n = database.upsert_bars(sym, df)
            results[sym] = n
            logger.info("%s: stored %d new bars", sym, n)
            time.sleep(pace_seconds)
        return results


def refresh_market_data(symbols: list[str] | None = None) -> dict[str, int]:
    """Convenience one-liner used by main.py / a scheduled cron job."""
    with IBKRDataClient() as client:
        return client.sync_universe(symbols)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(refresh_market_data())
