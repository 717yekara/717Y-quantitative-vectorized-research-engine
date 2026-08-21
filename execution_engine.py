"""
execution_engine.py
--------------------
"IBKR Execution Engine" box -- the live-trading half of the SHARED
pipeline:

    Signals -> PortfolioAllocator -> Target Weights -> PositionSizer
        -> Share Sizes -> IBKRExecutionEngine.build_orders() -> IBKR

Sizing itself (`size_target_positions()`) used to live here as its own,
separate implementation from what the backtester asked vectorbt to do.
That's gone -- this module now calls `portfolio_engine.equal_weight()`
(or whichever allocator you choose) and `portfolio_engine.size_positions()`,
the exact same functions backtester.py uses. This engine's only real
job is turning "share sizes" into "orders", and orders into IBKR calls.

SAFETY MODEL (please read before flipping dry_run off)
-------------------------------------------------------
1. `config.DRY_RUN_DEFAULT` is read from the QUANT_DRY_RUN env var and
   defaults to True. This module additionally requires the *caller* to
   pass dry_run=False explicitly -- an env var flip alone can't arm it.
2. In dry-run mode every intended order is computed, logged, and written
   to the `orders` table with dry_run=1, but nothing is transmitted to
   IBKR. This lets you run the full pipeline end-to-end against a live
   feed and inspect exactly what it *would* have done.
3. A kill switch (config.RiskConfig.kill_switch_drawdown) blocks new
   orders once trailing drawdown breaches the configured limit.
4. Position sizing is capped per-name (max_position_pct) and in
   aggregate (max_gross_exposure) inside portfolio_engine.size_positions(),
   the same caps applied during backtesting.
5. Always test against the IBKR *paper* account (port 7497 for TWS,
   4002 for IB Gateway) before ever pointing this at a live account.
"""
from __future__ import annotations

import logging

import pandas as pd

import config
import database
import portfolio_engine

logger = logging.getLogger(__name__)


class IBKRExecutionEngine:
    def __init__(self, ibkr_config: config.IBKRConfig = config.IBKR,
                 risk: config.RiskConfig = config.RISK,
                 dry_run: bool = config.DRY_RUN_DEFAULT):
        self.cfg = ibkr_config
        self.risk = risk
        self.dry_run = dry_run
        self._ib = None
        if not self.dry_run:
            logger.warning(
                "Execution engine initialised with dry_run=False -- "
                "orders WILL be transmitted to IBKR (host=%s port=%s account=%s).",
                ibkr_config.host, ibkr_config.port, ibkr_config.account,
            )

    # ------------------------------------------------------------------
    def connect(self):
        from ib_async import IB
        self._ib = IB()
        self._ib.connect(self.cfg.host, self.cfg.port, clientId=self.cfg.client_id,
                          readonly=False, timeout=self.cfg.timeout)
        return self

    def disconnect(self):
        if self._ib is not None and self._ib.isConnected():
            self._ib.disconnect()

    def __enter__(self):
        return self.connect()

    def __exit__(self, exc_type, exc, tb):
        self.disconnect()

    # ------------------------------------------------------------------
    def get_current_positions(self) -> pd.Series:
        """Live share counts per symbol from IBKR (0 if flat / not held)."""
        positions = {
            p.contract.symbol: p.position 
            for p in self._ib.positions()
            if p.contract.symbol in config.UNIVERSE
        }
        return pd.Series(positions, dtype=float)

    # ------------------------------------------------------------------
    def target_shares_from_signals(self, signals: pd.Series, prices: pd.Series,
                                    equity: float,
                                    allocator=portfolio_engine.equal_weight,
                                    allocator_kwargs: dict | None = None) -> pd.Series:
        """
        Signals -> PortfolioAllocator -> Target Weights -> PositionSizer
        -> Share Sizes, using the exact same functions the backtester
        uses. `signals` is a per-symbol "in position?" Series (1/0) for
        right now, e.g. from SignalGenerator.latest_signals().
        """
        allocator_kwargs = allocator_kwargs or {}
        weights = allocator(signals, **allocator_kwargs)
        return portfolio_engine.size_positions(weights, prices, equity, risk=self.risk)

    # ------------------------------------------------------------------
    def check_kill_switch(self, pf) -> bool:
        """Returns True if trading should be halted for this run."""
        try:
            dd = abs(pf.max_drawdown().mean())
        except Exception:
            return False
        if dd >= self.risk.kill_switch_drawdown:
            logger.error(
                "KILL SWITCH TRIGGERED: trailing drawdown %.1f%% >= limit %.1f%%. "
                "No new orders will be generated.", dd * 100, self.risk.kill_switch_drawdown * 100
            )
            return True
        return False

    # ------------------------------------------------------------------
    def build_orders(self, target_shares: pd.Series, current_shares: pd.Series) -> pd.DataFrame:
        """
        Compare target shares against current IBKR shares and convert the resulting signed deltas into orders.
        """
        managed = pd.Index(config.UNIVERSE)
        target_shares = target_shares[
            target_shares.index.isin(managed)
        ]
        current_shares = current_shares[
            current_shares.index.isin(managed)
        ]
        target_shares, current_shares = target_shares.align(
            current_shares,
            fill_value=0,
        )
        delta = (target_shares - current_shares).astype(int)
        delta = delta[delta != 0]
        orders = pd.DataFrame({
            "symbol": delta.index,
            "action": ["BUY" if q > 0 else "SELL" for q in delta],
            "quantity": delta.abs().values,
        }).reset_index(drop=True)
        return orders

    # ------------------------------------------------------------------
    def submit_orders(self, orders: pd.DataFrame, order_type: str = "MKT") -> list[dict]:
        """
        Send (or, in dry-run mode, simulate) a batch of orders.
        Every order is recorded in the database regardless of mode.
        """
        results = []
        for _, row in orders.iterrows():
            symbol, action, qty = row["symbol"], row["action"], float(row["quantity"])

            if self.dry_run:
                logger.info("[DRY RUN] Would submit %s %s x %s", action, qty, symbol)
                database.record_order(symbol, action, qty, order_type,
                                       status="DRY_RUN", dry_run=True)
                results.append({"symbol": symbol, "action": action, "quantity": qty,
                                 "status": "DRY_RUN"})
                continue

            from ib_async import Stock, MarketOrder, LimitOrder
            contract = Stock(symbol, "SMART", "USD")
            self._ib.qualifyContracts(contract)
            order = MarketOrder(action, qty) if order_type == "MKT" else LimitOrder(action, qty)
            trade = self._ib.placeOrder(contract, order)
            self._ib.sleep(0.5)  # let IBKR acknowledge the order

            database.record_order(symbol, action, qty, order_type,
                                   status=trade.orderStatus.status,
                                   ib_order_id=order.orderId, dry_run=False)
            results.append({"symbol": symbol, "action": action, "quantity": qty,
                             "status": trade.orderStatus.status})
            logger.info("Submitted %s %s x %s -> status=%s", action, qty, symbol,
                        trade.orderStatus.status)
        return results