"""Single-indicator scraper refresh — API and CLI resolution."""

import tv_scraper as tv


def test_resolve_run_indicators_single_macd():
    all_inds = ["PCA", "HTS Panel", "MacD"]
    selected, config_inds, is_subset = tv.resolve_run_indicators(
        all_inds, cli_indicators="MacD"
    )
    assert selected == ["MacD"]
    assert config_inds == all_inds
    assert is_subset is True


def test_resolve_run_indicators_unknown_raises():
    try:
        tv.resolve_run_indicators(["PCA", "MacD"], cli_indicators="Foo")
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "Foo" in str(exc)


def test_resolve_run_indicators_all_explicit_not_subset():
    all_inds = ["PCA", "HTS Panel", "MacD"]
    selected, _, is_subset = tv.resolve_run_indicators(
        all_inds, cli_indicators="PCA,HTS Panel,MacD"
    )
    assert selected == all_inds
    assert is_subset is False


def test_scraper_run_with_indicators_filter(client, monkeypatch):
    import app as m

    seen = {}

    def fake(tickers=None, indicators=None):
        seen["tickers"] = tickers
        seen["indicators"] = indicators
        return {
            "status": "started",
            "pid": 99,
            "count": len(tickers or []),
            "scope": "subset_indicators",
            "indicators": indicators or [],
        }

    monkeypatch.setattr(m, "start_scraper_subprocess", fake)
    monkeypatch.setattr(
        m,
        "load_config",
        lambda: {
            "tickers": ["GPW:TXT"],
            "intervals": ["1D"],
            "indicators": ["PCA", "HTS Panel", "MacD"],
        },
    )

    r = client.post(
        "/api/scraper/run",
        json={"tickers": ["GPW:TXT"], "indicators": ["MacD"]},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "started"
    assert seen["tickers"] == ["GPW:TXT"]
    assert seen["indicators"] == ["MacD"]
    assert body["indicators"] == ["MacD"]


def test_scraper_run_rejects_unknown_indicator(client, monkeypatch):
    import app as m

    monkeypatch.setattr(
        m,
        "load_config",
        lambda: {"indicators": ["PCA", "HTS Panel", "MacD"]},
    )
    r = client.post(
        "/api/scraper/run",
        json={"tickers": ["GPW:TXT"], "indicators": ["RSI"]},
    )
    assert r.status_code == 422


def test_format_scraper_progress_includes_indicator_name():
    assert tv._format_scraper_progress(0, 3, 0, 1, "MacD") == (
        "1/3 · ticker 1/3 · wsk. 1/1 · MacD"
    )
