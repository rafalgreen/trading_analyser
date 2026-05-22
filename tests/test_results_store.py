import os
from pathlib import Path

import pandas as pd
import pytest


def test_order_result_columns_puts_meta_first():
    from results_store import order_result_columns, CSV_META_COLUMNS

    cols = ["ZZZ_Indicator", "Interval", "Current_Price", "Ticker", "AAA_Indicator"]
    ordered = order_result_columns(cols)
    assert ordered[:4] == ["Ticker", "Current_Price", "Interval"][:3] + [
        c for c in CSV_META_COLUMNS if c in cols and c not in ("Ticker", "Current_Price", "Interval")
    ] or ordered[0] == "Ticker"
    meta_positions = [ordered.index(c) for c in CSV_META_COLUMNS if c in ordered]
    indicator_positions = [ordered.index(c) for c in ("ZZZ_Indicator", "AAA_Indicator")]
    assert max(meta_positions) < min(indicator_positions)
    assert ordered.index("AAA_Indicator") < ordered.index("ZZZ_Indicator")


def test_ensure_meta_columns_adds_missing():
    from results_store import ensure_meta_columns, CSV_META_COLUMNS

    df = pd.DataFrame([{"Ticker": "AAPL", "Custom_Col": 1}])
    out = ensure_meta_columns(df)
    for c in CSV_META_COLUMNS:
        assert c in out.columns
    assert "Custom_Col" in out.columns


def test_save_results_row_new_file_has_meta_columns(tmp_path: Path):
    from results_store import save_results_row, CSV_META_COLUMNS

    f = tmp_path / "r.csv"
    save_results_row(
        str(f),
        {
            "Ticker": "AAPL",
            "Company_Name": "Apple Inc.",
            "Current_Price": "180",
            "Interval": "1D",
            "Scrape_Status": "OK",
            "Scrape_Error": "",
            "PCA_Values": "0.5 (Niebieski)",
        },
    )
    df = pd.read_csv(f)
    for c in CSV_META_COLUMNS:
        assert c in df.columns
    assert list(df.columns[: len(CSV_META_COLUMNS)]) == CSV_META_COLUMNS


def test_save_results_row_upserts_by_ticker_interval(tmp_path: Path):
    from results_store import save_results_row

    f = tmp_path / "r.csv"
    save_results_row(
        str(f),
        {"Ticker": "AAPL", "Interval": "1D", "PCA_Values": "0.1 (Niebieski)"},
    )
    save_results_row(
        str(f),
        {"Ticker": "AAPL", "Interval": "1D", "PCA_Values": "0.9 (Zielony)"},
    )
    save_results_row(
        str(f),
        {"Ticker": "AAPL", "Interval": "1W", "PCA_Values": "0.5 (Zielony)"},
    )
    df = pd.read_csv(f)
    assert len(df) == 2
    daily = df[df["Interval"] == "1D"].iloc[0]
    assert "0.9" in str(daily["PCA_Values"])


def test_row_has_indicator_data_filters_placeholders():
    from results_store import row_has_indicator_data

    row_ok = pd.Series({"PCA_Values": "0.7 (Zielony)"})
    row_nodata = pd.Series({"PCA_Values": "Brak danych na wykresie"})
    row_skipped_ok = pd.Series({"PCA_Values": "OK"})
    assert row_has_indicator_data(row_ok, "PCA") is True
    assert row_has_indicator_data(row_nodata, "PCA") is False
    assert row_has_indicator_data(row_skipped_ok, "PCA") is False


def test_row_has_indicator_data_macd_requires_macd_line_present():
    """MacD: sam ``MacD_Trend`` to za mało — bez ``MacD_Line`` zwraca False."""
    from results_store import row_has_indicator_data

    row_trend_only = pd.Series({"MacD_Trend": "Wzrostowy", "MacD_Line": ""})
    row_no_line_col = pd.Series({"MacD_Trend": "Wzrostowy"})
    row_line_placeholder = pd.Series(
        {"MacD_Line": "Brak danych na wykresie", "MacD_Trend": "Wzrostowy"}
    )

    assert row_has_indicator_data(row_trend_only, "MacD") is False
    assert row_has_indicator_data(row_no_line_col, "MacD") is False
    assert row_has_indicator_data(row_line_placeholder, "MacD") is False


