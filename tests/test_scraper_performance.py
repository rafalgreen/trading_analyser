"""Testy optymalizacji wydajności scrapera (config, bufor CSV, tryb ticker_first)."""

from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd
import pytest

import tv_scraper as tv
from results_store import ResultsBuffer, upsert_results_row_in_df


def test_scraper_performance_normal_mode():
    perf = tv.ScraperPerformance({"mode": "normal"})
    assert perf.mode == "normal"
    assert perf.indicator_compute_s == 4.0
    assert perf.ticker_enter_s == 3.0
    assert perf.keyboard_delay_ms == 100
    assert perf.loop_mode == "indicator_first"
    assert perf.max_indicators_on_chart == 2


def test_scraper_performance_fast_mode():
    perf = tv.ScraperPerformance(
        {
            "mode": "fast",
            "loop_mode": "ticker_first",
            "keyboard_delay_ms": 25,
        }
    )
    assert perf.mode == "fast"
    assert perf.indicator_compute_s == 2.0
    assert perf.ticker_enter_s == 1.5
    assert perf.interval_change_s == 2.0
    assert perf.interval_settle_s == 0.3
    assert perf.keyboard_delay_ms == 25
    assert perf.loop_mode == "ticker_first"


def test_scraper_performance_max_indicators_on_chart():
    perf = tv.ScraperPerformance({"max_indicators_on_chart": 3})
    assert perf.max_indicators_on_chart == 3
    perf_invalid = tv.ScraperPerformance({"max_indicators_on_chart": "x"})
    assert perf_invalid.max_indicators_on_chart == 2


def test_chunk_indicators_tv_free():
    inds = ["PCA", "HTS Panel", "MacD"]
    assert tv.chunk_indicators(inds, 2) == [["PCA", "HTS Panel"], ["MacD"]]
    assert tv.chunk_indicators(inds, 3) == [["PCA", "HTS Panel", "MacD"]]
    assert tv.chunk_indicators(inds, 1) == [["PCA"], ["HTS Panel"], ["MacD"]]
    assert tv.chunk_indicators([], 2) == []


def test_scraper_overall_progress_ticker_first_with_intervals():
    # ticker 3, interval 1W (idx=1), batch 0, 174 tickers, 3 intervals
    assert tv.scraper_overall_progress_ticker_first(
        2, 174, batch_idx=0, n_batches=2, interval_idx=1, n_intervals=3
    ) == (8, 1044)
    # last step
    assert tv.scraper_overall_progress_ticker_first(
        173, 174, batch_idx=1, n_batches=2, interval_idx=2, n_intervals=3
    ) == (1044, 1044)


def test_format_scraper_progress_ticker_first():
    s = tv._format_scraper_progress_ticker_first(
        16,
        174,
        "1D",
        eta_label="~45 min",
        eta_total_label="~2h 10m",
        batch_idx=0,
        n_batches=2,
        interval_idx=0,
        n_intervals=3,
    )
    assert s.startswith("49/1044 · ticker 17/174 · partia 1/2")
    assert "1D" in s
    assert "pozostało ~45 min (całość ~2h 10m)" in s

    s2 = tv._format_scraper_progress_ticker_first(
        16, 174, batch_idx=1, n_batches=2, interval_idx=0, n_intervals=3
    )
    assert "partia 2/2" in s2
    assert s2.startswith("571/1044")


def test_scraper_elapsed_active_time_excludes_pause():
    assert tv._scraper_elapsed_seconds(600.0, None) == 600.0
    t0 = tv.time.perf_counter() - 10.0
    elapsed = tv._scraper_elapsed_seconds(600.0, t0)
    assert 609.0 <= elapsed <= 612.0


