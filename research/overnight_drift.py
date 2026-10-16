"""Does the NY Fed "overnight drift" finding help this bot?

Boyarchenko, Larsen & Whelan, "The Overnight Drift" (NY Fed Staff Report 917):
overnight bounces are much stronger after selling into the close, and stronger
when the VIX is high. Their data is S&P 500 futures order flow; here the proxy
for "selling into the close" is a down day in daily ETF data.

Part 1 checks whether the pattern shows up in SPY. Part 2 backtests strategy
variants on SPY and TQQQ with trading costs.

Run: .venv/bin/python research/overnight_drift.py
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from overnight.backtest import TRADING_DAYS, Taxes, after_tax_strategy, session_returns  # noqa: E402
from overnight.data import load_daily  # noqa: E402
from overnight.strategy import Filters, hold_mask  # noqa: E402

pd.set_option("display.width", 160)


def load(ticker: str) -> pd.DataFrame:
    df = load_daily(ticker)
    vix = load_daily("^VIX")["Close"].rename("VIX")
    return df.join(vix, how="left").ffill()


def conditional_table(df: pd.DataFrame, start: str, end: str | None) -> pd.DataFrame:
    """Average next-overnight return (basis points) by what happened today."""
    d = df.loc[start:end]
    nxt = session_returns(d)["overnight"].shift(-1).reindex(d.index)  # tonight's hold
    today_intraday = d["Close"] / d["Open"] - 1
    today_daily = d["Close"].pct_change()
    rows = {
        "All nights": nxt,
        "After up day (close > prev close)": nxt[today_daily > 0],
        "After down day (close < prev close)": nxt[today_daily < 0],
        "After down >1%": nxt[today_daily < -0.01],
        "After down >2%": nxt[today_daily < -0.02],
        "After intraday drop (close < open)": nxt[today_intraday < 0],
        "VIX < 15": nxt[d["VIX"] < 15],
        "VIX 15-20": nxt[(d["VIX"] >= 15) & (d["VIX"] < 20)],
        "VIX 20-30": nxt[(d["VIX"] >= 20) & (d["VIX"] < 30)],
        "VIX >= 30": nxt[d["VIX"] >= 30],
        "Down day AND VIX >= 20": nxt[(today_daily < 0) & (d["VIX"] >= 20)],
        "Up day AND VIX >= 20": nxt[(today_daily > 0) & (d["VIX"] >= 20)],
    }
    out = pd.DataFrame({
        "nights": {k: v.dropna().size for k, v in rows.items()},
        "avg bps": {k: 1e4 * v.mean() for k, v in rows.items()},
        "win %": {k: 100 * (v.dropna() > 0).mean() for k, v in rows.items()},
        "t-stat": {k: v.mean() / (v.std() / np.sqrt(v.dropna().size)) for k, v in rows.items()},
    })
    return out


def run_mask(df: pd.DataFrame, mask: pd.Series, bps_per_side: float) -> pd.Series:
    rets = session_returns(df)
    held = mask.shift(1).reindex(rets.index).fillna(False).astype(bool)
    return (rets["overnight"] - 2 * bps_per_side / 1e4).where(held, 0.0), held


def summarize(net: pd.Series, held: pd.Series) -> dict:
    eq = (1 + net).cumprod()
    years = len(net) / TRADING_DAYS
    cagr = eq.iloc[-1] ** (1 / years) - 1
    vol = net.std() * np.sqrt(TRADING_DAYS)
    return {
        "CAGR %": 100 * cagr,
        "vol %": 100 * vol,
        "max DD %": 100 * (eq / eq.cummax() - 1).min(),
        "Sharpe": cagr / vol if vol else np.nan,
        "held %": 100 * held.mean(),
        "bps/held night": 1e4 * net[held].mean() if held.any() else np.nan,
        "after-tax CAGR %": 100 * (after_tax_strategy(net, Taxes()) ** (1 / years) - 1),
    }


def variants(df: pd.DataFrame) -> dict[str, pd.Series]:
    daily = df["Close"].pct_change()
    trend = hold_mask(df, Filters(trend_sma_days=200))
    return {
        "A. Every night": hold_mask(df, Filters()),
        "B. Trend 200 (current bot)": trend,
        "C. Only after intraday drop": hold_mask(df, Filters(down_day="intraday")),
        "D. Only after down day": hold_mask(df, Filters(down_day="daily")),
        "E. Trend AND down day": hold_mask(df, Filters(trend_sma_days=200, down_day="daily")),
        "F. Trend OR down day": hold_mask(df, Filters(trend_sma_days=200, down_day="daily",
                                                      down_day_overrides_trend=True)),
        "G. Trend OR down >1%": hold_mask(df, Filters(trend_sma_days=200, down_day="daily",
                                                      down_day_min_pct=0.01,
                                                      down_day_overrides_trend=True)),
        "H. Only when VIX >= 20": df["VIX"] >= 20,
        "I. Trend OR (down day & VIX >= 20)": trend | ((daily < 0) & (df["VIX"] >= 20)),
    }


def strategy_table(df: pd.DataFrame, start: str, end: str | None, bps: float) -> pd.DataFrame:
    # Masks are computed on the full history so the 200-day average is warmed up.
    masks = variants(df)
    window = df.loc[start:end]
    rows = {}
    for name, mask in masks.items():
        net, held = run_mask(window, mask.reindex(window.index), bps)
        rows[name] = summarize(net, held)
    bh = session_returns(window)["buy_hold"]
    rows["Buy & hold"] = summarize(bh, pd.Series(True, index=bh.index))
    return pd.DataFrame(rows).T


def main() -> None:
    fmt = lambda x: f"{x:,.2f}"  # noqa: E731
    pd.set_option("display.float_format", fmt)
    spy, tqqq = load("SPY"), load("TQQQ")

    print("=" * 100)
    print("PART 1: Is the next overnight return better after down days / high VIX?  (SPY)")
    for start, end in [("1993", None), ("1993", "2009"), ("2010", None)]:
        print(f"\nSPY {start}-{end or 'now'}")
        print(conditional_table(spy, start, end).to_string())

    print("\n" + "=" * 100)
    print("PART 2: Strategy variants (costs: SPY 0.25 bp/side, TQQQ 0.75 bp/side, 22% tax)")
    for name, df, start, end, bps in [
        ("SPY", spy, "1994", None, 0.25),
        ("SPY", spy, "1994", "2009", 0.25),
        ("SPY", spy, "2010", None, 0.25),
        ("TQQQ", tqqq, "2011", None, 0.75),
        ("TQQQ", tqqq, "2018", None, 0.75),
    ]:
        print(f"\n{name} {start}-{end or 'now'}")
        print(strategy_table(df, start, end, bps).to_string())


if __name__ == "__main__":
    main()
