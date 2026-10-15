from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from overnight.decide import Session, Snapshot, decide, entry_order

ET = ZoneInfo("America/New_York")
MON = date(2026, 10, 12)


def at(day: date, hh: int, mm: int) -> datetime:
    return datetime(day.year, day.month, day.day, hh, mm, tzinfo=ET)


def snap(now, qty=0, open_buy=False, open_sell=False, last_entry=None, halted=False,
         close=(16, 0), trading_day=True):
    d = now.date()
    session = Session(at(d, 9, 30), at(d, *close)) if trading_day else None
    return Snapshot(now, session, qty, open_buy, open_sell, last_entry, halted)


def run(s):
    return decide(s, entry_window_min=30, entry_cutoff_min=11)


@pytest.mark.parametrize("hh,mm,action", [
    (9, 0, "wait"), (12, 0, "wait"), (15, 29, "wait"),
    (15, 30, "evaluate_entry"), (15, 45, "evaluate_entry"),
    (15, 50, "wait"), (16, 5, "wait"),
])
def test_entry_window(hh, mm, action):
    assert run(snap(at(MON, hh, mm))).action == action


def test_half_day_entry_window_follows_early_close():
    assert run(snap(at(MON, 12, 40), close=(13, 0))).action == "evaluate_entry"
    assert run(snap(at(MON, 15, 40), close=(13, 0))).action == "wait"


def test_entry_only_once_per_day():
    assert run(snap(at(MON, 15, 40), last_entry=MON)).action == "wait"


def test_no_entry_when_halted_or_holiday_or_buy_pending():
    assert run(snap(at(MON, 15, 40), halted=True)).action == "wait"
    assert run(snap(at(MON, 15, 40), trading_day=False)).action == "wait"
    assert run(snap(at(MON, 15, 40), open_buy=True)).action == "wait"


def test_after_close_fill_waits_until_7pm_then_queues_opening_sell():
    assert run(snap(at(MON, 16, 5), qty=5, last_entry=MON)).action == "wait"
    assert run(snap(at(MON, 18, 59), qty=5, last_entry=MON)).action == "wait"
    d = run(snap(at(MON, 19, 0), qty=5, last_entry=MON))
    assert (d.action, d.tif) == ("sell", "opg")


def test_morning_before_928_queues_opening_sell():
    tue = date(2026, 10, 13)
    d = run(snap(at(tue, 9, 15), qty=5, last_entry=MON))
    assert (d.action, d.tif) == ("sell", "opg")
    assert run(snap(at(tue, 9, 29), qty=5, last_entry=MON)).action == "wait"


def test_missed_auction_sells_at_market():
    tue = date(2026, 10, 13)
    d = run(snap(at(tue, 9, 35), qty=5, last_entry=MON))
    assert (d.action, d.tif) == ("sell", "day")


def test_never_sells_shares_bought_today_during_session():
    assert run(snap(at(MON, 15, 55), qty=5, last_entry=MON)).action == "wait"


def test_no_duplicate_sell():
    assert run(snap(at(MON, 20, 0), qty=5, open_sell=True, last_entry=MON)).action == "wait"


def test_weekend_hold_queues_sell_in_evening_only():
    sat = date(2026, 10, 17)
    assert run(snap(at(sat, 12, 0), qty=5, trading_day=False)).action == "wait"
    assert run(snap(at(sat, 20, 0), qty=5, trading_day=False)).tif == "opg"


def test_entry_order_sizing():
    qty, limit = entry_order(last_price=91.64, buying_power=500, max_position_usd=500, limit_pct=0.01)
    assert limit == 92.56 and qty == 5 and qty * limit <= 500
    assert entry_order(91.64, 100_000, 500, 0.01)[0] == 5      # capped by max position
    assert entry_order(91.64, 80, 500, 0.01)[0] == 0           # can't afford a share
