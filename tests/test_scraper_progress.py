"""Postęp scrapera — format monotoniczny między fazami wskaźników."""

import tv_scraper as tv


def test_format_scraper_progress_first_ticker_first_indicator():
    assert tv._format_scraper_progress(0, 26, 0, 3) == (
        "1/78 · ticker 1/26 · wsk. 1/3"
    )


def test_format_scraper_progress_mid_first_indicator_phase():
    assert tv._format_scraper_progress(14, 26, 0, 3) == (
        "15/78 · ticker 15/26 · wsk. 1/3"
    )


def test_format_scraper_progress_second_indicator_does_not_reset_overall():
    # Po zakończeniu fazy 1 (26/78), faza 2 ticker 3 → 29/78 (nie 3/26)
    assert tv._format_scraper_progress(2, 26, 1, 3) == (
        "29/78 · ticker 3/26 · wsk. 2/3"
    )


def test_format_scraper_progress_done():
    assert tv._format_scraper_progress(25, 26, 2, 3) == (
        "78/78 · ticker 26/26 · wsk. 3/3"
    )


def test_scraper_overall_progress():
    assert tv.scraper_overall_progress(8, 163, 0, 3) == (9, 489)


def test_compute_scraper_eta_estimating_before_min_steps():
    eta_seconds, eta_label = tv.compute_scraper_eta(2, 489, 120.0)
    assert eta_seconds is None
    assert eta_label == "szacowanie…"


def test_compute_scraper_eta_after_min_steps():
    # 9/489 done in 900s → avg 100s/step, 480 left → 48000s ≈ 800 min
    eta_seconds, eta_label = tv.compute_scraper_eta(9, 489, 900.0)
    assert eta_seconds == 48000.0
    assert eta_label == "~13h 20m"


def test_compute_scraper_eta_under_one_minute():
    eta_seconds, eta_label = tv.compute_scraper_eta(10, 11, 500.0)
    assert eta_seconds == 50.0
    assert eta_label == "< 1 min"


def test_compute_scraper_eta_minutes():
    eta_seconds, eta_label = tv.compute_scraper_eta(10, 20, 1200.0)
    assert eta_seconds == 1200.0
    assert eta_label == "~20 min"


def test_compute_scraper_eta_hours():
    eta_seconds, eta_label = tv.compute_scraper_eta(9, 489, 151.875)
    assert eta_seconds == 8100.0
    assert eta_label == "~2h 15m"


def test_format_scraper_eta_label():
    assert tv.format_scraper_eta_label(30) == "< 1 min"
    assert tv.format_scraper_eta_label(720) == "~12 min"
    assert tv.format_scraper_eta_label(8100) == "~2h 15m"


def test_format_scraper_progress_with_eta():
    assert tv._format_scraper_progress(8, 163, 0, 3, "PCA", eta_label="~2h 15m") == (
        "9/489 · ticker 9/163 · wsk. 1/3 · PCA · ~2h 15m"
    )


def test_build_running_scraper_progress_estimating():
    progress, eta_seconds, eta_label = tv._build_running_scraper_progress(
        0, 163, 0, 3, "PCA", run_t0=None
    )
    assert eta_seconds is None
    assert eta_label == "szacowanie…"
    assert progress.endswith("szacowanie…")


def test_build_running_scraper_progress_with_eta():
    eta_seconds, eta_label = tv.compute_scraper_eta(9, 489, 900.0)
    progress = tv._format_scraper_progress(8, 163, 0, 3, "PCA", eta_label=eta_label)
    assert progress == "9/489 · ticker 9/163 · wsk. 1/3 · PCA · ~13h 20m"
    assert eta_seconds == 48000.0


def test_format_scraper_progress_with_resumed():
    assert tv._format_scraper_progress(45, 163, 0, 3, "PCA", resumed=True) == (
        "46/489 · ticker 46/163 · wsk. 1/3 · (wznowiono) · PCA"
    )


def test_parse_progress_checkpoint():
    ticker_idx, ind_idx = tv._parse_progress_checkpoint(
        "46/489 · ticker 46/163 · wsk. 1/3 · PCA"
    )
    assert ticker_idx == 45
    assert ind_idx == 0


def test_write_and_read_run_state_file(tmp_path):
    state_path = tmp_path / "scraper_state.json"
    tv._write_run_state_file(
        str(state_path),
        current_run_file="results/x.csv",
        processed_combos={("AAPL", "1D")},
        session_started_at=1234.5,
        ticker_idx=45,
        ind_idx=0,
        tickers=["AAPL", "MSFT"],
        indicators=["PCA"],
        no_data_only=True,
        resumed=True,
    )
    loaded = tv._load_run_state_file(str(state_path))
    assert loaded["ticker_idx"] == 45
    assert loaded["no_data_only"] is True
    assert loaded["tickers"] == ["AAPL", "MSFT"]
    assert loaded["resumed"] is True