def test_compute_scraper_eta_uses_active_elapsed_not_wall_clock():
    """Po wznowieniu ETA bazuje na active_elapsed_s, nie na przerwie między Stop a Start."""
    active_before_pause = 600.0
    run_segment_s = 100.0
    t0 = tv.time.perf_counter() - run_segment_s
    active_total = tv._scraper_elapsed_seconds(active_before_pause, t0)
    eta_seconds, eta_label, eta_total = tv.compute_scraper_eta(20, 100, active_total)
    # Gdyby liczyć wall-clock z 2h przerwy (~7800s), ETA byłoby ~6× większe
    assert eta_seconds is not None
    assert eta_seconds < 5000.0
    assert "~" in eta_label


def test_format_scraper_eta_display():
    assert tv.format_scraper_eta_display("szacowanie…") == "szacowanie…"
    assert (
        tv.format_scraper_eta_display("~45 min", "~2h 10m")
        == "pozostało ~45 min (całość ~2h 10m)"
    )


def test_compute_scraper_eta_returns_total_estimate():
    eta_seconds, eta_label, eta_total = tv.compute_scraper_eta(20, 100, 2000.0)
    assert eta_seconds == 8000.0
    assert eta_label == "~2h 13m"
    assert eta_total == "~2h 47m"


def test_init_scraper_performance_updates_module_globals():
    tv.init_scraper_performance({"mode": "fast", "symbol_search_wait_ms": 2000})
    assert tv.SLEEP_AFTER_INDICATOR_COMPUTE_S == 2.0
    assert tv.SYMBOL_SEARCH_LIST_WAIT_MS == 2000
    tv.init_scraper_performance({"mode": "normal"})


def test_adaptive_wait_returns_early(monkeypatch):
    perf = tv.ScraperPerformance({"mode": "fast", "min_compute_wait_s": 0.01})
    monkeypatch.setattr(tv, "_SCRAPER_PERF", perf)
    sleeps = []
    monkeypatch.setattr(tv.time, "sleep", lambda s: sleeps.append(s))

    calls = {"n": 0}

    def pred():
        calls["n"] += 1
        return calls["n"] >= 2

    assert tv._adaptive_wait(pred, min_wait_s=0.01, max_wait_s=0.5, poll_s=0.01) is True
    assert calls["n"] >= 2


def test_adaptive_wait_skips_min_sleep_when_ready_immediately(monkeypatch):
    sleeps = []
    monkeypatch.setattr(tv.time, "sleep", lambda s: sleeps.append(s))
    assert tv._adaptive_wait(lambda: True, min_wait_s=0.5, max_wait_s=1.0) is True
    assert sleeps == []


def test_scraper_performance_interval_overrides():
    perf = tv.ScraperPerformance(
        {
            "mode": "fast",
            "interval_change_s": 1.2,
            "interval_settle_s": 0.11,
            "interval_settle_active_s": 0.05,
        }
    )
    assert perf.interval_change_s == 1.2
    assert perf.interval_settle_s == 0.11
    assert perf.interval_settle_active_s == 0.05


def test_metadata_from_existing_rows():
    df = pd.DataFrame(
        [
            {
                "Ticker": "AAPL",
                "Company_Name": "Apple Inc.",
                "Exchange": "NASDAQ",
                "Current_Price": "190.5",
                "Interval": "1D",
            }
        ]
    )
    assert tv._metadata_from_existing_rows(df, "AAPL") == (
        "Apple Inc.",
        "NASDAQ",
        "190.5",
    )
    assert tv._metadata_from_existing_rows(df, "MSFT") is None
    assert tv._metadata_from_existing_rows(None, "AAPL") is None


def test_fundamentals_during_scrape_reads_config(tmp_path, monkeypatch):
    cfg = tmp_path / "scraper_config.json"
    cfg.write_text(
        '{"fundamentals": {"enabled": true, "during_scrape": false}}',
        encoding="utf-8",
    )
    monkeypatch.setattr(tv, "CONFIG_FILE", str(cfg))
    assert tv._fundamentals_during_scrape() is False
    cfg.write_text(
        '{"fundamentals": {"enabled": true, "during_scrape": true}}',
        encoding="utf-8",
    )
    assert tv._fundamentals_during_scrape() is True


