import csv

import pytest


def test_parse_date_from_filename_date_only():
    import app as m

    label = m.parse_date_from_filename("results/tradingview_results_2026-04-15.csv")
    assert label == "2026-04-15"


def test_parse_date_from_filename_with_time():
    import app as m

    label = m.parse_date_from_filename(
        "results/tradingview_results_2026-04-15_14-30-00.csv"
    )
    assert label == "2026-04-15 14:30:00"


def test_validate_results_date_id_accepts():
    import app as m

    m.validate_results_date_id("2026-01-01")
    m.validate_results_date_id("2026-01-01_12-00-00")


def test_validate_results_date_id_rejects():
    import app as m
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as ei:
        m.validate_results_date_id("../../../x")
    assert ei.value.status_code == 400


def test_clean_company_name_watchlist(tmp_path, monkeypatch):
    import app as m

    data = tmp_path / "data"
    data.mkdir(parents=True)
    wl = data / "Portfel_Watchlist_99.csv"
    wl.write_text(
        "Symbol,Name,Last\nFOO,Foo Incorporated,1.0\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(m, "DATA_DIR", str(data))
    monkeypatch.setattr(m, "_watchlist_cache", None)
    wl_map = m.load_watchlist()
    assert m.clean_company_name("FOO", "garbage", wl_map) == "Foo Incorporated"


def test_clean_company_name_watchlist_symbol_case_insensitive(tmp_path, monkeypatch):
    import app as m

    data = tmp_path / "data"
    data.mkdir(parents=True)
    wl = data / "Portfel_Watchlist_98.csv"
    wl.write_text(
        "Symbol,Name,Last\nnke,Nike From WL,100\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(m, "DATA_DIR", str(data))
    monkeypatch.setattr(m, "_watchlist_cache", None)
    wl_map = m.load_watchlist()
    assert m.clean_company_name("NKE", "NKE", wl_map) == "Nike From WL"


def test_is_dirty_company_name():
    import app as m

    assert m.is_dirty_company_name("X", "") is True
    assert m.is_dirty_company_name("MSFT", "MSFT 123") is True
    assert m.is_dirty_company_name("MSFT", "▼ −1%") is True
    assert m.is_dirty_company_name("MSFT", "Microsoft") is False


def test_wl_signal_visibility_for_ticker():
    import app as m

    IND3 = ["PCA", "HTS Panel", "MacD"]

    def full_interval(iv: str):
        return {
            "Scrape_Status": "OK",
            "PCA_Values": "10 (Niebieski)",
            "HTS Panel_Trend": "Wzrostowy",
            "MacD_Line": "0.5 (Czerwony)",
            "MacD_Trend": "Spadkowy",
            "Interval": iv,
        }

    assert m.wl_signal_visibility_for_ticker([]) == {
        "daily": False,
        "weekly": False,
        "monthly": False,
    }
    assert m.wl_signal_visibility_for_ticker(
        [{"Scrape_Status": "SKIPPED", "PCA_Values": "", "Interval": "1D"}]
    ) == {"daily": False, "weekly": False, "monthly": False}

    base_bad = {
        "Scrape_Status": "OK",
        "PCA_Values": "Brak danych na wykresie",
        "HTS Panel_Trend": "Brak",
        "MacD_Trend": "Brak",
    }
    assert m.wl_signal_visibility_for_ticker(
        [{**base_bad, "Interval": "1D"}], IND3
    ) == {"daily": False, "weekly": False, "monthly": False}

    # Samo PCA bez HTS/MacD — nie pokazujemy sygnałów z watchlisty
    assert m.wl_signal_visibility_for_ticker(
        [
            {
                "Scrape_Status": "OK",
                "PCA_Values": "10 (Niebieski)",
                "HTS Panel_Trend": "Brak",
                "MacD_Trend": "Brak",
                "Interval": "1D",
            }
        ],
        IND3,
    ) == {"daily": False, "weekly": False, "monthly": False}

    # Tylko HTS — nadal za mało
    assert m.wl_signal_visibility_for_ticker(
        [
            {
                "Scrape_Status": "OK",
                "PCA_Values": "Brak danych na wykresie",
                "HTS Panel_Trend": "Wzrostowy",
                "MacD_Trend": "Brak",
                "Interval": "1W",
            }
        ],
        IND3,
    ) == {"daily": False, "weekly": False, "monthly": False}

    assert m.wl_signal_visibility_for_ticker([full_interval("1D")], IND3) == {
        "daily": True,
        "weekly": False,
        "monthly": False,
    }

    assert m.wl_signal_visibility_for_ticker(
        [full_interval("1D"), {**base_bad, "Interval": "1W"}],
        IND3,
    ) == {"daily": True, "weekly": False, "monthly": False}


def test_resolve_no_data_tickers_matches_dashboard(app_env, monkeypatch):
    """_resolve_no_data_tickers używa tej samej logiki co dashboard (Brak danych)."""
    m, res, _dat = app_env
    fields = ["Ticker", "Company_Name", "Interval", "PCA_Values", "Scrape_Status"]
    fp = res / "tradingview_results_2026-05-22.csv"
    with open(fp, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerow({
            "Ticker": "OK1",
            "Company_Name": "Ok Inc",
            "Interval": "1D",
            "PCA_Values": "1 (Niebieski)",
            "Scrape_Status": "OK",
        })
    monkeypatch.setattr(
        m,
        "load_config",
        lambda: {
            "tickers": ["OK1", "MISSING", "GPW:BAD"],
            "intervals": ["1D"],
            "indicators": ["PCA"],
        },
    )
    out = m._resolve_no_data_tickers(None, None)
    assert set(out) == {"MISSING", "GPW:BAD"}