def test_row_has_indicator_data_macd_line_value_true():
    """Niepuste ``MacD_Line`` (np. ``0.123``) wystarczy by uznać dane MacD."""
    from results_store import row_has_indicator_data

    row_simple = pd.Series({"MacD_Line": "0.123"})
    row_formatted = pd.Series(
        {"MacD_Line": "0.123 (Zielony)", "MacD_Trend": "Wzrostowy"}
    )

    assert row_has_indicator_data(row_simple, "MacD") is True
    assert row_has_indicator_data(row_formatted, "MacD") is True


def test_load_results_dataframe_missing_returns_none(tmp_path: Path):
    from results_store import load_results_dataframe

    assert load_results_dataframe(str(tmp_path / "brak.csv")) is None


def test_load_results_dataframe_tolerates_bad_lines(tmp_path: Path):
    from results_store import load_results_dataframe

    f = tmp_path / "r.csv"
    f.write_text(
        "Ticker,Interval,PCA_Values\nAAPL,1D,0.5\nBROKEN,1W,0.1,EXTRA,FIELDS\n",
        encoding="utf-8",
    )
    df = load_results_dataframe(str(f))
    assert df is not None
    assert "Ticker" in df.columns


def test_remove_ticker_rows_from_csv_counts_and_rewrites_atomically(tmp_path: Path):
    from results_store import count_ticker_rows_in_csv, remove_ticker_rows_from_csv

    f = tmp_path / "r.csv"
    f.write_text(
        "Ticker,Interval,PCA_Values\n"
        "AAPL,1D,0.5\n"
        "MSFT,1D,0.6\n"
        "aapl,1W,0.7\n",
        encoding="utf-8",
    )

    assert count_ticker_rows_in_csv(str(f), "AAPL") == 2
    removed, remaining = remove_ticker_rows_from_csv(str(f), "AAPL")
    assert removed == 2
    assert remaining == 1

    df = pd.read_csv(f)
    assert list(df.columns) == ["Ticker", "Interval", "PCA_Values"]
    assert df["Ticker"].tolist() == ["MSFT"]


def test_remove_ticker_rows_from_csv_leaves_file_when_no_match(tmp_path: Path):
    from results_store import remove_ticker_rows_from_csv

    f = tmp_path / "r.csv"
    original = "Ticker,Interval,PCA_Values\nMSFT,1D,0.6\n"
    f.write_text(original, encoding="utf-8")

    removed, remaining = remove_ticker_rows_from_csv(str(f), "AAPL")
    assert (removed, remaining) == (0, 1)
    assert f.read_text(encoding="utf-8") == original


def test_merge_existing_row_keeps_fresh_metadata():
    from results_store import merge_existing_row_into_row_data

    fresh = {
        "Ticker": "NKE",
        "Interval": "1D",
        "Company_Name": "Nike, Inc.",
        "Current_Price": "95.1",
        "PCA_Values": "11 (Niebieski)",
    }
    old = pd.Series(
        {
            "Ticker": "NKE",
            "Interval": "1D",
            "Company_Name": "NKE",
            "Current_Price": "90.0",
            "PCA_Values": "7 (Czerwony)",
            "MacD_Trend": "Spadkowy",
        }
    )
    merge_existing_row_into_row_data(fresh, old)
    assert fresh["Company_Name"] == "Nike, Inc."
    assert fresh["Current_Price"] == "95.1"
    assert fresh["MacD_Trend"] == "Spadkowy"


