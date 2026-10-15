"""Thin wrapper around alpaca-py: only the calls the bot needs."""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
from alpaca.common.exceptions import APIError
from alpaca.data.enums import Adjustment, DataFeed
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest, StockLatestTradeRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, QueryOrderStatus, TimeInForce
from alpaca.trading.requests import (
    GetCalendarRequest, GetOrdersRequest, LimitOrderRequest, MarketOrderRequest,
)

from overnight.decide import Session


@dataclass(frozen=True)
class Account:
    equity: float
    # What the bot may spend: settled + unsettled cash, never margin. Alpaca
    # accounts under $2,000 are "limited margin" and may trade on unsettled
    # funds, so there are no good-faith violations; using cash rather than
    # buying power also means the bot never borrows, even above $2,000.
    spendable: float
    problems: tuple[str, ...]  # account flags that should stop new entries
    daytrade_count: int


@dataclass(frozen=True)
class Fill:
    order_id: str
    filled_at: datetime  # ET
    side: str            # "buy" or "sell"
    qty: float
    price: float


ET = ZoneInfo("America/New_York")


class Broker:
    def __init__(self, api_key: str, secret_key: str, paper: bool, symbol: str):
        if not api_key or not secret_key:
            raise SystemExit("Missing ALPACA_API_KEY / ALPACA_SECRET_KEY in .env")
        self.symbol = symbol
        self.trading = TradingClient(api_key, secret_key, paper=paper)
        self.data = StockHistoricalDataClient(api_key, secret_key)

    # --- time -----------------------------------------------------------------
    def now(self) -> datetime:
        return self.trading.get_clock().timestamp.astimezone(ET)

    def session(self, day: date) -> Session | None:
        cal = self.trading.get_calendar(GetCalendarRequest(start=day, end=day))
        if not cal or cal[0].date != day:
            return None
        # Calendar times are naive Eastern time.
        return Session(cal[0].open.replace(tzinfo=ET), cal[0].close.replace(tzinfo=ET))

    # --- account --------------------------------------------------------------
    def account(self) -> Account:
        a = self.trading.get_account()
        problems = tuple(name for name in ("account_blocked", "trading_blocked", "trade_suspended_by_user")
                         if getattr(a, name))
        if a.status.value != "ACTIVE":
            problems += (f"status={a.status.value}",)
        return Account(equity=float(a.equity),
                       spendable=max(0.0, min(float(a.cash), float(a.buying_power))),
                       problems=problems, daytrade_count=int(a.daytrade_count or 0))

    def position_qty(self) -> int:
        try:
            return int(float(self.trading.get_open_position(self.symbol).qty))
        except APIError as e:
            if e.status_code == 404:
                return 0
            raise

    def open_orders(self) -> list:
        return self.trading.get_orders(GetOrdersRequest(
            status=QueryOrderStatus.OPEN, symbols=[self.symbol]))

    def recent_orders(self, days: int) -> list:
        return self.trading.get_orders(GetOrdersRequest(
            status=QueryOrderStatus.CLOSED, symbols=[self.symbol], limit=500,
            after=datetime.now(ET) - timedelta(days=days)))

    def filled_orders(self, days: int = 7) -> list[Fill]:
        """Filled (or partly filled) orders for the symbol, oldest first."""
        out = [Fill(str(o.id), o.filled_at.astimezone(ET), o.side.value,
                    float(o.filled_qty), float(o.filled_avg_price))
               for o in self.recent_orders(days)
               if o.filled_at and float(o.filled_qty or 0) > 0]
        return sorted(out, key=lambda f: f.filled_at)

    # --- orders ---------------------------------------------------------------
    def buy(self, qty: int, limit: float, tif: str):
        """Limit buy: tif "cls" joins the closing auction, "day" trades now."""
        return self.trading.submit_order(LimitOrderRequest(
            symbol=self.symbol, qty=qty, side=OrderSide.BUY,
            time_in_force=TimeInForce(tif), limit_price=limit))

    def sell(self, qty: int, tif: str):
        return self.trading.submit_order(MarketOrderRequest(
            symbol=self.symbol, qty=qty, side=OrderSide.SELL,
            time_in_force=TimeInForce(tif)))

    def cancel_open_orders(self) -> int:
        orders = self.open_orders()
        for o in orders:
            self.trading.cancel_order_by_id(o.id)
        return len(orders)

    # --- market data ----------------------------------------------------------
    def last_price(self) -> float:
        trades = self.data.get_stock_latest_trade(StockLatestTradeRequest(
            symbol_or_symbols=self.symbol, feed=DataFeed.IEX))
        return float(trades[self.symbol].price)

    def daily_closes(self, before: date, days: int) -> pd.DataFrame:
        """Daily closes for sessions before `before`.

        Free data plans reject queries reaching into the most recent 15 minutes
        of consolidated data, so the window ends at the earlier of midnight
        starting `before` and 20 minutes ago.
        """
        end = min(datetime.combine(before, datetime.min.time(), ET),
                  datetime.now(ET) - timedelta(minutes=20))
        bars = self.data.get_stock_bars(StockBarsRequest(
            symbol_or_symbols=self.symbol, timeframe=TimeFrame.Day,
            start=datetime.combine(before - timedelta(days=int(days * 1.6) + 10), datetime.min.time(), ET),
            end=end,
            adjustment=Adjustment.SPLIT))  # matches the backtest data
        df = bars.df.reset_index()
        df["Date"] = pd.to_datetime(df["timestamp"]).dt.tz_convert(ET).dt.tz_localize(None).dt.normalize()
        return df.set_index("Date")[["close"]].rename(columns={"close": "Close"})
