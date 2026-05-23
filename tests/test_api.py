import csv
import json
import os

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Smoke tests (pre-istniejące)
# ---------------------------------------------------------------------------


def test_history_empty(client: TestClient):
    r = client.get("/api/history")
    assert r.status_code == 200
    assert r.json() == {"dates": []}


def test_results_not_found(client: TestClient):
    r = client.get("/api/results/2026-01-01")
    assert r.status_code == 404


def test_results_invalid_date_id(client: TestClient):
    r = client.get("/api/results/not-a-valid-id")
    assert r.status_code == 400


def test_results_ok(app_env, client: TestClient):
    _m, res, _dat = app_env
    fp = res / "tradingview_results_2026-04-01.csv"
    with open(fp, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(
            f,
            fieldnames=["Ticker", "Company_Name", "Interval", "PCA_Values"],
        )
        w.writeheader()
        w.writerow(
            {
                "Ticker": "AAA",
                "Company_Name": "TestCo",
                "Interval": "1D",
                "PCA_Values": "10 (Niebieski)",
            }
        )
    r = client.get("/api/results/2026-04-01")
    assert r.status_code == 200
    data = r.json()["data"]
    assert len(data) == 1
    assert data[0]["Ticker"] == "AAA"


def test_config_get(client: TestClient):
    r = client.get("/api/config")
    assert r.status_code == 200
    body = r.json()
    assert "intervals" in body
    assert "indicators" in body


# ---------------------------------------------------------------------------
# /api/dashboard
# ---------------------------------------------------------------------------


def test_dashboard_empty_when_no_csv_files(client: TestClient, app_env, monkeypatch):
    """Bez plików CSV każdy ticker ma intervals z row=None i last_refresh=None."""
    m, _res, _dat = app_env
    monkeypatch.setattr(
        m,
        "load_config",
        lambda: {"tickers": ["AAA"], "intervals": ["1D"], "indicators": ["PCA"]},
    )
    r = client.get("/api/dashboard")
    assert r.status_code == 200
    body = r.json()
    assert "tickers" in body
    assert len(body["tickers"]) == 1
    assert "data" in body
    assert len(body["data"]) == 1
    assert body["data"][0]["Ticker"] == "AAA"
    assert body["data"][0]["Interval"] == "1D"
    assert body["data"][0].get("Last_Refresh") in (None, "")
    entry = body["tickers"][0]
    assert entry["ticker"] == "AAA"
    assert "intervals" in entry
    assert "1D" in entry["intervals"]
    assert entry["intervals"]["1D"]["row"] is None
    assert entry["intervals"]["1D"]["last_refresh"] is None
    assert entry["last_refresh_any"] is None
    assert "fundamentals" in entry
    assert entry["fundamentals"]["Fund_Source"] == "none"


def test_dashboard_picks_latest_non_no_data_row_per_interval(
    client: TestClient, app_env, monkeypatch
):
    """Dla każdego (ticker, interval) wybieramy najnowszy non-NO_DATA wiersz."""
    m, res, _dat = app_env
    fields = [
        "Ticker",
        "Company_Name",
        "Interval",
        "PCA_Values",
        "Scrape_Status",
        "Exchange",
    ]
    # Starszy plik z OK
    fp_old = res / "tradingview_results_2026-04-01.csv"
    with open(fp_old, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerow({
            "Ticker": "AAA",
            "Company_Name": "Old Co",
            "Interval": "1D",
            "PCA_Values": "10",
            "Scrape_Status": "OK",
            "Exchange": "NASDAQ",
        })
        w.writerow({
            "Ticker": "AAA",
            "Company_Name": "Old Co",
            "Interval": "1W",
            "PCA_Values": "11",
            "Scrape_Status": "OK",
            "Exchange": "NASDAQ",
        })
    # Nowszy plik z OK 1D + NO_DATA 1W (chcemy stary OK 1W zachować)
    fp_new = res / "tradingview_results_2026-05-01.csv"
    with open(fp_new, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerow({
            "Ticker": "AAA",
            "Company_Name": "New Co",
            "Interval": "1D",
            "PCA_Values": "20",
            "Scrape_Status": "OK",
            "Exchange": "NASDAQ",
        })
        w.writerow({
            "Ticker": "AAA",
            "Company_Name": "New Co",
            "Interval": "1W",
            "PCA_Values": "",
            "Scrape_Status": "NO_DATA",
            "Exchange": "NASDAQ",
        })

    monkeypatch.setattr(
        m,
        "load_config",
        lambda: {"tickers": ["AAA"], "intervals": ["1D", "1W"], "indicators": ["PCA"]},
    )
    r = client.get("/api/dashboard")
    assert r.status_code == 200
    body = r.json()
    assert len(body["tickers"]) == 1
    assert len(body["data"]) == 2
    entry = body["tickers"][0]

    # 1D powinien być z nowszego pliku (2026-05-01)
    one_d = entry["intervals"]["1D"]
    assert one_d["row"] is not None
    assert str(one_d["row"]["PCA_Values"]) == "20"
    assert one_d["last_refresh"] == "2026-05-01"

    # 1W powinien być z plika starszego (2026-04-01) bo nowszy jest NO_DATA
    one_w = entry["intervals"]["1W"]
    assert one_w["row"] is not None
    assert str(one_w["row"]["PCA_Values"]) == "11"
    assert one_w["last_refresh"] == "2026-04-01"

    # last_refresh_any = max(1D, 1W) = 2026-05-01
    assert entry["last_refresh_any"] == "2026-05-01"


def test_dashboard_merges_fundamentals(client: TestClient, app_env, monkeypatch):
    m, res, _dat = app_env
    fund = res / "fundamentals.csv"
    fund.write_text(
        "Ticker,Fund_PE,Fund_PB,Fund_EV_EBITDA,Fund_ROE,Fund_NetMargin,Fund_DE,Fund_FCF,Fund_Source,Fund_Updated_At\n"
        "AAA,14.5,2.0,8.0,0.15,0.10,0.4,1200000000,yfinance,2026-05-22T10:00:00Z\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        m,
        "load_config",
        lambda: {"tickers": ["AAA"], "intervals": ["1D"], "indicators": ["PCA"]},
    )
    r = client.get("/api/dashboard")
    assert r.status_code == 200
    entry = r.json()["tickers"][0]
    assert entry["fundamentals"]["Fund_PE"] == pytest.approx(14.5)
    assert entry["fundamentals"]["Fund_Source"] == "yfinance"


def test_dashboard_auto_fetches_missing_fundamentals(client: TestClient, app_env, monkeypatch):
    """Gdy brak wiersza w CSV, dashboard próbuje pobrać fundamentale."""
    m, res, _dat = app_env
    monkeypatch.setattr(
        m,
        "load_config",
        lambda: {
            "tickers": ["GPW:SNT"],
            "intervals": ["1D"],
            "indicators": ["PCA"],
            "fundamentals": {"enabled": True},
        },
    )
    calls = []

    def _fake_fetch(ticker, *, force_refresh=False):
        calls.append((ticker, force_refresh))
        row = {
            "Fund_PE": 18.17,
            "Fund_PB": 2.1,
            "Fund_EV_EBITDA": None,
            "Fund_ROE": None,
            "Fund_NetMargin": None,
            "Fund_DE": None,
            "Fund_FCF": None,
            "Fund_Source": "yfinance",
            "Fund_Updated_At": "2026-05-22T12:00:00Z",
        }
        from results_store import save_fundamentals_row

        save_fundamentals_row(
            {"Ticker": ticker, **row},
            path=str(res / "fundamentals.csv"),
        )
        return row

    monkeypatch.setattr(m, "_fetch_and_persist_fundamentals", _fake_fetch)

    r = client.get("/api/dashboard")
    assert r.status_code == 200
    assert calls == [("GPW:SNT", False)]
    entry = r.json()["tickers"][0]
    assert entry["fundamentals"]["Fund_PE"] == pytest.approx(18.17)
    assert entry["fundamentals"]["Fund_Source"] == "yfinance"


def test_build_dashboard_helper_directly(app_env, monkeypatch):
    """Lightweight test: helper-funkcja zwraca strukturę bez TestClient."""
    m, res, _dat = app_env
    fields = ["Ticker", "Company_Name", "Interval", "PCA_Values", "Scrape_Status"]
    fp = res / "tradingview_results_2026-05-22.csv"
    with open(fp, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerow({
            "Ticker": "AAA",
            "Company_Name": "X Inc.",
            "Interval": "1D",
            "PCA_Values": "5",
            "Scrape_Status": "OK",
        })
    monkeypatch.setattr(
        m,
        "load_config",
        lambda: {"tickers": ["AAA"], "intervals": ["1D"], "indicators": ["PCA"]},
    )
    result = m.build_dashboard()
    assert "tickers" in result
    assert len(result["tickers"]) == 1
    assert "data" in result
    assert len(result["data"]) == 1
    assert result["data"][0]["Ticker"] == "AAA"
    assert result["data"][0]["Last_Refresh"] == "2026-05-22"
    entry = result["tickers"][0]
    assert entry["ticker"] == "AAA"
    assert entry["intervals"]["1D"]["last_refresh"] == "2026-05-22"


def test_dashboard_includes_current_price(app_env, monkeypatch):
    """Dashboard zwraca Current_Price w tickers i płaskich wierszach."""
    m, res, _dat = app_env
    fields = [
        "Ticker",
        "Company_Name",
        "Exchange",
        "Current_Price",
        "Interval",
        "PCA_Values",
        "Scrape_Status",
    ]
    fp = res / "tradingview_results_2026-05-22.csv"
    with open(fp, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerow({
            "Ticker": "GPW:ATC",
            "Company_Name": "Arctic Paper",
            "Exchange": "GPW",
            "Current_Price": "42,50",
            "Interval": "1D",
            "PCA_Values": "5",
            "Scrape_Status": "OK",
        })
    monkeypatch.setattr(
        m,
        "load_config",
        lambda: {
            "tickers": ["GPW:ATC"],
            "intervals": ["1D", "1W"],
            "indicators": ["PCA"],
        },
    )
    result = m.build_dashboard()
    entry = result["tickers"][0]
    assert entry["current_price"] == "42,50"
    row_1d = next(r for r in result["data"] if r["Interval"] == "1D")
    assert row_1d["Current_Price"] == "42,50"


def test_dashboard_current_price_fallback_to_other_interval(app_env, monkeypatch):
    """Gdy 1D brak ceny, bierzemy z pierwszego dostępnego interwału."""
    m, res, _dat = app_env
    fields = [
        "Ticker",
        "Company_Name",
        "Exchange",
        "Current_Price",
        "Interval",
        "PCA_Values",
        "Scrape_Status",
    ]
    fp = res / "tradingview_results_2026-05-22.csv"
    with open(fp, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerow({
            "Ticker": "GPW:ATC",
            "Company_Name": "Arctic Paper",
            "Exchange": "GPW",
            "Current_Price": "",
            "Interval": "1D",
            "PCA_Values": "5",
            "Scrape_Status": "OK",
        })
        w.writerow({
            "Ticker": "GPW:ATC",
            "Company_Name": "Arctic Paper",
            "Exchange": "GPW",
            "Current_Price": "8,10",
            "Interval": "1W",
            "PCA_Values": "6",
            "Scrape_Status": "OK",
        })
    monkeypatch.setattr(
        m,
        "load_config",
        lambda: {
            "tickers": ["GPW:ATC"],
            "intervals": ["1D", "1W"],
            "indicators": ["PCA"],
        },
    )
    result = m.build_dashboard()
    assert result["tickers"][0]["current_price"] == "8,10"


def test_dashboard_marks_all_rows_in_config(client: TestClient, app_env, monkeypatch):
    """Dashboard budowany z configu — każdy wiersz ma In_Config=True."""
    m, _res, _dat = app_env
    monkeypatch.setattr(
        m,
        "load_config",
        lambda: {"tickers": ["AAA", "GPW:BBB"], "intervals": ["1D"], "indicators": ["PCA"]},
    )
    r = client.get("/api/dashboard")
    assert r.status_code == 200
    body = r.json()
    assert body["config_ticker_count"] == 2
    for row in body["data"]:
        assert row["In_Config"] is True
        assert row["Config_Match"] == row["Ticker"]
        assert row["Config_Status"] == "exact"
        assert row["Config_Candidates"] == []


def test_dashboard_maps_csv_bare_symbol_to_config_ticker(
    client: TestClient, app_env, monkeypatch
):
    """CSV z bare symbolem (ATC) mapuje się na GPW:ATC z configu."""
    m, res, _dat = app_env
    fields = [
        "Ticker",
        "Company_Name",
        "Interval",
        "PCA_Values",
        "Scrape_Status",
        "Exchange",
    ]
    fp = res / "tradingview_results_2026-05-22.csv"
    with open(fp, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerow({
            "Ticker": "ATC",
            "Company_Name": "Arctic Paper",
            "Interval": "1D",
            "PCA_Values": "0.3",
            "Scrape_Status": "OK",
            "Exchange": "GPW",
        })

    monkeypatch.setattr(
        m,
        "load_config",
        lambda: {"tickers": ["GPW:ATC"], "intervals": ["1D"], "indicators": ["PCA"]},
    )
    r = client.get("/api/dashboard")
    assert r.status_code == 200
    body = r.json()
    assert len(body["data"]) == 1
    row = body["data"][0]
    assert row["Ticker"] == "GPW:ATC"
    assert row["In_Config"] is True
    assert row["PCA_Values"] == "0.3"
    assert body["tickers"][0]["ticker"] == "GPW:ATC"


def test_dashboard_maps_bare_cdr_csv_to_gpw_cdr(
    client: TestClient, app_env, monkeypatch
):
    """Bare CDR (GPW) w CSV mapuje się na GPW:CDR z configu."""
    m, res, _dat = app_env
    fields = [
        "Ticker",
        "Company_Name",
        "Exchange",
        "Interval",
        "PCA_Values",
        "Scrape_Status",
    ]
    fp = res / "tradingview_results_2026-05-22.csv"
    with open(fp, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerow({
            "Ticker": "CDR",
            "Company_Name": "CD Projekt S.A.",
            "Exchange": "GPW",
            "Interval": "1D",
            "PCA_Values": "25.8 (Zielony)",
            "Scrape_Status": "OK",
        })

    monkeypatch.setattr(
        m,
        "load_config",
        lambda: {
            "tickers": ["GPW:CDR"],
            "intervals": ["1D"],
            "indicators": ["PCA"],
        },
    )
    r = client.get("/api/dashboard")
    assert r.status_code == 200
    body = r.json()
    assert len(body["data"]) == 1
    row = body["data"][0]
    assert row["Ticker"] == "GPW:CDR"
    assert row["PCA_Values"] == "25.8 (Zielony)"


def test_dashboard_maps_legacy_asbp_csv_to_gpw_asb(
    client: TestClient, app_env, monkeypatch
):
    """Stare wiersze ASBP w CSV mapują się na GPW:ASB z configu bez ponownego rename."""
    m, res, _dat = app_env
    fields = [
        "Ticker",
        "Company_Name",
        "Interval",
        "PCA_Values",
        "Scrape_Status",
        "Exchange",
    ]
    fp = res / "tradingview_results_2026-05-22.csv"
    with open(fp, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerow({
            "Ticker": "ASBP",
            "Company_Name": "Asseco BS",
            "Interval": "1D",
            "PCA_Values": "0.4",
            "Scrape_Status": "OK",
            "Exchange": "GPW",
        })

    monkeypatch.setattr(
        m,
        "load_config",
        lambda: {"tickers": ["GPW:ASB"], "intervals": ["1D"], "indicators": ["PCA"]},
    )
    r = client.get("/api/dashboard")
    assert r.status_code == 200
    body = r.json()
    assert len(body["data"]) == 1
    row = body["data"][0]
    assert row["Ticker"] == "GPW:ASB"
    assert row["PCA_Values"] == "0.4"


# ---------------------------------------------------------------------------
# /api/fundamentals
# ---------------------------------------------------------------------------


def test_fundamentals_list_empty(client: TestClient, app_env):
    r = client.get("/api/fundamentals")
    assert r.status_code == 200
    assert r.json() == {"data": []}


def test_fundamentals_list_and_get(client: TestClient, app_env):
    _m, res, _dat = app_env
    fund = res / "fundamentals.csv"
    fund.write_text(
        "Ticker,Fund_PE,Fund_PB,Fund_EV_EBITDA,Fund_ROE,Fund_NetMargin,Fund_DE,Fund_FCF,Fund_Source,Fund_Updated_At\n"
        "AAA,12.0,2.0,8.0,0.15,0.10,0.4,1.2e9,yfinance,2026-05-22T10:00:00Z\n",
        encoding="utf-8",
    )
    r = client.get("/api/fundamentals")
    assert r.status_code == 200
    body = r.json()
    assert len(body["data"]) == 1
    assert body["data"][0]["Ticker"] == "AAA"
    assert body["data"][0]["Fund_PE"] == pytest.approx(12.0)
    assert body["data"][0]["Fund_Source"] == "yfinance"

    r2 = client.get("/api/fundamentals/AAA")
    assert r2.status_code == 200
    assert r2.json()["Fund_PE"] == pytest.approx(12.0)


def test_fundamentals_get_not_found(client: TestClient, app_env):
    r = client.get("/api/fundamentals/MISSING")
    assert r.status_code == 404


def test_fundamentals_refresh_yfinance_only(client: TestClient, app_env, monkeypatch):
    """POST /api/fundamentals/refresh: yfinance only — bez Playwright."""
    m, res, _dat = app_env

    calls = []

    def _fake_fetch(ticker, **kwargs):
        calls.append((ticker, dict(kwargs)))
        assert kwargs.get("tv_fallback_page") is None
        return {
            "Ticker": ticker,
            "Fund_PE": 9.5,
            "Fund_PB": None,
            "Fund_EV_EBITDA": None,
            "Fund_ROE": None,
            "Fund_NetMargin": None,
            "Fund_DE": None,
            "Fund_FCF": None,
            "Fund_Source": "yfinance",
            "Fund_Updated_At": "2026-05-22T10:00:00Z",
        }

    monkeypatch.setattr(m, "fetch_fundamentals", _fake_fetch)
    monkeypatch.setattr(m, "check_yfinance_available", lambda: (True, None))

    r = client.post("/api/fundamentals/refresh", json={"tickers": ["AAA"]})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["count"] == 1
    assert body["with_data"] == 1
    assert body["without_data"] == 0
    assert body["refreshed"][0]["Ticker"] == "AAA"
    assert body["refreshed"][0]["Fund_PE"] == pytest.approx(9.5)
    assert (res / "fundamentals.csv").exists()
    assert len(calls) == 1


def test_fundamentals_refresh_all_fail_returns_503(client: TestClient, app_env, monkeypatch):
    """Gdy wszystkie tickery bez danych — 503, nie fałszywy sukces."""
    m, _res, _dat = app_env

    def _fake_fetch(ticker, **kwargs):
        return {
            "Ticker": ticker,
            "Fund_PE": None,
            "Fund_PB": None,
            "Fund_EV_EBITDA": None,
            "Fund_ROE": None,
            "Fund_NetMargin": None,
            "Fund_DE": None,
            "Fund_FCF": None,
            "Fund_Source": "none",
            "Fund_Updated_At": "2026-05-22T10:00:00Z",
        }

    monkeypatch.setattr(m, "fetch_fundamentals", _fake_fetch)
    monkeypatch.setattr(m, "check_yfinance_available", lambda: (True, None))

    r = client.post("/api/fundamentals/refresh", json={"tickers": ["AAA", "BBB"]})
    assert r.status_code == 503
    detail = r.json().get("detail", "")
    assert "Brak danych" in detail


def test_fundamentals_refresh_yfinance_missing_returns_503(
    client: TestClient, app_env, monkeypatch
):
    m, _res, _dat = app_env
    monkeypatch.setattr(
        m,
        "check_yfinance_available",
        lambda: (False, "No module named 'yfinance'"),
    )
    monkeypatch.setattr(
        m,
        "_fundamentals_config",
        lambda: {"enabled": True, "cache_ttl_hours": 24, "tv_fallback": False},
    )

    r = client.post("/api/fundamentals/refresh", json={"tickers": ["AAA"]})
    assert r.status_code == 503
    assert "yfinance" in r.json().get("detail", "").lower()


def test_fundamentals_refresh_partial_reports_counts(client: TestClient, app_env, monkeypatch):
    m, _res, _dat = app_env

    def _fake_fetch(ticker, **kwargs):
        if ticker == "AAA":
            return {
                "Ticker": ticker,
                "Fund_PE": 9.5,
                "Fund_PB": None,
                "Fund_EV_EBITDA": None,
                "Fund_ROE": None,
                "Fund_NetMargin": None,
                "Fund_DE": None,
                "Fund_FCF": None,
                "Fund_Source": "yfinance",
                "Fund_Updated_At": "2026-05-22T10:00:00Z",
            }
        return {
            "Ticker": ticker,
            "Fund_PE": None,
            "Fund_PB": None,
            "Fund_EV_EBITDA": None,
            "Fund_ROE": None,
            "Fund_NetMargin": None,
            "Fund_DE": None,
            "Fund_FCF": None,
            "Fund_Source": "none",
            "Fund_Updated_At": "2026-05-22T10:00:00Z",
        }

    monkeypatch.setattr(m, "fetch_fundamentals", _fake_fetch)
    monkeypatch.setattr(m, "check_yfinance_available", lambda: (True, None))

    r = client.post("/api/fundamentals/refresh", json={"tickers": ["AAA", "BBB"]})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "partial"
    assert body["count"] == 2
    assert body["with_data"] == 1
    assert body["without_data"] == 1


def test_fundamentals_refresh_requires_tickers(client: TestClient, app_env):
    r = client.post("/api/fundamentals/refresh", json={})
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# /api/results merge Fund_*
# ---------------------------------------------------------------------------


def test_results_merges_fundamentals_into_each_row(client: TestClient, app_env):
    _m, res, _dat = app_env
    fp = res / "tradingview_results_2026-04-01.csv"
    with open(fp, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(
            f,
            fieldnames=["Ticker", "Company_Name", "Interval", "PCA_Values"],
        )
        w.writeheader()
        w.writerow(
            {
                "Ticker": "AAA",
                "Company_Name": "TestCo",
                "Interval": "1D",
                "PCA_Values": "10",
            }
        )
        w.writerow(
            {
                "Ticker": "AAA",
                "Company_Name": "TestCo",
                "Interval": "1W",
                "PCA_Values": "12",
            }
        )
    fund = res / "fundamentals.csv"
    fund.write_text(
        "Ticker,Fund_PE,Fund_PB,Fund_EV_EBITDA,Fund_ROE,Fund_NetMargin,Fund_DE,Fund_FCF,Fund_Source,Fund_Updated_At\n"
        "AAA,14,2,8,0.15,0.10,0.4,1.2e9,yfinance,2026-05-22T10:00:00Z\n",
        encoding="utf-8",
    )
    r = client.get("/api/results/2026-04-01")
    assert r.status_code == 200
    rows = r.json()["data"]
    assert len(rows) == 2
    # Każdy wiersz musi dostać te same Fund_*
    for row in rows:
        assert row["Fund_PE"] == pytest.approx(14.0)
        assert row["Fund_Source"] == "yfinance"


# ---------------------------------------------------------------------------
# /api/tickers/no_data + no_data_only scraper (dashboard-aligned)
# ---------------------------------------------------------------------------


def test_no_data_tickers_includes_never_scraped_config(client: TestClient, app_env, monkeypatch):
    """Tickery z configu bez żadnego wiersza w CSV muszą trafić na listę no-data."""
    m, _res, _dat = app_env
    monkeypatch.setattr(
        m,
        "load_config",
        lambda: {
            "tickers": ["NEVER1", "NEVER2", "HASDATA"],
            "intervals": ["1D"],
            "indicators": ["PCA"],
        },
    )
    r = client.get("/api/tickers/no_data")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 3
    assert set(body["tickers"]) == {"NEVER1", "NEVER2", "HASDATA"}


def test_no_data_tickers_excludes_ok_and_includes_csv_no_data(
    client: TestClient, app_env, monkeypatch
):
    m, res, _dat = app_env
    fields = [
        "Ticker",
        "Company_Name",
        "Interval",
        "PCA_Values",
        "Scrape_Status",
    ]
    fp = res / "tradingview_results_2026-05-22.csv"
    with open(fp, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerow({
            "Ticker": "HASDATA",
            "Company_Name": "Has Data Inc",
            "Interval": "1D",
            "PCA_Values": "12.3 (Niebieski)",
            "Scrape_Status": "OK",
        })
        w.writerow({
            "Ticker": "NODATA",
            "Company_Name": "No Data Inc",
            "Interval": "1D",
            "PCA_Values": "",
            "Scrape_Status": "NO_DATA",
        })
    monkeypatch.setattr(
        m,
        "load_config",
        lambda: {
            "tickers": ["HASDATA", "NODATA", "FRESH"],
            "intervals": ["1D"],
            "indicators": ["PCA"],
        },
    )
    r = client.get("/api/tickers/no_data")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 2
    assert set(body["tickers"]) == {"NODATA", "FRESH"}


def test_scraper_run_no_data_only_includes_never_scraped(
    client: TestClient, app_env, monkeypatch
):
    m, _res, _dat = app_env
    monkeypatch.setattr(
        m,
        "load_config",
        lambda: {
            "tickers": ["0016", "AAPL"],
            "intervals": ["1D", "1W", "1M"],
            "indicators": ["PCA"],
        },
    )
    called = {}

    def fake(tickers=None, indicators=None):
        called["tickers"] = list(tickers or [])
        return {"status": "started", "pid": 999, "count": len(tickers or [])}

    monkeypatch.setattr(m, "start_scraper_subprocess", fake)
    r = client.post("/api/scraper/run", json={"no_data_only": True})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "started"
    assert body["scope"] == "no_data_only"
    assert body["count"] == 2
    assert set(called["tickers"]) == {"0016", "AAPL"}


def test_dashboard_uses_no_data_row_when_indicators_present(
    client: TestClient, app_env, monkeypatch
):
    """Legacy CSV rows marked NO_DATA but with values (e.g. ALB) must appear on dashboard."""
    m, res, _dat = app_env
    fields = [
        "Ticker",
        "Company_Name",
        "Interval",
        "Scrape_Status",
        "Scrape_Error",
        "PCA_Values",
        "HTS Panel_Fast_High",
        "HTS Panel_Trend",
        "MacD_Line",
    ]
    fp = res / "tradingview_results_2026-05-22.csv"
    with open(fp, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerow({
            "Ticker": "ALB",
            "Company_Name": "Albemarle Corporation",
            "Interval": "1D",
            "Scrape_Status": "NO_DATA",
            "Scrape_Error": "Brak danych wskaźników na wykresie",
            "PCA_Values": "22.43 (Niebieski)",
            "HTS Panel_Fast_High": "186.12 (Niebieski)",
            "HTS Panel_Trend": "Wzrostowy",
            "MacD_Line": "−3.88 (Czerwony)",
        })

    monkeypatch.setattr(
        m,
        "load_config",
        lambda: {
            "tickers": ["ALB"],
            "intervals": ["1D"],
            "indicators": ["PCA", "HTS Panel", "MacD"],
        },
    )
    r = client.get("/api/dashboard")
    assert r.status_code == 200
    body = r.json()
    row = body["data"][0]
    assert row["Ticker"] == "ALB"
    assert row.get("Missing_Indicators") == []
    assert row.get("Scrape_Status") == "OK"
    assert body["tickers"][0]["intervals"]["1D"]["row"] is not None


def test_scraper_run_single_ticker_alb(client, monkeypatch):
    """Per-ticker rescrape API contract: POST /api/scraper/run with one ticker."""
    import app as m

    seen = {}

    def fake(tickers=None, indicators=None):
        seen["tickers"] = list(tickers or [])
        return {"status": "started", "pid": 42, "count": len(tickers or []), "scope": "subset"}

    monkeypatch.setattr(m, "start_scraper_subprocess", fake)
    r = client.post("/api/scraper/run", json={"tickers": ["ALB"]})
    assert r.status_code == 200
    assert r.json()["status"] == "started"
    assert seen["tickers"] == ["ALB"]


def test_resolve_config_symbol_bare_alb():
    import app as m

    cfg = ["FCX", "ALB", "GPW:XTB"]
    resolution = m._resolve_config_symbol("ALB", cfg)
    assert resolution["in_config"] is True
    assert resolution["match"] == "ALB"
    assert resolution["status"] == "exact"


def test_dashboard_merges_hts_from_older_csv_when_newer_partial(
    client: TestClient, app_env, monkeypatch
):
    """Nowszy wiersz z PCA+MacD nie może wyczyścić HTS ze starszego pliku."""
    m, res, _dat = app_env
    fields = [
        "Ticker",
        "Company_Name",
        "Interval",
        "Scrape_Status",
        "PCA_Values",
        "HTS Panel_Trend",
        "HTS Panel_Fast_High",
        "MacD_Line",
    ]
    fp_old = res / "tradingview_results_2026-05-18.csv"
    with open(fp_old, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerow({
            "Ticker": "BTCUSDT",
            "Company_Name": "Bitcoin",
            "Interval": "1D",
            "Scrape_Status": "NO_DATA",
            "PCA_Values": "31.73 (Niebieski)",
            "HTS Panel_Trend": "Spadkowy",
            "HTS Panel_Fast_High": "77,613.93 (Niebieski)",
            "MacD_Line": "1,005.89 (Czerwony)",
        })
    fp_new = res / "tradingview_results_2026-05-22.csv"
    with open(fp_new, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerow({
            "Ticker": "BTCUSDT",
            "Company_Name": "Bitcoin",
            "Interval": "1D",
            "Scrape_Status": "",
            "PCA_Values": "40.00 (Zielony)",
            "HTS Panel_Trend": "",
            "HTS Panel_Fast_High": "",
            "MacD_Line": "2.00 (Czerwony)",
        })
    monkeypatch.setattr(
        m,
        "load_config",
        lambda: {
            "tickers": ["BTCUSDT"],
            "intervals": ["1D"],
            "indicators": ["PCA", "HTS Panel", "MacD"],
        },
    )
    r = client.get("/api/dashboard")
    assert r.status_code == 200
    row = r.json()["data"][0]
    assert row["PCA_Values"] == "40.00 (Zielony)"
    assert row["MacD_Line"] == "2.00 (Czerwony)"
    assert row["HTS Panel_Trend"] == "Spadkowy"
    assert "77,613.93" in str(row["HTS Panel_Fast_High"])
    assert row.get("Missing_Indicators") == []


def test_no_data_tickers_includes_stale_btcusdt(client: TestClient, app_env, monkeypatch):
    """Ticker obecny tylko w starszym CSV (poza latest bulk scrape) trafia na listę odświeżenia."""
    m, res, _dat = app_env
    fields = [
        "Ticker",
        "Company_Name",
        "Interval",
        "Scrape_Status",
        "PCA_Values",
        "HTS Panel_Trend",
        "HTS Panel_Fast_High",
        "MacD_Line",
    ]
    fp_old = res / "tradingview_results_2026-05-18.csv"
    with open(fp_old, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for interval in ("1D", "1W", "1M"):
            w.writerow({
                "Ticker": "BTCUSDT",
                "Company_Name": "Bitcoin",
                "Interval": interval,
                "Scrape_Status": "NO_DATA",
                "PCA_Values": "1 (Niebieski)",
                "HTS Panel_Trend": "Spadkowy",
                "HTS Panel_Fast_High": "100 (Niebieski)",
                "MacD_Line": "0.5 (Czerwony)",
            })
    fp_new = res / "tradingview_results_2026-05-22.csv"
    with open(fp_new, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for interval in ("1D", "1W", "1M"):
            w.writerow({
                "Ticker": "FRESH",
                "Company_Name": "Fresh Inc",
                "Interval": interval,
                "Scrape_Status": "OK",
                "PCA_Values": "12 (Niebieski)",
                "HTS Panel_Trend": "Wzrostowy",
                "HTS Panel_Fast_High": "10 (Niebieski)",
                "MacD_Line": "0.1 (Zielony)",
            })
    monkeypatch.setattr(
        m,
        "load_config",
        lambda: {
            "tickers": ["BTCUSDT", "FRESH"],
            "intervals": ["1D", "1W", "1M"],
            "indicators": ["PCA", "HTS Panel", "MacD"],
        },
    )
    r = client.get("/api/tickers/no_data")
    assert r.status_code == 200
    assert "BTCUSDT" in r.json()["tickers"]
    assert "FRESH" not in r.json()["tickers"]
