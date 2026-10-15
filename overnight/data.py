"""Daily price history (unadjusted open/close plus dividends), cached on disk."""

from pathlib import Path

import pandas as pd
import yfinance as yf

CACHE_DIR = Path(__file__).resolve().parent.parent / "data"


def load_daily(ticker: str, refresh: bool = False) -> pd.DataFrame:
    """Return a DataFrame indexed by date with Open, Close, Dividends.

    Prices are unadjusted so open/close match what actually traded; dividends
    are kept separately so the backtest can credit them on the ex-date.
    """
    CACHE_DIR.mkdir(exist_ok=True)
    path = CACHE_DIR / f"{ticker}.csv"
    if path.exists() and not refresh:
        df = pd.read_csv(path, index_col=0, parse_dates=True)
    else:
        raw = yf.Ticker(ticker).history(period="max", auto_adjust=False, actions=True)
        if raw.empty:
            raise ValueError(f"No data for {ticker}")
        df = raw[["Open", "Close", "Dividends"]].copy()
        df.index = pd.DatetimeIndex(df.index.tz_localize(None).normalize(), name="Date")
        df.to_csv(path)
    return df[(df["Open"] > 0) & (df["Close"] > 0)]