def test_merge_skip_indicator_merge_on_partial_refresh():
    from results_store import merge_existing_row_into_row_data

    fresh = {
        "Ticker": "GPW:TXT",
        "Interval": "1D",
        "Company_Name": "Text S.A.",
        "Current_Price": "40.28",
    }
    old = pd.Series(
        {
            "Ticker": "GPW:TXT",
            "Interval": "1D",
            "HTS Panel_Trend": "Wzrostowy",
            "HTS Panel_Fast_High": "40,15 (Brak)",
            "PCA_Values": "46,60 (Zielony)",
        }
    )
    merge_existing_row_into_row_data(fresh, old, skip_indicator_merge=True)
    assert fresh["Company_Name"] == "Text S.A."
    assert "HTS Panel_Trend" not in fresh
    assert "PCA_Values" not in fresh


def test_tickers_with_no_data_detects_no_data_and_all_missing():
    from results_store import tickers_with_no_data

    df = pd.DataFrame(
        [
            {
                "Ticker": "AAA",
                "Interval": "1D",
                "Scrape_Status": "NO_DATA",
                "PCA_Values": "Brak danych na wykresie",
            },
            {
                "Ticker": "BBB",
                "Interval": "1D",
                "Scrape_Status": "OK",
                "PCA_Values": "Brak danych na wykresie",
                "HTS Panel_Values": "Brak poprawnych danych",
                "MacD_Values": "Brak danych na wykresie",
            },
            {
                "Ticker": "CCC",
                "Interval": "1D",
                "Scrape_Status": "OK",
                "PCA_Values": "12.3 (Niebieski)",
            },
            {
                "Ticker": "DDD",
                "Interval": "-",
                "Scrape_Status": "SKIPPED",
                "PCA_Values": "",
            },
        ]
    )
    out = tickers_with_no_data(df, ["PCA", "HTS Panel", "MacD"])
    assert out == ["AAA", "BBB"]


def test_ticker_rows_show_no_data_matches_dashboard_rules():
    from results_store import config_tickers_with_no_data, ticker_rows_show_no_data

    placeholder_rows = [
        {"Ticker": "NEVER", "Interval": "1D", "Scrape_Status": "", "All_Indicators_Missing": True},
        {"Ticker": "NEVER", "Interval": "1W", "Scrape_Status": "", "All_Indicators_Missing": True},
    ]
    assert ticker_rows_show_no_data([]) is True
    assert ticker_rows_show_no_data(placeholder_rows) is True

    ok_rows = [
        {"Ticker": "OK", "Interval": "1D", "Scrape_Status": "OK", "All_Indicators_Missing": False},
    ]
    assert ticker_rows_show_no_data(ok_rows) is False

    skipped_rows = [
        {"Ticker": "SKIP", "Interval": "-", "Scrape_Status": "SKIPPED", "All_Indicators_Missing": True},
    ]
    assert ticker_rows_show_no_data(skipped_rows) is False

    no_data_rows = [
        {"Ticker": "BAD", "Interval": "1D", "Scrape_Status": "NO_DATA", "All_Indicators_Missing": True},
    ]
    assert ticker_rows_show_no_data(no_data_rows) is True

    flat = placeholder_rows + ok_rows + no_data_rows
    assert config_tickers_with_no_data(["NEVER", "OK", "BAD", "MISSING"], flat) == [
        "NEVER",
        "BAD",
        "MISSING",
    ]


def test_row_has_indicator_data_macd_requires_line():
    """MacD: sam Trend/Cross bez MacD_Line liczy się jako brak danych."""
    from results_store import row_has_indicator_data

    row_ok = pd.Series({"MacD_Line": "0.48 (Czerwony)", "MacD_Trend": "Spadkowy"})
    row_empty_line = pd.Series({"MacD_Trend": "Spadkowy", "MacD_Cross": "BEAR CROSS"})
    row_placeholder = pd.Series({"MacD_Line": "Brak danych na wykresie"})
    assert row_has_indicator_data(row_ok, "MacD") is True
    assert row_has_indicator_data(row_empty_line, "MacD") is False
    assert row_has_indicator_data(row_placeholder, "MacD") is False