def test_extract_legend_html_builds_fragment():
    page = MagicMock()
    page.evaluate.return_value = [
        '<div data-qa-id="legend-source-item"><span>PCA</span></div>'
    ]
    html = tv._extract_legend_html(page)
    assert "legend-source-item" in html
    assert html.startswith("<html>")


def test_parse_indicators_from_page_uses_legend_html(monkeypatch):
    page = MagicMock()
    page.content = MagicMock(side_effect=AssertionError("page.content should not run"))
    monkeypatch.setattr(tv, "_wait_compute_for_indicators", lambda *a, **k: True)
    monkeypatch.setattr(tv, "_ensure_legend_expanded", lambda *a, **k: None)
    monkeypatch.setattr(tv, "_move_crosshair_off_chart", lambda *a, **k: None)
    monkeypatch.setattr(
        tv,
        "_extract_legend_html",
        lambda *a, **k: "<html><body></body></html>",
    )
    monkeypatch.setattr(
        tv,
        "parse_indicators",
        lambda html, inds: {"PCA_Value": "12", "PCA_Color": "Green", "PCA_Values": "12"},
    )
    row = {}
    wait_s, parse_s = tv._parse_indicators_from_page(page, ["PCA"], row)
    assert wait_s >= 0
    assert parse_s >= 0
    page.content.assert_not_called()


def test_format_scraper_progress_ticker_first_with_phases():
    changing = tv._format_scraper_progress_ticker_first(
        2,
        174,
        "1W",
        batch_idx=0,
        n_batches=2,
        interval_idx=1,
        n_intervals=3,
        phase="zmiana interwału",
    )
    assert "8/1044" in changing
    assert "1W" in changing
    assert "zmiana interwału" in changing

    reading = tv._format_scraper_progress_ticker_first(
        2,
        174,
        "1W",
        batch_idx=0,
        n_batches=2,
        interval_idx=1,
        n_intervals=3,
        phase="odczyt PCA, HTS Panel",
    )
    assert "odczyt PCA, HTS Panel" in reading


def test_canonical_interval_maps_polish_weekly():
    assert tv._canonical_interval("1T") == "1W"
    assert tv._canonical_interval("1W") == "1W"
    assert tv._canonical_interval("1D") == "1D"
    assert tv._canonical_interval("H1") == "H1"


def test_chart_interval_is_compares_toolbar_display(monkeypatch):
    monkeypatch.setattr(tv, "_read_displayed_chart_interval", lambda page: "1W")
    page = MagicMock()
    assert tv._chart_interval_is(page, "1T") is True
    assert tv._chart_interval_is(page, "1D") is False


def _fake_page_h1_toolbar_favorite_1d_active():
    """DOM jak na żywym TV: wykres H1, ulubiony 1D wygląda na „aktywny” w pasku."""
    page = MagicMock()

    class FakeLocator:
        def __init__(self, selector):
            self.selector = selector

        def count(self):
            if 'button[data-name="time-intervals"]' in self.selector:
                return 1
            if "1D" in self.selector and (
                '[aria-checked="true"]' in self.selector
                or '[aria-pressed="true"]' in self.selector
            ):
                return 1
            if self.selector == 'button[data-value="1D"]':
                return 1
            return 0

        @property
        def first(self):
            return self

        def get_attribute(self, name):
            if 'data-name="time-intervals"' in self.selector:
                if name == "data-value":
                    return "60"
                return None
            if name == "class":
                return "button interval-btn isActive"
            if name in ("aria-checked", "aria-pressed"):
                return "true"
            return None

        def inner_text(self, timeout=1500):
            if 'data-name="time-intervals"' in self.selector:
                return "1h"
            return ""

    page.locator = lambda sel: FakeLocator(sel)
    return page


