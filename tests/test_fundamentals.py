"""Testy modułu fundamentals (mapowanie symboli, yfinance, cache TTL, krypto)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# is_crypto
# ---------------------------------------------------------------------------


def test_is_crypto_recognises_usdt_and_exchange_prefixes():
    from fundamentals import is_crypto

    assert is_crypto("BTCUSDT") is True
    assert is_crypto("ETHUSDT") is True
    assert is_crypto("BINANCE:BTCUSDT") is True
    assert is_crypto("BITFINEX:ETHUSD") is True
    assert is_crypto("COINBASE:BTC") is True


def test_is_crypto_ignores_stocks_and_gpw():
    from fundamentals import is_crypto

    assert is_crypto("AAPL") is False
    assert is_crypto("GPW:TXT") is False
    assert is_crypto("NASDAQ:MSFT") is False
    # Para FX z giełdą prefix (USDPLN) — w configu nie ma takiego prefiksu krypto,
    # więc traktujemy jako nie-krypto.
    assert is_crypto("EURPLN") is False


# ---------------------------------------------------------------------------
# to_yahoo_symbol
# ---------------------------------------------------------------------------


def test_to_yahoo_symbol_mappings():
    from fundamentals import to_yahoo_symbol

    assert to_yahoo_symbol("GPW:TXT") == "TXT.WA"
    assert to_yahoo_symbol("NASDAQ:AAPL") == "AAPL"
    assert to_yahoo_symbol("NYSE:FCX") == "FCX"
    assert to_yahoo_symbol("AAPL") == "AAPL"
    assert to_yahoo_symbol("BTCUSDT") is None


def test_to_yahoo_symbol_exchange_suffixes():
    from fundamentals import to_yahoo_symbol

    assert to_yahoo_symbol("XETR:E3G1") == "E3G1.DE"
    assert to_yahoo_symbol("KRX:000660") == "000660.KS"
    assert to_yahoo_symbol("HKEX:0700") == "0700.HK"
    assert to_yahoo_symbol("000660") == "000660.KS"
    assert to_yahoo_symbol("0016") == "0016.HK"


def test_to_yahoo_symbol_uses_lookup_exchange(monkeypatch):
    from fundamentals import to_yahoo_symbol

    monkeypatch.setattr(
        "fundamentals._lookup_yahoo_suffix",
        lambda _sym: ".F",
    )
    assert to_yahoo_symbol("E3G1") == "E3G1.F"


# ---------------------------------------------------------------------------
# _yf_fetch
# ---------------------------------------------------------------------------


def test_yf_fetch_maps_info_fields(monkeypatch):
    from fundamentals import _yf_fetch

    fake_info = {
        "trailingPE": 12.5,
        "priceToBook": 2.1,
        "enterpriseToEbitda": 8.0,
        "returnOnEquity": 0.15,
        "profitMargins": 0.22,
        "debtToEquity": 45.0,
        "freeCashflow": 1_500_000_000,
        "dividendYield": 0.025,
        "dividendRate": 0.96,
    }

    class _FakeTicker:
        def __init__(self, _sym):
            self.info = fake_info

    import yfinance  # type: ignore

    monkeypatch.setattr(yfinance, "Ticker", _FakeTicker)
    data = _yf_fetch("AAPL")
    assert data is not None
    assert data["Fund_PE"] == pytest.approx(12.5)
    assert data["Fund_PB"] == pytest.approx(2.1)
    assert data["Fund_EV_EBITDA"] == pytest.approx(8.0)
    assert data["Fund_ROE"] == pytest.approx(0.15)
    assert data["Fund_NetMargin"] == pytest.approx(0.22)
    assert data["Fund_DE"] == pytest.approx(45.0)
    assert data["Fund_FCF"] == pytest.approx(1_500_000_000.0)
    assert data["Fund_DividendYield"] == pytest.approx(0.025)
    assert data["Fund_DividendRate"] == pytest.approx(0.96)


def test_yf_fetch_dividend_rate_fallback_to_trailing_annual(monkeypatch):
    from fundamentals import _yf_fetch

    fake_info = {
        "trailingPE": 20.0,
        "priceToBook": 3.0,
        "enterpriseToEbitda": 10.0,
        "returnOnEquity": 0.12,
        "profitMargins": 0.15,
        "debtToEquity": 30.0,
        "freeCashflow": 500_000_000,
        "dividendYield": 0.018,
        "trailingAnnualDividendRate": 1.25,
    }

    class _FakeTicker:
        def __init__(self, _sym):
            self.info = fake_info

    import yfinance  # type: ignore

    monkeypatch.setattr(yfinance, "Ticker", _FakeTicker)
    data = _yf_fetch("MSFT")
    assert data is not None
    assert data["Fund_DividendYield"] == pytest.approx(0.018)
    assert data["Fund_DividendRate"] == pytest.approx(1.25)


def test_yf_fetch_tolerates_missing_and_nan(monkeypatch):
    from fundamentals import _yf_fetch

    fake_info = {
        "trailingPE": float("nan"),
        "priceToBook": None,
        # Brakuje EV/EBITDA i ROE — kasowane na None
        "profitMargins": 0.1,
        "debtToEquity": "not-a-number",
        "freeCashflow": 0,
    }

    class _FakeTicker:
        def __init__(self, _sym):
            self.info = fake_info

    import yfinance  # type: ignore

    monkeypatch.setattr(yfinance, "Ticker", _FakeTicker)
    data = _yf_fetch("X")
    assert data is not None
    assert data["Fund_PE"] is None
    assert data["Fund_PB"] is None
    assert data["Fund_EV_EBITDA"] is None
    assert data["Fund_ROE"] is None
    assert data["Fund_NetMargin"] == pytest.approx(0.1)
    assert data["Fund_DE"] is None
    assert data["Fund_FCF"] == pytest.approx(0.0)


def test_yf_fetch_returns_none_when_info_empty(monkeypatch):
    from fundamentals import _yf_fetch

    class _FakeTicker:
        def __init__(self, _sym):
            self.info = {}

    import yfinance  # type: ignore

    monkeypatch.setattr(yfinance, "Ticker", _FakeTicker)
    assert _yf_fetch("X") is None


# ---------------------------------------------------------------------------
# fetch_fundamentals — crypto, cache TTL
# ---------------------------------------------------------------------------


def test_fetch_fundamentals_crypto_returns_source_none(tmp_path):
    from fundamentals import fetch_fundamentals, FUND_KEYS

    cache = tmp_path / "cache.json"
    data = fetch_fundamentals("BTCUSDT", cache_path=cache)
    assert data["Fund_Source"] == "none"
    for key in FUND_KEYS:
        assert data[key] is None
    # Powinniśmy też zapisać w cache (żeby nie spamować yfinance).
    assert cache.exists()
    on_disk = json.loads(cache.read_text(encoding="utf-8"))
    assert "BTCUSDT" in on_disk


def test_fetch_fundamentals_cache_hit_within_ttl(tmp_path, monkeypatch):
    """Cache hit: drugie wywołanie w obrębie TTL nie powinno wołać yfinance."""
    import fundamentals as mod

    cache = tmp_path / "cache.json"
    fake_clock = {"now": 1000.0}
    monkeypatch.setattr(mod.time, "time", lambda: fake_clock["now"])

    calls = []

    def _fake_yf(symbol):
        calls.append(symbol)
        return {
            "Fund_PE": 10.0,
            "Fund_PB": 1.0,
            "Fund_EV_EBITDA": 5.0,
            "Fund_ROE": 0.1,
            "Fund_NetMargin": 0.05,
            "Fund_DE": 0.5,
            "Fund_FCF": 100.0,
        }

    monkeypatch.setattr(mod, "_yf_fetch", _fake_yf)

    first = fetch_fundamentals_local(mod, "AAPL", cache)
    assert first["Fund_PE"] == pytest.approx(10.0)
    assert first["Fund_Source"] == "yfinance"
    assert calls == ["AAPL"]

    # 12 godzin później — wciąż w TTL=24h.
    fake_clock["now"] += 12 * 3600
    second = fetch_fundamentals_local(mod, "AAPL", cache)
    assert second["Fund_PE"] == pytest.approx(10.0)
    assert calls == ["AAPL"], "yfinance nie powinien być wołany przy cache hit"


def test_fetch_fundamentals_cache_miss_after_ttl(tmp_path, monkeypatch):
    """Po przekroczeniu TTL ponownie wołamy yfinance."""
    import fundamentals as mod

    cache = tmp_path / "cache.json"
    fake_clock = {"now": 1000.0}
    monkeypatch.setattr(mod.time, "time", lambda: fake_clock["now"])

    values = [
        {
            "Fund_PE": 10.0,
            "Fund_PB": 1.0,
            "Fund_EV_EBITDA": 5.0,
            "Fund_ROE": 0.1,
            "Fund_NetMargin": 0.05,
            "Fund_DE": 0.5,
            "Fund_FCF": 100.0,
        },
        {
            "Fund_PE": 11.0,
            "Fund_PB": 1.1,
            "Fund_EV_EBITDA": 5.5,
            "Fund_ROE": 0.12,
            "Fund_NetMargin": 0.06,
            "Fund_DE": 0.55,
            "Fund_FCF": 110.0,
        },
    ]

    def _fake_yf(_symbol):
        return values.pop(0) if values else None

    monkeypatch.setattr(mod, "_yf_fetch", _fake_yf)

    first = fetch_fundamentals_local(mod, "AAPL", cache, ttl_hours=24)
    assert first["Fund_PE"] == pytest.approx(10.0)

    # 25 godzin później — TTL minął.
    fake_clock["now"] += 25 * 3600
    second = fetch_fundamentals_local(mod, "AAPL", cache, ttl_hours=24)
    assert second["Fund_PE"] == pytest.approx(11.0)


def test_fetch_fundamentals_empty_cache_hit_within_ttl(tmp_path, monkeypatch):
    """Pusty wpis cache (source=none) nie woła yfinance ponownie w TTL."""
    import fundamentals as mod

    cache = tmp_path / "cache.json"
    fake_clock = {"now": 1000.0}
    monkeypatch.setattr(mod.time, "time", lambda: fake_clock["now"])

    calls = []

    def _fake_yf(symbol):
        calls.append(symbol)
        return {
            "Fund_PE": 10.0,
            "Fund_PB": None,
            "Fund_EV_EBITDA": None,
            "Fund_ROE": None,
            "Fund_NetMargin": None,
            "Fund_DE": None,
            "Fund_FCF": None,
        }

    monkeypatch.setattr(mod, "_yf_fetch", _fake_yf)

    cache.write_text(
        json.dumps(
            {
                "AAPL": {
                    "Ticker": "AAPL",
                    "Fund_PE": None,
                    "Fund_PB": None,
                    "Fund_EV_EBITDA": None,
                    "Fund_ROE": None,
                    "Fund_NetMargin": None,
                    "Fund_DE": None,
                    "Fund_FCF": None,
                    "Fund_Source": "none",
                    "Fund_Updated_At": "2026-05-22T10:00:00Z",
                    "_cached_at": fake_clock["now"],
                }
            }
        ),
        encoding="utf-8",
    )

    data = fetch_fundamentals_local(mod, "AAPL", cache, ttl_hours=24)
    assert data["Fund_Source"] == "none"
    assert calls == []


def test_fetch_fundamentals_empty_cache_retries_with_force_refresh(tmp_path, monkeypatch):
    """force_refresh omija pusty cache w TTL."""
    import fundamentals as mod

    cache = tmp_path / "cache.json"
    fake_clock = {"now": 1000.0}
    monkeypatch.setattr(mod.time, "time", lambda: fake_clock["now"])

    calls = []

    def _fake_yf(symbol):
        calls.append(symbol)
        return {
            "Fund_PE": 10.0,
            "Fund_PB": None,
            "Fund_EV_EBITDA": None,
            "Fund_ROE": None,
            "Fund_NetMargin": None,
            "Fund_DE": None,
            "Fund_FCF": None,
        }

    monkeypatch.setattr(mod, "_yf_fetch", _fake_yf)
    cache.write_text(
        json.dumps(
            {
                "AAPL": {
                    "Ticker": "AAPL",
                    "Fund_PE": None,
                    "Fund_PB": None,
                    "Fund_EV_EBITDA": None,
                    "Fund_ROE": None,
                    "Fund_NetMargin": None,
                    "Fund_DE": None,
                    "Fund_FCF": None,
                    "Fund_Source": "none",
                    "Fund_Updated_At": "2026-05-22T10:00:00Z",
                    "_cached_at": fake_clock["now"],
                }
            }
        ),
        encoding="utf-8",
    )

    data = fetch_fundamentals_local(
        mod, "AAPL", cache, ttl_hours=24, force_refresh=True
    )
    assert data["Fund_Source"] == "yfinance"
    assert data["Fund_PE"] == pytest.approx(10.0)
    assert calls == ["AAPL"]


def test_fetch_fundamentals_yfinance_then_none_records_source_none(tmp_path, monkeypatch):
    """Gdy yfinance pusty i brak fallbacku, ``Fund_Source='none'``."""
    import fundamentals as mod

    cache = tmp_path / "cache.json"
    monkeypatch.setattr(mod, "_yf_fetch_best", lambda _sym: None)
    data = fetch_fundamentals_local(mod, "GPW:UNKNOWN", cache)
    assert data["Fund_Source"] == "none"
    for key in mod.FUND_KEYS:
        assert data[key] is None


def test_fetch_fundamentals_bad_symbol_returns_none_without_error(tmp_path, monkeypatch):
    import fundamentals as mod

    cache = tmp_path / "cache.json"

    def _raise_on_fetch(_sym):
        raise RuntimeError("should not propagate")

    monkeypatch.setattr(mod, "_yf_fetch_best", lambda _sym: None)
    monkeypatch.setattr(mod, "_yf_fetch", _raise_on_fetch)

    data = mod.fetch_fundamentals("NOTAREALSYMBOL999", cache_path=cache, force_refresh=True)
    assert data["Fund_Source"] == "none"
    assert data["Ticker"] == "NOTAREALSYMBOL999"


def test_fetch_fundamentals_persists_yfinance_values(tmp_path, monkeypatch):
    """Udany fetch zapisuje Fund_PE i Fund_Source=yfinance."""
    import fundamentals as mod

    cache = tmp_path / "cache.json"
    monkeypatch.setattr(
        mod,
        "_yf_fetch",
        lambda _sym: {
            "Fund_PE": 12.5,
            "Fund_PB": 2.0,
            "Fund_EV_EBITDA": None,
            "Fund_ROE": None,
            "Fund_NetMargin": 0.1,
            "Fund_DE": None,
            "Fund_FCF": None,
        },
    )
    data = fetch_fundamentals_local(mod, "AAPL", cache, force_refresh=True)
    assert data["Fund_Source"] == "yfinance"
    assert data["Fund_PE"] == pytest.approx(12.5)
    on_disk = json.loads(cache.read_text(encoding="utf-8"))
    assert on_disk["AAPL"]["Fund_Source"] == "yfinance"
    assert on_disk["AAPL"]["Fund_PE"] == pytest.approx(12.5)


def test_fetch_fundamentals_tv_http_fallback_when_yfinance_empty(tmp_path, monkeypatch):
    """HTTP TV fallback uzupełnia GPW gdy yfinance pusty."""
    import fundamentals as mod

    cache = tmp_path / "cache.json"
    monkeypatch.setattr(mod, "_yf_fetch", lambda _sym: None)
    monkeypatch.setattr(
        mod,
        "_tv_financials_http_fallback",
        lambda _ticker: {
            "Fund_PE": 15.0,
            "Fund_PB": None,
            "Fund_EV_EBITDA": None,
            "Fund_ROE": None,
            "Fund_NetMargin": None,
            "Fund_DE": None,
            "Fund_FCF": None,
        },
    )
    data = mod.fetch_fundamentals(
        "GPW:SNT",
        cache_path=cache,
        force_refresh=True,
        tv_http_fallback=True,
    )
    assert data["Fund_Source"] == "tradingview"
    assert data["Fund_PE"] == pytest.approx(15.0)


def test_check_yfinance_available_when_import_fails(monkeypatch):
    import fundamentals as mod

    import builtins

    real_import = builtins.__import__

    def _fake_import(name, *args, **kwargs):
        if name == "yfinance":
            raise ImportError("No module named 'yfinance'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fake_import)
    ok, err = mod.check_yfinance_available()
    assert ok is False
    assert "yfinance" in (err or "").lower()


def test_fundamentals_row_has_values():
    from fundamentals import fundamentals_fetch_attempted, fundamentals_row_has_values

    assert fundamentals_row_has_values({"Fund_PE": 10.0, "Fund_Source": "yfinance"}) is True
    assert fundamentals_row_has_values({"Fund_PE": 10.0, "Fund_Source": "none"}) is True
    assert fundamentals_row_has_values({"Fund_Source": "yfinance"}) is False
    assert fundamentals_row_has_values({"Fund_Source": "none"}) is False
    assert fundamentals_row_has_values(None) is False
    assert fundamentals_fetch_attempted(
        {"Fund_Source": "none", "Fund_Updated_At": "2026-05-22T10:00:00Z"}
    ) is True
    assert fundamentals_fetch_attempted({"Fund_Source": "none"}) is True
    assert fundamentals_fetch_attempted(None) is False
    # Absurdalne P/E z TV nie liczy się jako sensowne dane.
    assert fundamentals_row_has_values(
        {"Fund_PE": 2.0132014201520162e51, "Fund_Source": "tradingview"}
    ) is False


# ---------------------------------------------------------------------------
# GPW:XTB — yfinance preferowany, TV bez śmieciowych lat wykresu
# ---------------------------------------------------------------------------


def test_gpw_xtb_yfinance_mock_returns_sensible_pe(tmp_path, monkeypatch):
    """GPW:XTB → XTB.WA; sensowne P/E z yfinance, bez TV fallback."""
    import fundamentals as mod

    cache = tmp_path / "cache.json"
    monkeypatch.setattr(
        mod,
        "_yf_fetch",
        lambda sym: {
            "Fund_PE": 13.01,
            "Fund_PB": 6.41,
            "Fund_EV_EBITDA": None,
            "Fund_ROE": None,
            "Fund_NetMargin": 0.367,
            "Fund_DE": 14.85,
            "Fund_FCF": None,
        }
        if sym == "XTB.WA"
        else None,
    )
    monkeypatch.setattr(
        mod,
        "_tv_financials_http_fallback",
        lambda _ticker: {"Fund_PE": 2.0132014201520162e51},
    )

    data = mod.fetch_fundamentals(
        "GPW:XTB",
        cache_path=cache,
        force_refresh=True,
        tv_http_fallback=True,
    )
    assert data["Fund_Source"] == "yfinance"
    assert data["Fund_PE"] == pytest.approx(13.01)
    assert data["Fund_PB"] == pytest.approx(6.41)
    assert data["Fund_NetMargin"] == pytest.approx(0.367)


def test_tv_chart_html_fixture_does_not_emit_absurd_pe():
    from fundamentals import _parse_tv_financials_html
    from pathlib import Path

    html = Path("tests/fixtures/tv_gpw_xtb_chart_pe.html").read_text(encoding="utf-8")
    parsed = _parse_tv_financials_html(html)
    assert parsed is None or parsed.get("Fund_PE") is None


def test_tv_table_html_fixture_extracts_pe_and_margins():
    from fundamentals import _parse_tv_financials_html
    from pathlib import Path

    html = Path("tests/fixtures/tv_gpw_xtb_table_pe.html").read_text(encoding="utf-8")
    parsed = _parse_tv_financials_html(html)
    assert parsed is not None
    assert parsed["Fund_PE"] == pytest.approx(13.01)
    assert parsed["Fund_PB"] == pytest.approx(6.41)
    assert parsed["Fund_NetMargin"] == pytest.approx(0.3675)
    assert parsed["Fund_DE"] == pytest.approx(14.85)


def test_sanity_filter_rejects_absurd_pe():
    from fundamentals import _sanitize_fund_values, _is_sane_fund_value

    assert _is_sane_fund_value("Fund_PE", 13.0) is True
    assert _is_sane_fund_value("Fund_PE", 0.0) is False
    assert _is_sane_fund_value("Fund_PE", -1.0) is False
    assert _is_sane_fund_value("Fund_PE", 1001.0) is False
    assert _is_sane_fund_value("Fund_PE", 2.0132014201520162e51) is False

    out = _sanitize_fund_values({"Fund_PE": 2.0132014201520162e51, "Fund_PB": 2.0})
    assert out["Fund_PE"] is None
    assert out["Fund_PB"] == pytest.approx(2.0)


def test_coerce_number_polish_and_thousand_separators():
    from fundamentals import _coerce_number

    assert _coerce_number("12,34") == pytest.approx(12.34)
    assert _coerce_number("1 234,56") == pytest.approx(1234.56)
    assert _coerce_number("36,75%") == pytest.approx(0.3675)
    assert _coerce_number("1,234.56") == pytest.approx(1234.56)
    # Wiele liczb (oś lat) — bierzemy pierwszą, sanity odrzuci jako P/E.
    assert _coerce_number("2013 2014 2015 2016") == pytest.approx(2013.0)


def test_absurd_cached_pe_not_served_within_ttl(tmp_path, monkeypatch):
    """Zepsuty cache TV nie blokuje ponownego pobrania z yfinance."""
    import fundamentals as mod

    cache = tmp_path / "cache.json"
    fake_clock = {"now": 1000.0}
    monkeypatch.setattr(mod.time, "time", lambda: fake_clock["now"])
    cache.write_text(
        json.dumps(
            {
                "GPW:XTB": {
                    "Ticker": "GPW:XTB",
                    "Fund_PE": 2.0132014201520162e51,
                    "Fund_PB": None,
                    "Fund_EV_EBITDA": None,
                    "Fund_ROE": None,
                    "Fund_NetMargin": None,
                    "Fund_DE": None,
                    "Fund_FCF": None,
                    "Fund_Source": "tradingview",
                    "Fund_Updated_At": "2026-05-22T19:54:11Z",
                    "_cached_at": fake_clock["now"],
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        mod,
        "_yf_fetch",
        lambda sym: {"Fund_PE": 13.01, "Fund_PB": 6.41}
        if sym == "XTB.WA"
        else None,
    )

    data = mod.fetch_fundamentals("GPW:XTB", cache_path=cache, ttl_hours=24)
    assert data["Fund_Source"] == "yfinance"
    assert data["Fund_PE"] == pytest.approx(13.01)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def fetch_fundamentals_local(
    mod, ticker: str, cache_path: Path, *, ttl_hours: int = 24, force_refresh: bool = False
):
    """Drobny wrapper – ten sam co publiczny entrypoint, używany w testach."""
    return mod.fetch_fundamentals(
        ticker,
        cache_path=cache_path,
        ttl_hours=ttl_hours,
        force_refresh=force_refresh,
    )
