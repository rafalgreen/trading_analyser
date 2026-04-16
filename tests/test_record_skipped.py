import pandas as pd

from tv_scraper import record_skipped_ticker


def test_record_skipped_ticker_writes_row(tmp_path):
    path = str(tmp_path / "out.csv")
    record_skipped_ticker(path, "BADTICK", "Test reason")
    df = pd.read_csv(path)
    assert len(df) == 1
    assert df.iloc[0]["Ticker"] == "BADTICK"
    assert df.iloc[0]["Scrape_Status"] == "SKIPPED"
    assert "Test reason" in str(df.iloc[0]["Scrape_Error"])


def test_record_skipped_replaces_previous_skip(tmp_path):
    path = str(tmp_path / "out.csv")
    record_skipped_ticker(path, "X", "first")
    record_skipped_ticker(path, "X", "second")
    df = pd.read_csv(path)
    assert len(df) == 1
    assert "second" in str(df.iloc[0]["Scrape_Error"])
