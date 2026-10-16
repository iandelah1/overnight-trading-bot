"""Drive `tick` through two full trading days against a fake broker."""

from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from overnight import bot
from overnight.broker import Account, Fill
from overnight import journal
from overnight.config import Settings
from overnight.decide import Session
from overnight.strategy import Filters

ET = ZoneInfo("America/New_York")


@dataclass
class Order:
    side: str
    qty: int
    tif: str
    limit: float | None = None

    @property
    def side_enum(self):
        return SimpleNamespace(value=self.side)


class FakeBroker:
    def __init__(self, cash=500.0, price=91.0, closes_above_sma=True):
        self.cash, self.qty, self.price = cash, 0, price
        self.orders: list[Order] = []
        self.fills: list[tuple] = []
        self.fill_dates: list[tuple] = []
        self.fill_records: list[Fill] = []
        self.clock = datetime(2026, 10, 12, 9, 0, tzinfo=ET)  # Monday
        trend = 1 if closes_above_sma else -1
        self.history = pd.DataFrame(
            {"Close": [price - trend * (250 - i) * 0.05 for i in range(250)]},
            index=pd.bdate_range(end="2026-10-09", periods=250))

    # interface used by bot.tick
    def now(self): return self.clock
    def session(self, d: date):
        if d.weekday() >= 5:
            return None
        return Session(datetime(d.year, d.month, d.day, 9, 30, tzinfo=ET),
                       datetime(d.year, d.month, d.day, 16, 0, tzinfo=ET))
    problems: tuple = ()
    daytrades = 0
    def account(self):
        return Account(self.cash + self.qty * self.price, self.cash, self.problems, self.daytrades)
    def position_qty(self): return self.qty
    def open_orders(self): return [SimpleNamespace(side=o.side_enum) for o in self.orders]
    def last_price(self): return self.price
    def filled_orders(self, days=7): return list(self.fill_records)
    def daily_closes(self, before, days): return self.history.copy()
    def market_open(self):
        s = self.session(self.clock.date())
        return s is not None and s.open <= self.clock < s.close
    def fill(self, side, qty, px):
        sign = 1 if side == "buy" else -1
        self.cash -= sign * qty * px; self.qty += sign * qty
        self.fills.append((side, qty, px)); self.fill_dates.append((side, self.clock.date()))
        self.fill_records.append(Fill(f"o{len(self.fill_records)}", self.clock, side, qty, px))
    def buy(self, qty, limit, tif):
        assert qty * limit <= self.cash
        if tif == "day" and self.market_open():
            if self.price <= limit:  # marketable limit fills immediately
                self.fill("buy", qty, self.price)
            return
        self.orders.append(Order("buy", qty, tif, limit))
    def sell(self, qty, tif):
        assert qty <= self.qty
        if tif == "day" and self.market_open():
            self.fill("sell", qty, self.price)
            return
        self.orders.append(Order("sell", qty, tif))  # queued for the next open
    def cancel_open_orders(self):
        n = len(self.orders); self.orders.clear(); return n

    # simulation
    def advance_to(self, when: datetime, close_px=None, open_px=None):
        s = self.session(self.clock.date())
        if s and self.clock < s.close <= when:  # closing auction
            px = close_px or self.price
            for o in [o for o in self.orders if o.tif == "cls"]:
                if px <= o.limit:
                    self.fill("buy", o.qty, px)
                self.orders.remove(o)
            self.price = px
        nxt = self.session(when.date())
        if nxt and self.clock < nxt.open <= when:  # opening auction
            px = open_px or self.price
            self.price = px
            self.clock = nxt.open
            for o in [o for o in self.orders if o.tif in ("opg", "day")]:
                self.fill(o.side, o.qty, px)
                self.orders.remove(o)
        self.clock = when


