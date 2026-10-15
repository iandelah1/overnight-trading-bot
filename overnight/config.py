"""Bot settings from .env, optionally overridden by a profile in profiles/<name>.env.

Profiles let several strategies (e.g. SSO, UPRO, TQQQ) run side by side. A
profile only needs the values that differ; blank values fall back to .env,
so profiles can share one Alpaca account or each use their own keys.
"""

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import dotenv_values

from overnight.strategy import Filters

ROOT = Path(__file__).resolve().parent.parent
PROFILES_DIR = ROOT / "profiles"
DEFAULT_PROFILE = "default"


@dataclass(frozen=True)
class Settings:
    api_key: str
    secret_key: str
    paper: bool
    symbol: str
    filters: Filters
    # Never put more than this many dollars into a position (lets a big paper
    # account behave like the real small one).
    max_position_usd: float
    # LOC buy limit, as a fraction above the last price. If the close prints
    # higher than this, the order doesn't fill and the bot sits out that night.
    entry_limit_pct: float
    # Halt new entries if equity falls this far below its high-water mark.
    max_drawdown_pct: float
    # "auction": limit-on-close buy, market-on-open sell (best fills, live only:
    # Alpaca's paper simulator never fills these). "regular": marketable limit
    # buy shortly before the close, market sell queued for the next open.
    execution: str = "auction"
    profile: str = DEFAULT_PROFILE

    @property
    def entry_window_minutes(self) -> int:
        """Start placing the buy this many minutes before the close."""
        return 30 if self.execution == "auction" else 20

    @property
    def entry_cutoff_minutes(self) -> int:
        """Stop trying this many minutes before the close. Alpaca rejects
        closing-auction orders in the final 10 minutes."""
        return 11 if self.execution == "auction" else 2


def list_profiles() -> list[str]:
    return sorted(p.stem for p in PROFILES_DIR.glob("*.env"))


def _non_blank(path: Path) -> dict[str, str]:
    return {k: v for k, v in dotenv_values(path).items() if v not in (None, "")}


def _values(profile: str) -> dict[str, str]:
    values = dict(os.environ)
    values.update(_non_blank(ROOT / ".env"))
    if profile != DEFAULT_PROFILE:
        path = PROFILES_DIR / f"{profile}.env"
        if not path.exists():
            raise SystemExit(f"No profile file {path}")
        values.update(_non_blank(path))
    return values


def _execution(value: str) -> str:
    value = value.strip().lower()
    if value not in ("auction", "regular"):
        raise SystemExit(f"EXECUTION must be 'auction' or 'regular', not {value!r}")
    return value


@lru_cache
def alert_topic_arn(profile: str = DEFAULT_PROFILE) -> str:
    """SNS topic for email alerts (ALERT_TOPIC_ARN); empty means no emails."""
    return _values(profile).get("ALERT_TOPIC_ARN", "").strip()


def load_settings(profile: str = DEFAULT_PROFILE) -> Settings:
    v = _values(profile)

    def flag(name: str, default: bool) -> bool:
        return v.get(name, str(default)).strip().lower() in ("1", "true", "yes")

    sma = v.get("TREND_SMA_DAYS", "").strip()
    return Settings(
        api_key=v.get("ALPACA_API_KEY", ""),
        secret_key=v.get("ALPACA_SECRET_KEY", ""),
        paper=flag("ALPACA_PAPER", True),
        symbol=v.get("SYMBOL", "SPYM").upper(),
        filters=Filters(trend_sma_days=int(sma) if sma else None,
                        skip_weekends=flag("SKIP_WEEKENDS", False)),
        max_position_usd=float(v.get("MAX_POSITION_USD", "500")),
        entry_limit_pct=float(v.get("ENTRY_LIMIT_PCT", "0.01")),
        max_drawdown_pct=float(v.get("MAX_DRAWDOWN_PCT", "0.20")),
        execution=_execution(v.get("EXECUTION", "auction")),
        profile=profile,
    )