def test_chart_interval_ignores_favorite_button_when_toolbar_shows_h1():
    """Regresja: stara detekcja myliła ulubiony 1D z aktywnym wykresem na H1."""
    page = _fake_page_h1_toolbar_favorite_1d_active()
    assert tv._interval_button_is_active(page, "1D") is True
    assert tv._read_displayed_chart_interval(page) == ""
    assert tv._read_displayed_chart_interval_raw(page).lower() in ("1h", "60")
    assert tv._chart_interval_is(page, "1D") is False


def test_switch_chart_interval_skips_comma_retry_when_toolbar_changed(monkeypatch):
    """Regresja: nie wpisuj 1D drugi raz, gdy toolbar już się zmienił."""
    perf = tv.ScraperPerformance({"mode": "fast"})
    monkeypatch.setattr(tv, "_SCRAPER_PERF", perf)
    page = MagicMock()
    page.keyboard = MagicMock()
    page.locator = MagicMock(return_value=MagicMock())
    reads = iter(["30M", "1D", "1D"])

    monkeypatch.setattr(
        tv,
        "_read_displayed_chart_interval_raw",
        lambda p: next(reads, "1D"),
    )
    monkeypatch.setattr(tv, "_wait_for_interval_loaded", lambda *a, **k: True)
    monkeypatch.setattr(tv.time, "sleep", lambda s: None)

    tv._switch_chart_interval(page, "1D")

    page.keyboard.type.assert_called_once_with("1D", delay=perf.keyboard_delay_ms)
    assert page.keyboard.press.call_count == 1
    page.keyboard.press.assert_called_once_with("Enter")


def test_switch_chart_interval_comma_retry_when_toolbar_unchanged(monkeypatch):
    """Picker (,) tylko gdy toolbar w ogóle nie zareagował na pierwszą próbę."""
    perf = tv.ScraperPerformance({"mode": "fast"})
    monkeypatch.setattr(tv, "_SCRAPER_PERF", perf)
    page = MagicMock()
    page.keyboard = MagicMock()
    page.locator = MagicMock(return_value=MagicMock())

    monkeypatch.setattr(tv, "_read_displayed_chart_interval_raw", lambda p: "30M")
    monkeypatch.setattr(tv, "_wait_for_interval_loaded", lambda *a, **k: False)
    monkeypatch.setattr(tv.time, "sleep", lambda s: None)

    tv._switch_chart_interval(page, "1D")

    assert page.keyboard.type.call_count == 2
    assert page.keyboard.press.call_count == 3
    page.keyboard.press.assert_any_call(",")


def test_switch_chart_interval_switches_when_toolbar_h1_not_favorite_1d(monkeypatch):
    """Regresja: przy H1 na wykresie scraper musi wpisać 1D, nie pomijać (~100ms)."""
    perf = tv.ScraperPerformance({"mode": "fast"})
    monkeypatch.setattr(tv, "_SCRAPER_PERF", perf)
    page = _fake_page_h1_toolbar_favorite_1d_active()
    page.keyboard = MagicMock()
    real_locator = page.locator
    body_click = MagicMock()

    def locator(sel):
        if sel == "body":
            return body_click
        return real_locator(sel)

    page.locator = locator
    displayed = {"value": "1H"}

    def read_toolbar(target_page):
        return displayed["value"]

    def wait_loaded(target_page, interval):
        displayed["value"] = tv._canonical_interval(interval)
        return True

    monkeypatch.setattr(tv, "_read_displayed_chart_interval_raw", read_toolbar)
    monkeypatch.setattr(tv, "_wait_for_interval_loaded", wait_loaded)
    monkeypatch.setattr(tv.time, "sleep", lambda s: None)

    assert tv._interval_button_is_active(page, "1D") is True
    assert tv._chart_interval_is(page, "1D") is False

    tv._switch_chart_interval(page, "1D")

    page.keyboard.type.assert_called_once_with("1D", delay=perf.keyboard_delay_ms)
    page.keyboard.press.assert_any_call("Enter")
    assert tv._chart_interval_is(page, "1D") is True


