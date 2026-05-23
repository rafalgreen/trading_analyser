"""Tests for composite verdict scoring."""

from __future__ import annotations

import pytest

from composite_score import (
    VERDICT_KUP,
    VERDICT_OBSERWUJ,
    VERDICT_UNIKAJ,
    compute_composite_verdict,
)


def _row(
    *,
    hts: str = "Wzrostowy",
    macd: str = "Wzrostowy",
    pca: str = "35",
) -> dict:
    return {
        "HTS Panel_Trend": hts,
        "MacD_Trend": macd,
        "MacD_Line": "1.0 (Zielony)",
        "PCA_Values": pca,
    }


def _quality_fundamentals() -> dict:
    return {
        "Fund_PE": 12.0,
        "Fund_PB": 0.9,
        "Fund_EV_EBITDA": 9.0,
        "Fund_ROE": 0.18,
        "Fund_NetMargin": 0.22,
        "Fund_DE": 80.0,
        "Fund_FCF": 1_000_000_000.0,
    }


def test_quality_stock_gets_kup():
    intervals = {
        "1D": _row(pca="35"),
        "1W": _row(pca="35"),
        "1M": _row(pca="35"),
    }
    result = compute_composite_verdict("AAPL", intervals, _quality_fundamentals())
    assert result["verdict"] == VERDICT_KUP
    assert result["score"] >= 40
    assert not result["flags"]
    assert result["breakdown"]["fund"] > 0
    assert result["breakdown"]["tech"] > 0
    assert result["breakdown"]["consensus"] > 0


def test_value_trap_red_flag_forces_unikaj():
    fundamentals = {
        "Fund_PE": 8.0,
        "Fund_PB": 0.6,
        "Fund_EV_EBITDA": 7.0,
        "Fund_ROE": -0.05,
        "Fund_NetMargin": 0.05,
        "Fund_DE": 250.0,
        "Fund_FCF": -500_000.0,
    }
    intervals = {
        "1D": _row(hts="Spadkowy", macd="Spadkowy", pca="75"),
        "1W": _row(hts="Spadkowy", macd="Spadkowy", pca="75"),
        "1M": _row(hts="Spadkowy", macd="Spadkowy", pca="75"),
    }
    result = compute_composite_verdict("TRAP", intervals, fundamentals)
    assert result["verdict"] == VERDICT_UNIKAJ
    assert "ROE<0" in result["flags"]


def test_crypto_neutral_fundamentals_layer():
    fundamentals = {f"Fund_{k}": None for k in ("PE", "PB", "EV_EBITDA", "ROE", "NetMargin", "DE", "FCF")}
    # Fix key names to match FUND_KEYS
    fundamentals = {
        "Fund_PE": None,
        "Fund_PB": None,
        "Fund_EV_EBITDA": None,
        "Fund_ROE": None,
        "Fund_NetMargin": None,
        "Fund_DE": None,
        "Fund_FCF": None,
    }
    intervals = {
        "1W": _row(pca="50"),
    }
    result = compute_composite_verdict("BTCUSDT", intervals, fundamentals)
    assert result["breakdown"]["fund"] == pytest.approx(0.0)
    assert result["verdict"] in (VERDICT_OBSERWUJ, VERDICT_KUP, VERDICT_UNIKAJ)


def test_mixed_signals_obserwuj():
    fundamentals = {
        "Fund_PE": 22.0,
        "Fund_PB": 2.0,
        "Fund_EV_EBITDA": 14.0,
        "Fund_ROE": 0.12,
        "Fund_NetMargin": 0.12,
        "Fund_DE": 150.0,
        "Fund_FCF": 100.0,
    }
    intervals = {
        "1D": _row(hts="Wzrostowy", macd="Spadkowy", pca="50"),
        "1W": _row(hts="Spadkowy", macd="Wzrostowy", pca="50"),
        "1M": _row(pca="50"),
    }
    result = compute_composite_verdict("MIX", intervals, fundamentals)
    assert result["verdict"] == VERDICT_OBSERWUJ
    assert -20 < result["score"] < 40
    assert not result["flags"]


def test_fcf_negative_high_de_red_flag():
    fundamentals = {
        "Fund_PE": 18.0,
        "Fund_ROE": 0.12,
        "Fund_FCF": -1.0,
        "Fund_DE": 250.0,
    }
    intervals = {"1W": _row()}
    result = compute_composite_verdict("X", intervals, fundamentals)
    assert "FCF<0&D/E>200" in result["flags"]
    assert result["verdict"] == VERDICT_UNIKAJ


def _partial_pca_only_row(pca: str = "22.0") -> dict:
    return {"PCA_Values": pca}


def test_fundamentals_only_never_kup_without_technical_data():
    """Fundamentals alone (or stale PCA partial merge) must not yield Kup."""
    intervals = {
        "1D": _partial_pca_only_row("22.0"),
        "1W": _partial_pca_only_row("22.0"),
        "1M": _partial_pca_only_row("22.0"),
    }
    result = compute_composite_verdict("KWEB", intervals, _quality_fundamentals())
    assert result["verdict"] != VERDICT_KUP
    assert result["verdict"] == VERDICT_OBSERWUJ
    assert "Brak danych technicznych" in result["flags"]
    assert result["breakdown"]["tech"] == 0.0
    assert result["breakdown"]["consensus"] == 0.0
    assert result["breakdown"]["fund"] > 0


def test_kweb_like_partial_merge_stale_pca_no_buy_badges():
    """One interval with only PCA (HTS/MacD missing) — tech layers stay neutral."""
    intervals = {
        "1W": {
            "PCA_Values": "18.5 (Niebieski)",
            "HTS Panel_Trend": "",
            "MacD_Trend": "",
        },
    }
    indicators = ["PCA", "HTS Panel", "MacD"]
    result = compute_composite_verdict(
        "KWEB", intervals, _quality_fundamentals(), indicators=indicators
    )
    assert result["verdict"] == VERDICT_OBSERWUJ
    assert result["breakdown"]["tech"] == 0.0
    assert result["breakdown"]["consensus"] == 0.0