def test_save_and_load_fundamentals_roundtrip(tmp_path: Path):
    from results_store import (
        FUNDAMENTALS_COLUMNS,
        get_fundamentals_for_ticker,
        load_fundamentals_dataframe,
        save_fundamentals_row,
    )

    path = tmp_path / "fundamentals.csv"
    save_fundamentals_row(
        {
            "Ticker": "AAPL",
            "Fund_PE": 12.5,
            "Fund_PB": 2.0,
            "Fund_EV_EBITDA": 8.0,
            "Fund_ROE": 0.15,
            "Fund_NetMargin": 0.10,
            "Fund_DE": 0.4,
            "Fund_FCF": 1.2e9,
            "Fund_Source": "yfinance",
            "Fund_Updated_At": "2026-05-22T10:00:00Z",
        },
        path=str(path),
    )

    df = load_fundamentals_dataframe(str(path))
    assert list(df.columns)[: len(FUNDAMENTALS_COLUMNS)] == FUNDAMENTALS_COLUMNS
    assert len(df) == 1
    assert df.iloc[0]["Ticker"] == "AAPL"

    one = get_fundamentals_for_ticker("AAPL", path=str(path))
    assert one is not None
    assert one["Fund_PE"] == pytest.approx(12.5)
    assert one["Fund_Source"] == "yfinance"
    assert one["Fund_Updated_At"] == "2026-05-22T10:00:00Z"


def test_save_fundamentals_row_upsert_overwrites(tmp_path: Path):
    from results_store import (
        get_fundamentals_for_ticker,
        load_fundamentals_dataframe,
        save_fundamentals_row,
    )

    path = tmp_path / "fundamentals.csv"
    save_fundamentals_row(
        {
            "Ticker": "AAPL",
            "Fund_PE": 12.5,
            "Fund_Source": "yfinance",
            "Fund_Updated_At": "2026-05-22T10:00:00Z",
        },
        path=str(path),
    )
    save_fundamentals_row(
        {
            "Ticker": "AAPL",
            "Fund_PE": 13.0,
            "Fund_PB": 2.5,
            "Fund_Source": "yfinance",
            "Fund_Updated_At": "2026-05-23T10:00:00Z",
        },
        path=str(path),
    )

    df = load_fundamentals_dataframe(str(path))
    assert len(df) == 1, "upsert nie powinien duplikować tickera"

    one = get_fundamentals_for_ticker("AAPL", path=str(path))
    assert one is not None
    assert one["Fund_PE"] == pytest.approx(13.0)
    assert one["Fund_PB"] == pytest.approx(2.5)
    assert one["Fund_Updated_At"] == "2026-05-23T10:00:00Z"


def test_save_fundamentals_row_handles_none_values(tmp_path: Path):
    from results_store import (
        get_fundamentals_for_ticker,
        save_fundamentals_row,
    )

    path = tmp_path / "fundamentals.csv"
    save_fundamentals_row(
        {
            "Ticker": "BTCUSDT",
            "Fund_PE": None,
            "Fund_PB": None,
            "Fund_EV_EBITDA": None,
            "Fund_ROE": None,
            "Fund_NetMargin": None,
            "Fund_DE": None,
            "Fund_FCF": None,
            "Fund_Source": "none",
            "Fund_Updated_At": "2026-05-22T10:00:00Z",
        },
        path=str(path),
    )

    one = get_fundamentals_for_ticker("BTCUSDT", path=str(path))
    assert one is not None
    assert one["Fund_PE"] is None
    assert one["Fund_Source"] == "none"


def test_get_fundamentals_for_ticker_returns_none_when_missing(tmp_path: Path):
    from results_store import (
        get_fundamentals_for_ticker,
        save_fundamentals_row,
    )

    path = tmp_path / "fundamentals.csv"
    save_fundamentals_row(
        {"Ticker": "AAPL", "Fund_PE": 1.0, "Fund_Source": "yfinance"},
        path=str(path),
    )

    assert get_fundamentals_for_ticker("MSFT", path=str(path)) is None
    assert get_fundamentals_for_ticker("", path=str(path)) is None
    # Brak pliku — None
    assert get_fundamentals_for_ticker("AAPL", path=str(tmp_path / "missing.csv")) is None


