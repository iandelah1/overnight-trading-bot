from overnight import config


def test_profile_overrides_env_and_blank_falls_back(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("ALPACA_API_KEY=base\nALPACA_SECRET_KEY=s\nSYMBOL=SPYM\nTREND_SMA_DAYS=200\n")
    (tmp_path / "profiles").mkdir()
    (tmp_path / "profiles" / "upro.env").write_text("ALPACA_API_KEY=\nSYMBOL=UPRO\nMAX_DRAWDOWN_PCT=0.5\n")
    monkeypatch.setattr(config, "ROOT", tmp_path)
    monkeypatch.setattr(config, "PROFILES_DIR", tmp_path / "profiles")
    monkeypatch.delenv("SYMBOL", raising=False)

    base = config.load_settings()
    upro = config.load_settings("upro")
    assert (base.symbol, base.max_drawdown_pct) == ("SPYM", 0.20)
    assert (upro.symbol, upro.max_drawdown_pct, upro.profile) == ("UPRO", 0.5, "upro")
    assert upro.api_key == "base"                      # blank key falls back to .env
    assert upro.filters.trend_sma_days == 200          # inherited
    assert config.list_profiles() == ["upro"]


def test_down_day_settings(tmp_path, monkeypatch):
    import pytest
    (tmp_path / ".env").write_text("DOWN_DAY=daily\nDOWN_DAY_OVERRIDES_TREND=true\nDOWN_DAY_MIN_PCT=0.01\n")
    monkeypatch.setattr(config, "ROOT", tmp_path)
    for k in ("DOWN_DAY", "DOWN_DAY_OVERRIDES_TREND", "DOWN_DAY_MIN_PCT"):
        monkeypatch.delenv(k, raising=False)
    f = config.load_settings().filters
    assert (f.down_day, f.down_day_overrides_trend, f.down_day_min_pct) == ("daily", True, 0.01)

    (tmp_path / ".env").write_text("DOWN_DAY=intraday\n")
    with pytest.raises(SystemExit):
        config.load_settings()
