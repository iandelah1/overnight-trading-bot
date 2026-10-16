"""Entry rules shared by the backtest and the live bot.

The strategy: buy near the close, sell at the next open. Filters decide which
nights to skip. Keeping them here means the live bot trades exactly the rules
that were backtested.
"""

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class Filters:
    # Only hold overnight when the close is above its N-day simple moving average.
    trend_sma_days: int | None = None
    # Skip the Friday->Monday hold (two extra days of news risk).
    skip_weekends: bool = False
    # "Overnight drift" idea (NY Fed Staff Report 917): prices tend to bounce
    # overnight after selling into the close. "intraday" = close below today's
    # open, "daily" = close below yesterday's close, "" = off.
    down_day: str = ""
    # How big the drop must be, e.g. 0.01 = more than 1%. 0 = any drop.
    down_day_min_pct: float = 0.0
    # False: a down day is required on top of the trend filter (both must pass).
    # True: a down day lets the bot hold even when the trend filter says no.
    down_day_overrides_trend: bool = False


def hold_mask(history: pd.DataFrame, filters: Filters) -> pd.Series:
    """For every row of `history`, whether to hold from that close to the next open."""
    index = history.index
    weekend_ok = pd.Series(not filters.skip_weekends, index=index) | (index.dayofweek != 4)

    trend_ok = pd.Series(True, index=index)
    if filters.trend_sma_days:
        sma = history["Close"].rolling(filters.trend_sma_days).mean()
        trend_ok = (history["Close"] > sma).fillna(False)

    if not filters.down_day:
        return weekend_ok & trend_ok

    if filters.down_day == "intraday":
        change = history["Close"] / history["Open"] - 1
    elif filters.down_day == "daily":
        change = history["Close"].pct_change()
    else:
        raise ValueError(f"down_day must be 'intraday', 'daily' or '', not {filters.down_day!r}")
    down = (change < -filters.down_day_min_pct).fillna(False)

    if filters.down_day_overrides_trend:
        return weekend_ok & (trend_ok | down)
    return weekend_ok & trend_ok & down


def should_hold_tonight(history: pd.DataFrame, today: pd.Timestamp, filters: Filters) -> bool:
    """Decide whether to hold from `today`'s close to the next session's open.

    `history` has a DatetimeIndex and `Close` (plus `Open` for the intraday
    down-day rule) up to and including `today`. In live trading, today's row
    holds the latest price a few minutes before the close.
    """
    if filters.down_day == "intraday" and "Open" not in history:
        raise ValueError("the intraday down-day rule needs today's open price")
    return bool(hold_mask(history.loc[:today], filters).iloc[-1])