def test_interval_button_is_active_detects_selected_not_mere_presence():
    page = MagicMock()

    class FakeLocator:
        def __init__(self, selector):
            self.selector = selector

        def count(self):
            if '[aria-checked="true"]' in self.selector and "1W" in self.selector:
                return 1
            if self.selector == 'button[data-value="1W"]':
                return 1
            return 0

        @property
        def first(self):
            return self

        def get_attribute(self, name):
            if name == "class":
                return "button interval-btn"
            if name in ("aria-checked", "aria-pressed"):
                return "false"
            return None

    page.locator = lambda sel: FakeLocator(sel)
    assert tv._interval_button_is_active(page, "1W") is True


def test_interval_button_is_active_inactive_when_only_present():
    page = MagicMock()

    class FakeLocator:
        def __init__(self, selector):
            self.selector = selector

        def count(self):
            return 1 if self.selector == 'button[data-value="1W"]' else 0

        @property
        def first(self):
            return self

        def get_attribute(self, name):
            if name == "class":
                return "button interval-btn"
            if name in ("aria-checked", "aria-pressed"):
                return "false"
            return None

    page.locator = lambda sel: FakeLocator(sel)
    assert tv._interval_button_is_active(page, "1W") is False


def test_wait_for_interval_loaded_waits_until_active(monkeypatch):
    perf = tv.ScraperPerformance({"mode": "fast", "min_compute_wait_s": 0.01})
    monkeypatch.setattr(tv, "_SCRAPER_PERF", perf)
    sleeps = []
    monkeypatch.setattr(tv.time, "sleep", lambda s: sleeps.append(s))
    calls = {"n": 0}

    def active(*args, **kwargs):
        calls["n"] += 1
        return calls["n"] >= 2

    monkeypatch.setattr(tv, "_chart_interval_is", active)
    page = MagicMock()
    assert tv._wait_for_interval_loaded(page, "1D") is True
    assert calls["n"] >= 2
    assert perf.interval_settle_s in sleeps


def test_wait_compute_for_indicators_single_adaptive_pass(monkeypatch):
    perf = tv.ScraperPerformance({"mode": "fast", "min_compute_wait_s": 0.01})
    monkeypatch.setattr(tv, "_SCRAPER_PERF", perf)
    page = MagicMock()
    content_calls = {"n": 0}
    page.content = lambda: content_calls.__setitem__("n", content_calls["n"] + 1) or ""
    monkeypatch.setattr(
        tv,
        "_legend_has_nonempty_values_locator",
        lambda page, ind: ind == "PCA",
    )
    waits = {"n": 0}

    def fake_adaptive(pred, **kwargs):
        waits["n"] += 1
        return pred()

    monkeypatch.setattr(tv, "_adaptive_wait", fake_adaptive)
    assert tv._wait_compute_for_indicators(page, ["PCA", "HTS Panel"]) is False
    assert waits["n"] == 1
    assert content_calls["n"] == 0


def test_legend_has_nonempty_values_locator_reads_value_text():
    page = MagicMock()

    class FakeLocator:
        def __init__(self, selector=""):
            self.selector = selector
            self._text = "1.23"

        def count(self):
            if "legend-source-item" in self.selector:
                return 1
            if "legend-source-title" in self.selector:
                return 1
            if "legend-source-values" in self.selector:
                return 1
            if "valueValue" in self.selector:
                return 1
            return 0

        def nth(self, i):
            return self

        @property
        def first(self):
            return self

        def inner_text(self, timeout=800):
            if "title" in self.selector:
                return "PCA-RI"
            return "1.23"

        def locator(self, sel):
            return FakeLocator(sel)

    page.locator = lambda sel: FakeLocator(sel)
    assert tv._legend_has_nonempty_values_locator(page, "PCA") is True
    page.content.assert_not_called() if hasattr(page.content, "assert_not_called") else None


