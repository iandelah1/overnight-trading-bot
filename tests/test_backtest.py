import pandas as pd
import pytest

from overnight.backtest import Costs, session_returns, simulate_account, strategy_returns
from overnight.strategy import Filters, hold_mask, should_hold_tonight


def frame(opens, closes, divs=None, start="2026-01-05"):
    idx = pd.bdate_range(start, periods=len(opens))
    return pd.DataFrame({"Open": opens, "Close": closes,
                         "Dividends": divs or [0.0] * len(opens)}, index=idx)


def test_overnight_and_intraday_split():
    df = frame([100, 102, 101], [101, 100, 103])
    r = session_returns(df)
    assert r["overnight"].tolist() == pytest.approx([102 / 101 - 1, 101 / 100 - 1])
    assert r["intraday"].tolist() == pytest.approx([100 / 102 - 1, 103 / 101 - 1])


def test_dividend_credited_to_overnight_hold():
    # Open drops by the $1 dividend: the overnight holder should break even.
    df = frame([100, 99], [100, 99], divs=[0.0, 1.0])
    assert session_returns(df)["overnight"].iloc[0] == pytest.approx(0.0)


def test_costs_charged_per_round_trip():
    df = frame([100, 100, 100], [100, 100, 100])
    net = strategy_returns(df, Filters(), Costs(bps_per_side=1.0))
    assert net.tolist() == pytest.approx([-0.0002, -0.0002])


def test_vectorized_mask_matches_live_rule():
    df = frame(list(range(100, 160)), [100 + (i % 7) * 3 + i * 0.2 for i in range(60)])
    f = Filters(trend_sma_days=10, skip_weekends=True)
    mask = hold_mask(df, f)
    for ts in df.index:
        assert mask[ts] == should_hold_tonight(df, ts, f)


def test_whole_share_sim():
    df = frame([90, 100], [92, 101])
    sim = simulate_account(df, Filters(), Costs(0), capital=500)
    # 5 shares at 92, sold at 100 -> +40; $40 idle cash never invested.
    assert sim["Shares"].iloc[0] == 5
    assert sim["Equity"].iloc[0] == pytest.approx(540)