@pytest.fixture
def settings(tmp_path, monkeypatch):
    monkeypatch.setattr(bot, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(journal, "JOURNAL_DIR", tmp_path / "journal")
    monkeypatch.setattr(bot, "STOP_FILE", tmp_path / "STOP")
    monkeypatch.setattr(bot, "notify", lambda msg, profile="": None)
    return Settings("k", "s", True, "SPYM", Filters(trend_sma_days=200), 500, 0.01, 0.20)


@pytest.fixture
def regular(settings):
    return replace(settings, execution="regular")


def run_day(b, s, day, open_px, close_px):
    """Tick every 5 minutes from 9:00 ET on `day` until 9:00 ET the next day."""
    t = datetime(day.year, day.month, day.day, 9, 0, tzinfo=ET)
    end = t + timedelta(days=1)
    while t < end:
        b.advance_to(t, close_px=close_px, open_px=open_px)
        bot.tick(s, b)
        t += timedelta(minutes=5)


def test_two_round_trips(settings):
    b = FakeBroker()
    run_day(b, settings, date(2026, 10, 12), open_px=90.5, close_px=91.0)
    run_day(b, settings, date(2026, 10, 13), open_px=92.0, close_px=92.0)
    b.advance_to(datetime(2026, 10, 14, 9, 31, tzinfo=ET), open_px=91.5)
    assert b.fills == [("buy", 5, 91.0), ("sell", 5, 92.0), ("buy", 5, 92.0), ("sell", 5, 91.5)]
    assert b.qty == 0
    assert b.cash == pytest.approx(500 + 5 * 1.0 - 5 * 0.5)


def test_trend_filter_blocks_entry(settings):
    b = FakeBroker(closes_above_sma=False)
    run_day(b, settings, date(2026, 10, 12), open_px=90.5, close_px=91.0)
    assert b.fills == []


def test_stop_file_blocks_entry_but_not_exit(settings):
    b = FakeBroker()
    run_day(b, settings, date(2026, 10, 12), open_px=90.5, close_px=91.0)
    bot.STOP_FILE.touch()
    run_day(b, settings, date(2026, 10, 13), open_px=92.0, close_px=92.0)
    assert [f[0] for f in b.fills] == ["buy", "sell"]


def test_close_above_limit_means_no_fill(settings):
    b = FakeBroker()
    run_day(b, settings, date(2026, 10, 12), open_px=91.0, close_px=95.0)  # +4.4% into close
    assert b.fills == [] and b.qty == 0


def test_friday_position_sold_monday(settings):
    b = FakeBroker()
    b.clock = datetime(2026, 10, 16, 9, 0, tzinfo=ET)
    for i, (o, c) in enumerate([(90.5, 91.0), (None, None), (None, None), (93.0, 93.0)]):
        run_day(b, settings, date(2026, 10, 16) + timedelta(days=i), open_px=o, close_px=c)
    assert b.fills[:2] == [("buy", 5, 91.0), ("sell", 5, 93.0)]


def test_account_problem_blocks_entry(settings):
    b = FakeBroker()
    b.problems = ("trading_blocked",)
    run_day(b, settings, date(2026, 10, 12), open_px=90.5, close_px=91.0)
    assert b.fills == []


def test_cash_never_exceeded_across_many_days(settings):
    b = FakeBroker(cash=500.0, price=81.0)
    px = 81.0
    for i in range(10):
        day = date(2026, 10, 12) + timedelta(days=i)
        px *= 1.004 if i % 3 else 0.995
        run_day(b, settings, day, open_px=px, close_px=px)
        assert b.cash >= 0


def no_day_trades(b):
    """Every sell must close shares bought on an earlier day."""
    open_buys = []
    for side, d in b.fill_dates:
        if side == "buy":
            open_buys.append(d)
        elif open_buys.pop(0) >= d:
            return False
    return True


def test_regular_mode_round_trips(regular):
    b = FakeBroker()
    run_day(b, regular, date(2026, 10, 12), open_px=91.0, close_px=91.5)
    run_day(b, regular, date(2026, 10, 13), open_px=92.0, close_px=92.0)
    b.advance_to(datetime(2026, 10, 14, 9, 31, tzinfo=ET), open_px=91.5)
    # Buys fill before the close at the intraday price; sells at the next open.
    assert b.fills == [("buy", 5, 91.0), ("sell", 5, 92.0), ("buy", 5, 92.0), ("sell", 5, 91.5)]
    assert b.qty == 0 and no_day_trades(b)


def test_regular_mode_sell_is_queued_right_after_the_close(regular):
    b = FakeBroker()
    run_day(b, regular, date(2026, 10, 12), open_px=91.0, close_px=91.5)
    # 9:00 the next morning: sell already waiting for the open, nothing sold yet.
    assert b.qty == 5 and [(o.side, o.tif) for o in b.orders] == [("sell", "day")]


def test_regular_mode_buy_window(regular):
    from overnight.decide import Snapshot, decide
    d = date(2026, 10, 12)
    sess = Session(datetime(2026, 10, 12, 9, 30, tzinfo=ET), datetime(2026, 10, 12, 16, 0, tzinfo=ET))
    act = lambda hh, mm: decide(Snapshot(datetime(2026, 10, 12, hh, mm, tzinfo=ET), sess, 0, False, False,
                                         None, False), regular.entry_window_minutes,
                                regular.entry_cutoff_minutes, "regular").action
    assert [act(15, m) for m in (35, 40, 55, 59)] == ["wait", "evaluate_entry", "evaluate_entry", "wait"]


def test_auction_mode_never_day_trades(settings):
    b = FakeBroker()
    for i in range(5):
        run_day(b, settings, date(2026, 10, 12) + timedelta(days=i), open_px=91.0, close_px=91.0)
    assert len(b.fills) >= 6 and no_day_trades(b)


def test_journal_records_each_fill_once_with_pnl(regular):
    import csv
    b = FakeBroker()
    run_day(b, regular, date(2026, 10, 12), open_px=91.0, close_px=91.5)
    run_day(b, regular, date(2026, 10, 13), open_px=92.0, close_px=92.0)
    b.advance_to(datetime(2026, 10, 14, 9, 31, tzinfo=ET), open_px=91.5)
    bot.tick(regular, b)
    bot.tick(regular, b)  # extra ticks must not duplicate rows
    rows = list(csv.DictReader(journal.journal_path("default").open()))
    assert [(r["side"], r["price"], r["pnl"]) for r in rows] == [
        ("buy", "91.0", ""), ("sell", "92.0", "5.0"), ("buy", "92.0", ""), ("sell", "91.5", "-2.5")]


def test_fill_notifications(regular, monkeypatch):
    sent = []
    monkeypatch.setattr(bot, "notify", lambda msg, profile="": sent.append(msg))
    b = FakeBroker()
    run_day(b, regular, date(2026, 10, 12), open_px=91.0, close_px=91.5)
    b.advance_to(datetime(2026, 10, 13, 9, 31, tzinfo=ET), open_px=92.0)
    bot.tick(regular, b)
    assert "BOUGHT 5 SPYM @ $91.00 ($455.00)" in sent
    assert "SOLD 5 SPYM @ $92.00 ($460.00): +$5.00 (+1.10%)" in sent


def test_notify_emails_via_sns_and_survives_failures(monkeypatch):
    import sys
    from types import ModuleType
    sent = []

    class FakeSNS:
        def publish(self, **kw):
            sent.append(kw)

    fake_boto3 = ModuleType("boto3")
    fake_boto3.client = lambda service, region_name: FakeSNS() if region_name == "us-east-2" else None
    monkeypatch.setitem(sys.modules, "boto3", fake_boto3)
    monkeypatch.setattr(bot, "alert_topic_arn", lambda p: "arn:aws:sns:us-east-2:123456789012:alerts")
    bot.notify("BOUGHT 6 TQQQ", "default")
    assert sent == [{"TopicArn": "arn:aws:sns:us-east-2:123456789012:alerts",
                     "Subject": "Overnight bot", "Message": "[default] BOUGHT 6 TQQQ"}]

    fake_boto3.client = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no network"))
    bot.notify("still fine", "default")  # must not raise


def test_down_day_rule_buys_below_trend_only_after_a_drop(regular, monkeypatch):
    sent = []
    monkeypatch.setattr(bot, "notify", lambda msg, profile="": sent.append(msg))
    f = Filters(trend_sma_days=200, down_day="daily", down_day_overrides_trend=True)
    s = replace(regular, filters=f)

    up = FakeBroker(closes_above_sma=False)   # downtrend; last close 91.05
    up.price = 91.10                           # but up today -> still skip
    run_day(up, s, date(2026, 10, 12), open_px=91.10, close_px=91.10)
    assert up.fills == []

    down = FakeBroker(closes_above_sma=False)
    down.clock = datetime(2026, 10, 13, 9, 0, tzinfo=ET)  # new day: the bot already decided Monday
    down.price = 90.50                         # down today -> bounce night, buy
    run_day(down, s, date(2026, 10, 13), open_px=90.50, close_px=90.50)
    assert down.fills and down.fills[0][0] == "buy", sent
    assert any("down-day bounce" in m for m in sent)
