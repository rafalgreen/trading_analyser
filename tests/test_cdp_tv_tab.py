"""Reuse istniejącej karty TradingView przez CDP — wybór URL i stron."""

from unittest.mock import MagicMock

import tv_scraper as tv


def test_is_tradingview_chart_url_chart_path():
    assert tv.is_tradingview_chart_url("https://www.tradingview.com/chart/abc123/")
    assert tv.is_tradingview_chart_url("HTTPS://TRADINGVIEW.COM/CHART/")


def test_is_tradingview_chart_url_rejects_unrelated():
    assert not tv.is_tradingview_chart_url("https://google.com/")
    assert not tv.is_tradingview_chart_url("")
    assert not tv.is_tradingview_chart_url("https://www.tradingview.com")


def test_is_tradingview_chart_url_symbols_page():
    assert tv.is_tradingview_chart_url("https://www.tradingview.com/symbols/NASDAQ-AAPL/")


def test_pick_tradingview_chart_page_prefers_chart_url():
    pages = [
        {"type": "page", "url": "https://www.google.com/", "title": "Google"},
        {
            "type": "page",
            "url": "https://www.tradingview.com/symbols/NASDAQ-AAPL/",
            "title": "AAPL",
        },
        {
            "type": "page",
            "url": "https://www.tradingview.com/chart/xYz/",
            "title": "Chart",
        },
    ]
    picked = tv.pick_tradingview_chart_page(pages)
    assert picked is pages[2]


def test_pick_tradingview_chart_page_fallback_other_tv():
    pages = [
        {"type": "page", "url": "https://example.com/", "title": "X"},
        {
            "type": "page",
            "url": "https://www.tradingview.com/markets/",
            "title": "Markets",
        },
    ]
    assert tv.pick_tradingview_chart_page(pages) is pages[1]


def test_pick_tradingview_chart_page_playwright_title_fallback():
    other = MagicMock()
    other.url = "https://example.com/"
    other.title = lambda: "Example"

    tv_page = MagicMock()
    tv_page.url = "about:blank"
    tv_page.title = lambda: "AAPL — TradingView"

    assert tv.pick_tradingview_chart_page([other, tv_page]) is tv_page


def test_pick_tradingview_chart_page_none_when_missing():
    assert tv.pick_tradingview_chart_page([]) is None
    assert (
        tv.pick_tradingview_chart_page(
            [{"type": "page", "url": "https://news.ycombinator.com/"}]
        )
        is None
    )


def test_cdp_find_tradingview_chart_url(monkeypatch):
    monkeypatch.setattr(
        tv,
        "cdp_list_targets",
        lambda port, host="127.0.0.1", timeout_s=2.0: [
            {"type": "page", "url": "https://www.tradingview.com/chart/abc/"},
            {"type": "page", "url": "https://www.google.com/"},
        ],
    )
    assert (
        tv.cdp_find_tradingview_chart_url(9222)
        == "https://www.tradingview.com/chart/abc/"
    )


def test_cdp_find_tradingview_chart_url_skips_non_page_targets(monkeypatch):
    monkeypatch.setattr(
        tv,
        "cdp_list_targets",
        lambda port, host="127.0.0.1", timeout_s=2.0: [
            {
                "type": "service_worker",
                "url": "https://www.tradingview.com/chart/hidden/",
            },
            {"type": "page", "url": "https://www.tradingview.com/chart/visible/"},
        ],
    )
    assert (
        tv.cdp_find_tradingview_chart_url(9222)
        == "https://www.tradingview.com/chart/visible/"
    )
