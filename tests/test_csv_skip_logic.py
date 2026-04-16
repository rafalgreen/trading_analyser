import pandas as pd

import tv_scraper as tv


def test_row_has_indicator_data_pca():
    row = pd.Series(
        {"PCA_Values": "10 (Niebieski)", "Interval": "1D", "Ticker": "X"}
    )
    assert tv.row_has_indicator_data(row, "PCA") is True
    assert tv.row_has_indicator_data(row, "MacD") is False


def test_row_has_indicator_data_pca_placeholder_not_counted():
    row = pd.Series(
        {
            "PCA_Values": "Brak danych na wykresie",
            "Interval": "1D",
            "Ticker": "X",
        }
    )
    assert tv.row_has_indicator_data(row, "PCA") is False


def test_row_interval_complete_all_three():
    row = pd.Series(
        {
            "PCA_Values": "1",
            "HTS Panel_Trend": "Wzrostowy",
            "MacD_Trend": "Spadkowy",
            "Scrape_Status": "OK",
        }
    )
    inds = ["PCA", "HTS Panel", "MacD"]
    assert tv.row_interval_complete(row, inds) is True


def test_row_interval_complete_missing_macd():
    row = pd.Series(
        {
            "PCA_Values": "1",
            "HTS Panel_Trend": "Wzrostowy",
            "Scrape_Status": "OK",
        }
    )
    inds = ["PCA", "HTS Panel", "MacD"]
    assert tv.row_interval_complete(row, inds) is False


def test_ticker_fully_done_skipped():
    df = pd.DataFrame(
        [
            {
                "Ticker": "BAD",
                "Interval": "-",
                "Scrape_Status": "SKIPPED",
                "Scrape_Error": "x",
            }
        ]
    )
    assert tv.ticker_fully_done_in_csv(df, "BAD", ["1D", "1W"], ["PCA"]) is True