def test_row_skipped_for_dashboard_allows_no_data_with_values():
    from results_store import normalize_served_scrape_status, row_skipped_for_dashboard

    indicators = ["PCA", "HTS Panel", "MacD"]
    alb_like = {
        "Ticker": "ALB",
        "Interval": "1D",
        "Scrape_Status": "NO_DATA",
        "Scrape_Error": "Brak danych wskaźników na wykresie",
        "PCA_Values": "22.43 (Niebieski)",
        "HTS Panel_Fast_High": "186.12 (Niebieski)",
        "HTS Panel_Trend": "Wzrostowy",
        "MacD_Line": "−3.88 (Czerwony)",
    }
    assert row_skipped_for_dashboard(alb_like, indicators) is False
    normalized = normalize_served_scrape_status(alb_like, indicators)
    assert normalized["Scrape_Status"] == "OK"
    assert normalized["Scrape_Error"] == ""

    empty_no_data = {
        "Ticker": "BAD",
        "Interval": "1D",
        "Scrape_Status": "NO_DATA",
        "PCA_Values": "",
        "HTS Panel_Values": "Brak danych na wykresie",
        "MacD_Line": "",
    }
    assert row_skipped_for_dashboard(empty_no_data, indicators) is True

    partial_mid_scrape = {
        "Ticker": "BTCUSDT",
        "Interval": "1D",
        "Scrape_Status": "",
        "PCA_Values": "31.73 (color: rgb(0, 184, 70);)",
        "MacD_Line": "1,005.89 (Czerwony)",
    }
    assert row_skipped_for_dashboard(partial_mid_scrape, indicators) is True


def test_row_interval_complete_accepts_no_data_with_all_indicators():
    from results_store import row_interval_complete

    indicators = ["PCA", "HTS Panel", "MacD"]
    row = pd.Series(
        {
            "Scrape_Status": "NO_DATA",
            "PCA_Values": "31.73 (Niebieski)",
            "HTS Panel_Trend": "Spadkowy",
            "HTS Panel_Fast_High": "77,613.93 (Niebieski)",
            "MacD_Line": "1,005.89 (Czerwony)",
        }
    )
    assert row_interval_complete(row, indicators) is True


def test_merge_indicator_into_row_copies_hts_only():
    from results_store import merge_indicator_into_row

    target = {
        "Ticker": "BTCUSDT",
        "Interval": "1D",
        "PCA_Values": "20 (Zielony)",
        "MacD_Line": "0.5 (Czerwony)",
    }
    source = {
        "HTS Panel_Trend": "Wzrostowy",
        "HTS Panel_Fast_High": "90,464.39 (Niebieski)",
        "PCA_Values": "99 (Czerwony)",
    }
    merge_indicator_into_row(target, source, "HTS Panel")
    assert target["HTS Panel_Trend"] == "Wzrostowy"
    assert target["PCA_Values"] == "20 (Zielony)"


def test_config_tickers_with_no_data_flags_stale_and_partial():
    from results_store import config_tickers_with_no_data

    flat = [
        {
            "Ticker": "BTCUSDT",
            "Interval": "1D",
            "Last_Refresh": "2026-05-18",
            "Missing_Indicators": [],
            "Scrape_Status": "OK",
        },
        {
            "Ticker": "PARTIAL",
            "Interval": "1D",
            "Last_Refresh": "2026-05-22",
            "Missing_Indicators": ["HTS Panel"],
            "Scrape_Status": "OK",
        },
        {
            "Ticker": "FRESH",
            "Interval": "1D",
            "Last_Refresh": "2026-05-22",
            "Missing_Indicators": [],
            "Scrape_Status": "OK",
        },
    ]
    out = config_tickers_with_no_data(
        ["BTCUSDT", "PARTIAL", "FRESH", "NEVER"],
        flat,
        latest_scrape_date="2026-05-22",
        tickers_in_latest_csv=["FRESH", "PARTIAL"],
    )
    assert set(out) == {"BTCUSDT", "PARTIAL", "NEVER"}
