"""
database.py
------------
Market-data persistence layer. Same code path works against SQLite
(local/dev) or PostgreSQL (shared/production) -- only DATABASE_URL changes.

Tables
------
bars    : OHLCV bars per symbol/timeframe (the "market database" box)
signals : generated signals (audit trail from Signal Generator)
orders  : orders sent by the Execution Engine (audit trail / fills)
"""
from __future__ import annotations

import logging
from datetime import datetime
from dataclasses import field

import pandas as pd

from sqlalchemy import (
    Column, DateTime, Float, Integer, String, UniqueConstraint,
    create_engine, select, delete
)
from sqlalchemy.orm import declarative_base, sessionmaker

import config

logger = logging.getLogger(__name__)

Base = declarative_base()
_engine = None
_SessionFactory = None


class Bar(Base):
    __tablename__ = "bars"
    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(16), nullable=False, index=True)
    timeframe = Column(String(8), nullable=False, default="1d")
    ts = Column(DateTime, nullable=False, index=True)
    open = Column(Float)
    high = Column(Float)
    low = Column(Float)
    close = Column(Float)
    volume = Column(Float)

    __table_args__ = (
        UniqueConstraint("symbol", "timeframe", "ts", name="uix_bar"),
    )


class SignalRecord(Base):
    __tablename__ = "signals"
    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(16), nullable=False, index=True)
    strategy = Column(String(64), nullable=False)
    ts = Column(DateTime, nullable=False, index=True)
    signal = Column(Integer, nullable=False)  # 1 = long, -1 = flat/short, 0 = no-op
    created_at = Column(DateTime, default=datetime.utcnow)


class OrderRecord(Base):
    __tablename__ = "orders"
    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(16), nullable=False, index=True)
    action = Column(String(8), nullable=False)   # BUY / SELL
    quantity = Column(Float, nullable=False)
    order_type = Column(String(16), nullable=False)
    status = Column(String(32), default="CREATED")
    ib_order_id = Column(Integer, nullable=True)
    dry_run = Column(Integer, default=1)
    created_at = Column(DateTime, default=datetime.utcnow)


def get_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(config.DATABASE_URL, future=True)
    return _engine


def init_db() -> None:
    """Create all tables if they don't already exist."""
    Base.metadata.create_all(get_engine())
    logger.info("Database ready at %s", config.DATABASE_URL)


def get_session():
    global _SessionFactory
    if _SessionFactory is None:
        _SessionFactory = sessionmaker(bind=get_engine(), future=True)
    return _SessionFactory()


def upsert_bars(symbol: str, df: pd.DataFrame, timeframe: str = "1d") -> int:
    """
    Insert new bars for a symbol, skipping timestamps already stored.
    df must be indexed by timestamp with columns: open, high, low, close, volume
    """
    if df.empty:
        return 0

    session = get_session()
    try:
        existing = set(
            session.execute(
                select(Bar.ts).where(Bar.symbol == symbol, Bar.timeframe == timeframe)
            ).scalars().all()
        )
        new_rows = []
        for ts, row in df.iterrows():
            ts_naive = pd.Timestamp(ts).to_pydatetime().replace(tzinfo=None)
            if ts_naive in existing:
                continue
            new_rows.append(Bar(
                symbol=symbol,
                timeframe=timeframe,
                ts=ts_naive,
                open=float(row.get("open", row.get("Open", float("nan")))),
                high=float(row.get("high", row.get("High", float("nan")))),
                low=float(row.get("low", row.get("Low", float("nan")))),
                close=float(row.get("close", row.get("Close", float("nan")))),
                volume=float(row.get("volume", row.get("Volume", 0.0)) or 0.0),
            ))
        if new_rows:
            session.bulk_save_objects(new_rows)
            session.commit()
        return len(new_rows)
    finally:
        session.close()


def load_bars(
    symbols: list[str] | str,
    start: str | None = None,
    end: str | None = None,
    timeframe: str = "1d",
    field: str = "close",
) -> pd.DataFrame:
    """
    Load stored bars and return a wide DataFrame of the requested OHLCV field.
    Parameters
    ----------
    field : str
        One of:
            "open", "high", "low", "close", "volume"
        Defaults to "close" so all existing callers continue to work
        without modification.
    """
    if isinstance(symbols, str):
        symbols = [symbols]

    engine = get_engine()
    query = select(Bar).where(Bar.symbol.in_(symbols), Bar.timeframe == timeframe)
    if start:
        query = query.where(Bar.ts >= pd.Timestamp(start).to_pydatetime())
    if end:
        query = query.where(Bar.ts <= pd.Timestamp(end).to_pydatetime())

    df = pd.read_sql(query, engine)
    if df.empty:
        return pd.DataFrame(columns=symbols)

    field = field.lower()

    if field not in {"open", "high", "low", "close", "volume"}:
        raise ValueError(
            f"Unsupported field '{field}'. "
            "Choose one of: open, high, low, close, volume."
        )

    wide = (
        df.pivot(index="ts", columns="symbol", values=field)
        .sort_index()
    )

    wide.index.name = "datetime"

    return wide[[s for s in symbols if s in wide.columns]]


def load_ohlcv(symbol: str, start: str | None = None, end: str | None = None,
                timeframe: str = "1d") -> pd.DataFrame:
    """Load full OHLCV for a single symbol (used by the Feature Engine)."""
    engine = get_engine()
    query = select(Bar).where(Bar.symbol == symbol, Bar.timeframe == timeframe)
    if start:
        query = query.where(Bar.ts >= pd.Timestamp(start).to_pydatetime())
    if end:
        query = query.where(Bar.ts <= pd.Timestamp(end).to_pydatetime())
    df = pd.read_sql(query, engine)
    if df.empty:
        return df
    df = df.set_index("ts").sort_index()[["open", "high", "low", "close", "volume"]]
    df.index.name = "datetime"
    return df


def record_signal(symbol: str, strategy: str, ts, signal: int) -> None:
    session = get_session()
    try:
        session.add(SignalRecord(symbol=symbol, strategy=strategy,
                                  ts=pd.Timestamp(ts).to_pydatetime(), signal=signal))
        session.commit()
    finally:
        session.close()


def record_order(symbol: str, action: str, quantity: float, order_type: str,
                  status: str = "CREATED", ib_order_id: int | None = None,
                  dry_run: bool = True) -> None:
    session = get_session()
    try:
        session.add(OrderRecord(
            symbol=symbol, action=action, quantity=quantity, order_type=order_type,
            status=status, ib_order_id=ib_order_id, dry_run=int(dry_run),
        ))
        session.commit()
    finally:
        session.close()