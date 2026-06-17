"""Testy optymalizacji wydajności scrapera (config, bufor CSV, tryb ticker_first)."""

from pathlib import Path

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


def test_scraper_overall_progress_ticker_first():
    assert tv.scraper_overall_progress_ticker_first(0, 174) == (1, 174)
    assert tv.scraper_overall_progress_ticker_first(173, 174) == (174, 174)
    # 2 partie × 174 tickerów
    assert tv.scraper_overall_progress_ticker_first(0, 174, batch_idx=1, n_batches=2) == (
        175,
        348,
    )


def test_format_scraper_progress_ticker_first():
    s = tv._format_scraper_progress_ticker_first(16, 174, "1D", eta_label="~45 min")
    assert s.startswith("17/174 · ticker 17/174")
    assert "1D" in s
    assert "ETA ~45 min" in s

    s2 = tv._format_scraper_progress_ticker_first(
        16, 174, batch_idx=1, n_batches=2
    )
    assert "partia 2/2" in s2
    assert s2.startswith("191/348")
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
    assert perf.keyboard_delay_ms == 25
    assert perf.loop_mode == "ticker_first"


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


def test_scraper_performance_fast_mode():
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