def test_switch_chart_interval_skips_keyboard_when_active(monkeypatch):
    perf = tv.ScraperPerformance({"mode": "fast"})
    monkeypatch.setattr(tv, "_SCRAPER_PERF", perf)
    page = MagicMock()
    page.keyboard = MagicMock()
    monkeypatch.setattr(tv, "_chart_interval_is", lambda *a, **k: True)
    monkeypatch.setattr(tv.time, "sleep", lambda s: None)
    elapsed = tv._switch_chart_interval(page, "1D")
    page.keyboard.type.assert_not_called()
    page.keyboard.press.assert_not_called()
    assert elapsed >= 0.0


def test_switch_chart_interval_types_when_inactive(monkeypatch):
    perf = tv.ScraperPerformance({"mode": "fast"})
    monkeypatch.setattr(tv, "_SCRAPER_PERF", perf)
    page = MagicMock()
    page.keyboard = MagicMock()
    page.locator = MagicMock(return_value=MagicMock())
    checks = {"n": 0}

    def chart_is(*args, **kwargs):
        checks["n"] += 1
        return checks["n"] > 1

    monkeypatch.setattr(tv, "_chart_interval_is", chart_is)
    monkeypatch.setattr(tv, "_read_displayed_chart_interval_raw", lambda *a, **k: "H1")
    monkeypatch.setattr(tv, "_wait_for_interval_loaded", lambda *a, **k: True)
    monkeypatch.setattr(tv.time, "sleep", lambda s: None)
    tv._switch_chart_interval(page, "1W")
    page.keyboard.type.assert_called_once()
    assert page.keyboard.press.call_count >= 1
    page.keyboard.press.assert_any_call("Enter")


