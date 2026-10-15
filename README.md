# Overnight Trading Bot

**Current setup: TQQQ (3x Nasdaq-100), $500.** The backtests below use SPY/SPYM. See `run_backtest.py --ticker TQQQ` for TQQQ.

Buys an S&P 500 ETF in the **closing auction** and sells it in the **next opening auction**, so it only holds the market overnight. Runs on Alpaca.

*Educational project, not financial advice.*

## What the backtest says (SPY, 1993–2026)

| | Return/yr | Volatility | Worst drawdown | After 22% tax |
|---|---|---|---|---|
| Buy & hold | 10.9% | 18.5% | −55% | 10.1% |
| Overnight, every night, no costs | 10.1% | 10.6% | −33% | 7.9% |
| Overnight + 200-day trend filter (default) | 9.1% | 6.4% | −13% | 7.2% |
| Intraday only (open→close) | 0.7% | 15.2% | −69% | — |

Since 2018, on SPYM itself with the trend filter: 9.6%/yr, −14% worst drawdown, vs. 14.7%/yr and −34% for buy & hold.

**What that means:**
- Almost all of the market's gain has historically come overnight.
- The strategy does **not** beat buy-and-hold on returns, especially after taxes. What it offers is a much smoother ride.
- **Costs decide everything.** Every 1 bp (0.01%) of slippage per side costs about 5%/yr. That's why the bot only trades in the opening and closing auctions.

Reproduce:

```bash
.venv/bin/python run_backtest.py --trend 200 --bps 0 --yearly
.venv/bin/python run_backtest.py --ticker SPYM --start 2018 --trend 200 --bps 0.25 --capital 500
```

## How it trades

`EXECUTION=regular` (current setting, works in paper): buy with a normal limit order between 3:40 and 3:58 pm ET, then queue a market sell right after the close, which fills at the next open. `EXECUTION=auction` uses the auction orders below; Alpaca's paper simulator never fills those, so use it live only.

Auction mode:

| Time (ET) | Action |
|---|---|
| 3:30–3:49 pm | If the price is above its 200-day average, place a **limit-on-close** buy for whole shares (limit 1% above the current price). |
| 4:00 pm | The closing auction fills the buy. |
| after 7:00 pm (or before 9:28 am) | Queue a **market-on-open** sell. |
| 9:30 am | The opening auction fills the sell. If that's missed, the bot sells at market. |

- The bot runs `tick` every 5 minutes. Each tick looks at the clock and the account, and does at most one thing.
- Half-days are handled automatically from Alpaca's market calendar.

### Why SPYM

With $500 you can't buy a whole share of SPY (~$780), and Alpaca's auction orders require **whole shares**. SPYM tracks the S&P 500, trades ~$1.3B a day, and costs ~$92 a share, so 5 shares fit. Its overnight returns correlate 0.996 with SPY's.

## Rules and safety built in

- **No day trades, ever.** Shares are always held overnight, and the bot refuses to sell anything bought the same day. It warns you if Alpaca's day-trade count is ever above 0.
- **No good-faith violations.** Alpaca has no cash accounts. Accounts under $2,000 are "limited margin" accounts that may trade on unsettled funds.
- **Never borrows.** Orders are sized against `min(cash, buying power)` at the limit price and capped at `MAX_POSITION_USD`, so a margin balance or margin call can't happen.
- **Account flags.** If Alpaca marks the account blocked, suspended or not active, new buys stop and you get a notification.
- **Kill switch.** Create a file named `STOP` in this folder to block new buys. Any shares already held still get sold.
- **Drawdown halt.** New buys stop if equity drops 20% below its peak.
- **Live trading is locked behind two switches:** `ALPACA_PAPER=false` in `.env` **and** `--live` on the command.
- **Notifications.** macOS notifications on every buy, sell, skip, and error. Full log in `logs/bot.log`.

## Setup

1. Create an Alpaca account and open the **Paper Trading** dashboard. Reset the paper account balance to **$500** so it behaves like the real account.
2. Generate paper API keys and paste them into `.env`. The template is `.env.example`.
3. Check the connection:
   ```bash
   .venv/bin/python run_bot.py status
   ```
4. Start the 5-minute schedule:
   ```bash
   scripts/install_schedule.sh
   ```
5. Keep the Mac **awake and online** from 3:30 pm to 9:35 am ET on weekdays. If it's asleep during the entry window, the bot just skips that night, which is safe. A $5/month cloud server is the robust long-term option.
6. After a few weeks, review results:
   ```bash
   .venv/bin/python run_bot.py report
   ```
   Compare the fills against the backtest.

### Comparing several funds side by side (profiles)

`profiles/sso.env`, `profiles/upro.env` and `profiles/tqqq.env` each run the same strategy on a different fund. A profile only lists what differs from `.env`.

There are two ways to give the profiles money:
- **Separate paper accounts (cleanest):** paste each paper account's keys into its profile and set each account to $500.
- **One shared paper account:** leave the profile keys blank, set the `.env` paper account to $1,500, and each profile is capped at $500.

Commands:

```bash
.venv/bin/python run_bot.py status --profile all
PROFILE=all scripts/install_schedule.sh       # schedule all three
.venv/bin/python run_bot.py compare           # side-by-side paper results
```

### Going live (after 1–2 months of paper that matches the backtest)

1. Fund the live account and create **live** API keys. These are different from paper keys.
2. In `.env`, set the live keys and `ALPACA_PAPER=false`.
3. Reinstall the schedule in live mode:
   ```bash
   scripts/install_schedule.sh --live
   ```

### Commands

```bash
.venv/bin/python run_bot.py status     # account, position, orders, state
.venv/bin/python run_bot.py report     # nightly P&L
.venv/bin/python run_bot.py flatten    # emergency: cancel everything and sell
scripts/uninstall_schedule.sh          # stop the bot
.venv/bin/pytest                       # run tests
```

## Taxes

In a regular taxable account:
- Every round trip is a **short-term** trade.
- Selling at a loss and rebuying the same ETF that afternoon creates **wash sales**. The loss isn't gone; it's added to the cost basis of the new shares.
- Alpaca's 1099 handles the reporting, but expect a long form at tax time.

An IRA avoids all of this.

## Files

- `overnight/strategy.py`: entry filters, shared by the backtest and the live bot.
- `overnight/backtest.py`, `run_backtest.py`: the backtest.
- `overnight/decide.py`: pure decision logic. Fully unit-tested.
- `overnight/broker.py`: Alpaca API wrapper.
- `overnight/bot.py`, `run_bot.py`: tick runner, flatten, status, report.
- `tests/`: unit tests plus full simulated trading days against a fake broker.
