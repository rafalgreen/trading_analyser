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
    assert list(df.columns[:6]) == CSV_META_COLUMNS


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