def test_wait_for_interval_loaded_fallback_on_detection_failure(monkeypatch):
    perf = tv.ScraperPerformance({"mode": "fast", "min_compute_wait_s": 0.01})
    monkeypatch.setattr(tv, "_SCRAPER_PERF", perf)
    sleeps = []
    monkeypatch.setattr(tv.time, "sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr(tv, "_chart_interval_is", lambda *a, **k: False)
    monkeypatch.setattr(tv, "_read_displayed_chart_interval_raw", lambda *a, **k: "H1")
    monkeypatch.setattr(
        tv,
        "_adaptive_wait",
        lambda pred, **kwargs: False,
    )
    page = MagicMock()
    assert tv._wait_for_interval_loaded(page, "1M") is False
    assert perf.interval_settle_s in sleeps


def test_upsert_results_row_in_df_new_and_update():
    row = {
        "Ticker": "AAPL",
        "Interval": "1D",
        "Company_Name": "Apple",
        "Exchange": "NASDAQ",
        "Current_Price": "100",
        "Scrape_Status": "OK",
        "Scrape_Error": "",
        "PCA_Value": "1.2",
    }
    df = upsert_results_row_in_df(None, row)
    assert len(df) == 1
    assert df.loc[0, "PCA_Value"] == "1.2"

    row2 = dict(row)
    row2["PCA_Value"] = "2.3"
    df2 = upsert_results_row_in_df(df, row2)
    assert len(df2) == 1
    assert df2.loc[df2.index[0], "PCA_Value"] == "2.3"


def test_results_buffer_flush(tmp_path: Path):
    csv_path = tmp_path / "results.csv"
    buf = ResultsBuffer(str(csv_path))
    buf.upsert(
        {
            "Ticker": "MSFT",
            "Interval": "1W",
            "Company_Name": "Microsoft",
            "Exchange": "NASDAQ",
            "Current_Price": "400",
            "Scrape_Status": "OK",
            "Scrape_Error": "",
        }
    )
    buf.flush()
    assert csv_path.exists()
    loaded = pd.read_csv(csv_path)
    assert loaded.loc[0, "Ticker"] == "MSFT"

    buf.upsert(
        {
            "Ticker": "MSFT",
            "Interval": "1W",
            "Company_Name": "Microsoft",
            "Exchange": "NASDAQ",
            "Current_Price": "401",
            "Scrape_Status": "OK",
            "Scrape_Error": "",
        }
    )
    buf.flush()
    loaded2 = pd.read_csv(csv_path)
    assert str(loaded2.loc[0, "Current_Price"]) == "401"


def test_results_buffer_record_skipped(tmp_path: Path):
    csv_path = tmp_path / "results.csv"
    buf = ResultsBuffer(str(csv_path))
    buf.record_skipped("BAD", "Nie znaleziono")
    buf.flush()
    df = pd.read_csv(csv_path)
    assert df.loc[0, "Scrape_Status"] == "SKIPPED"
    assert df.loc[0, "Ticker"] == "BAD"


def test_parse_indicators_all_three_from_fixture():
    html = """
    <div data-qa-id="legend-source-item">
      <div data-qa-id="title-wrapper legend-source-title">PCA</div>
      <div data-qa-id="legend-source-values">
        <div class="valueItem"><div class="valueValue" style="color:red">1.5</div></div>
      </div>
    </div>
    <div data-qa-id="legend-source-item">
      <div data-qa-id="title-wrapper legend-source-title">HTS Panel</div>
      <div data-qa-id="legend-source-values">
        <div class="valueItem"><div class="valueTitle">Fast High</div><div class="valueValue">10</div></div>
        <div class="valueItem"><div class="valueTitle">Fast Low</div><div class="valueValue">9</div></div>
        <div class="valueItem"><div class="valueTitle">Slow High</div><div class="valueValue">8</div></div>
        <div class="valueItem"><div class="valueTitle">Slow Low</div><div class="valueValue">7</div></div>
      </div>
    </div>
    <div data-qa-id="legend-source-item">
      <div data-qa-id="title-wrapper legend-source-title">CM_Ult_MacD_MTF</div>
      <div data-qa-id="legend-source-values">
        <div class="valueItem"><div class="valueTitle">MacD Line</div><div class="valueValue">0.12</div></div>
      </div>
    </div>
    """
    data = tv.parse_indicators(html, ["PCA", "HTS Panel", "MacD"])
    assert data.get("PCA_Value") == "1.5"
    assert str(data.get("MacD_Line", "")).startswith("0.12")
    assert "HTS Panel_Trend" in data or "HTS Panel_Values" in data


def test_merge_parsed_indicators_into_row():
    row = {"Ticker": "X", "Interval": "1D"}
    tv._merge_parsed_indicators_into_row(
        row, {"PCA_Value": "2.0", "PCA_Color": "rgb(0,0,0)"}, "PCA"
    )
    assert row["PCA_Value"] == "2.0"


def test_run_state_persists_active_elapsed(tmp_path):
    state_path = tmp_path / "scraper_state.json"
    tv._write_run_state_file(
        str(state_path),
        current_run_file="results/x.csv",
        processed_combos={("AAPL", "1D")},
        session_started_at=1234.5,
        active_elapsed_s=987.5,
        ticker_idx=3,
        ind_idx=0,
        tickers=["AAPL"],
        indicators=["PCA"],
        no_data_only=False,
    )
    loaded = tv._load_run_state_file(str(state_path))
    assert loaded["active_elapsed_s"] == 987.5


@pytest.mark.parametrize(
    "cfg,expected_compute",
    [
        ({"mode": "normal"}, 4.0),
        ({"mode": "fast"}, 2.0),
    ],
)
def test_performance_timing_presets(cfg, expected_compute):
    perf = tv.ScraperPerformance(cfg)
    assert perf.indicator_compute_s == expected_compute
