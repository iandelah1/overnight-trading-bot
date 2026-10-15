"""Entry rules shared by the backtest and the live bot.

The strategy: buy at the closing auction, sell at the next opening auction.
Filters decide which nights to skip. Keeping them here means the live bot
trades exactly the rules that were backtested.
"""

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class Filters:
    # Only hold overnight when the close is above its N-day simple moving average.
    trend_sma_days: int | None = None
    # Skip the Friday->Monday hold (two extra days of news risk).
    skip_weekends: bool = False


def should_hold_tonight(history: pd.DataFrame, today: pd.Timestamp, filters: Filters) -> bool:
    """Decide whether to hold from `today`'s close to the next session's open.

    `history` has a DatetimeIndex and a `Close` column with data up to and
    including `today`. In live trading, today's row holds the latest price a few
    minutes before the close.
    """
    if filters.skip_weekends and today.dayofweek == 4:
        return False
    if filters.trend_sma_days:
        closes = history.loc[:today, "Close"]
        if len(closes) < filters.trend_sma_days:
            return False
        if closes.iloc[-1] <= closes.iloc[-filters.trend_sma_days:].mean():
            return False
    return True


def hold_mask(history: pd.DataFrame, filters: Filters) -> pd.Series:
    """Vectorized `should_hold_tonight` for every row of `history`."""
    mask = pd.Series(True, index=history.index)
    if filters.skip_weekends:
        mask &= history.index.dayofweek != 4
    if filters.trend_sma_days:
        sma = history["Close"].rolling(filters.trend_sma_days).mean()
        mask &= (history["Close"] > sma).fillna(False)
    return mask
