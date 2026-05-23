"""Regresja: synchronizacja scraper_config.json ↔ CSV ↔ dashboard.

Dashboard buduje listę tickerów wyłącznie z configu; wiersze CSV bez
dopasowania w configu są ignorowane (orphans). Testy poniżej dokumentują
to zachowanie i pilnują, by tickery GPW z danymi w CSV były w configu.
"""

from __future__ import annotations

import csv
import glob
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = PROJECT_ROOT / "scraper_config.json"
RESULTS_DIR = PROJECT_ROOT / "results"
CACHE_PATH = PROJECT_ROOT / "data" / ".fundamentals_cache.json"


def _load_config_tickers() -> list[str]:
    with open(CONFIG_PATH, encoding="utf-8") as fh:
        return list(json.load(fh).get("tickers") or [])


def _latest_results_csv() -> Path | None:
    files = sorted(RESULTS_DIR.glob("tradingview_results_*.csv"))
    return files[-1] if files else None


def _gpw_tickers_in_csv(path: Path) -> set[str]:
    """Zwraca symbole GPW:* obecne w pliku CSV (kanoniczny prefiks GPW:)."""
    found: set[str] = set()
    with open(path, encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            ticker = str(row.get("Ticker") or "").strip()
            exchange = str(row.get("Exchange") or "").strip().upper()
            if not ticker:
                continue
            if ticker.startswith("GPW:"):
                found.add(ticker)
            elif exchange == "GPW":
                found.add(f"GPW:{ticker}")
    return found


def _csv_orphans(config_tickers: list[str]) -> list[str]:
    import app as m

    orphans: set[str] = set()
    for path in sorted(RESULTS_DIR.glob("tradingview_results_*.csv")):
        with open(path, encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                ticker = str(row.get("Ticker") or "").strip()
                if not ticker:
                    continue
                if not m._config_ticker_for_csv_symbol(ticker, config_tickers):
                    orphans.add(ticker)
    return sorted(orphans)


# ---------------------------------------------------------------------------
# Dashboard: config jest źródłem prawdy
# ---------------------------------------------------------------------------


def test_dashboard_excludes_csv_orphan_not_in_config(
    client: TestClient, app_env, monkeypatch
):
    """Wiersze CSV bez tickera w configu nie pojawiają się na dashboardzie."""
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
            "Ticker": "AAA",
            "Company_Name": "In Config",
            "Interval": "1D",
            "PCA_Values": "10",
            "Scrape_Status": "OK",
            "Exchange": "NASDAQ",
        })
        w.writerow({
            "Ticker": "ORPHAN",
            "Company_Name": "Not In Config",
            "Interval": "1D",
            "PCA_Values": "99",
            "Scrape_Status": "OK",
            "Exchange": "GPW",
        })

    monkeypatch.setattr(
        m,
        "load_config",
        lambda: {"tickers": ["AAA"], "intervals": ["1D"], "indicators": ["PCA"]},
    )
    r = client.get("/api/dashboard")
    assert r.status_code == 200
    body = r.json()
    tickers = {row["Ticker"] for row in body["data"]}
    assert tickers == {"AAA"}
    assert body["config_ticker_count"] == 1


def test_dashboard_maps_bare_gpw_csv_to_config_ticker(
    client: TestClient, app_env, monkeypatch
):
    """Ticker w configu (GPW:CDR) widoczny na dashboardzie mimo bare symbolu w CSV."""
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
    assert row["In_Config"] is True


def test_gpw_cdr_present_in_production_config():
    """GPW:CDR musi być w scraper_config.json (regresja po fixie dashboardu)."""
    tickers = _load_config_tickers()
    assert "GPW:CDR" in tickers


# ---------------------------------------------------------------------------
# Integracja: config ↔ rzeczywiste pliki CSV / cache
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not CONFIG_PATH.exists() or not list(RESULTS_DIR.glob("tradingview_results_*.csv")),
    reason="Brak scraper_config.json lub plików CSV w results/",
)
def test_config_includes_tickers_with_csv_data():
    """Każdy GPW:* z najnowszego CSV musi być w scraper_config.json."""
    config_tickers = set(_load_config_tickers())
    latest = _latest_results_csv()
    assert latest is not None

    gpw_in_csv = _gpw_tickers_in_csv(latest)
    missing = sorted(gpw_in_csv - config_tickers)
    assert not missing, (
        f"Tickery GPW w {latest.name} bez wpisu w configu: {missing}. "
        "Dodaj je do scraper_config.json, inaczej dashboard ich nie pokaże."
    )


@pytest.mark.skipif(
    not CONFIG_PATH.exists() or not list(RESULTS_DIR.glob("tradingview_results_*.csv")),
    reason="Brak scraper_config.json lub plików CSV w results/",
)
def test_config_csv_orphans_are_documented():
    """Orphan tickery (CSV bez configu) — fail tylko dla GPW:* wymagających configu.

    Oczekiwane zachowanie: dashboard ignoruje wiersze CSV, których nie da się
    zmapować na ticker z configu. Stare / błędne symbole mogą pozostać w
    historycznych CSV (known_legacy) — nie wymagają wpisu w configu.
    """
    config_tickers = _load_config_tickers()
    orphans = _csv_orphans(config_tickers)

    known_legacy = {
        "B24P.WA",
        "ETH/USD - Ethereum US Dollar",
        "FROP.WA",
        "LIOP",
        "PSHG_p",
        "WLIL.SI",
    }
    actionable = sorted(set(orphans) - known_legacy)
    gpw_actionable = [t for t in actionable if t.startswith("GPW:")]

    assert not gpw_actionable, (
        "Orphan tickery GPW w CSV bez configu "
        f"(dodaj do scraper_config.json): {gpw_actionable}. "
        f"Pozostałe orphans (legacy): {sorted(set(orphans) & known_legacy)}"
    )


@pytest.mark.skipif(
    not CACHE_PATH.exists() or not CONFIG_PATH.exists(),
    reason="Brak cache fundamentów lub configu",
)
def test_cache_gpw_with_data_in_config():
    """GPW:* z cache (Fund_Source != none) muszą być w configu."""
    config_tickers = set(_load_config_tickers())
    with open(CACHE_PATH, encoding="utf-8") as fh:
        cache = json.load(fh)

    missing = sorted(
        k
        for k, v in cache.items()
        if k.startswith("GPW:")
        and (v.get("Fund_Source") or "none") != "none"
        and k not in config_tickers
    )
    assert not missing, (
        f"GPW tickery z danymi w cache bez configu: {missing}"
    )
