from signal_strategies import (
    compute_signals,
    strategy_cross_priority,
    strategy_pca_buckets,
    strategy_scoring,
    strategy_trend_only,
)


def _row(
    hts_trend: str = "",
    macd_trend: str = "",
    hts_cross: str = "",
    macd_cross: str = "",
    pca: str = "",
):
    return {
        "HTS Panel_Trend": hts_trend,
        "MacD_Trend": macd_trend,
        "HTS Panel_Cross": hts_cross,
        "MacD_Cross": macd_cross,
        "PCA_Values": pca,
    }


def test_trend_only_strong_buy_with_low_pca():
    r = _row(
        hts_trend="Wzrostowy",
        macd_trend="Wzrostowy",
        pca="22.10 (color: rgb(0, 128, 255);)",
    )
    assert strategy_trend_only(r) == "strong buy"


def test_trend_only_strong_sell_with_high_pca():
    r = _row(hts_trend="Spadkowy", macd_trend="Spadkowy", pca="72.0")
    assert strategy_trend_only(r) == "strong sell"


def test_trend_only_buy_without_pca_threshold():
    r = _row(hts_trend="Wzrostowy", macd_trend="Wzrostowy", pca="55.0")
    assert strategy_trend_only(r) == "buy"


def test_trend_only_neutral_when_mixed():
    r = _row(hts_trend="Wzrostowy", macd_trend="Spadkowy", pca="55.0")
    assert strategy_trend_only(r) == "neutral"


def test_trend_only_empty_when_no_trends():
    assert strategy_trend_only(_row()) == ""


def test_cross_priority_two_bull_crosses():
    r = _row(hts_cross="BULL CROSS (Wstęgi)", macd_cross="BULL CROSS")
    assert strategy_cross_priority(r) == "strong buy"


def test_cross_priority_one_bear_with_high_pca():
    r = _row(macd_cross="BEAR CROSS", pca="72.0")
    assert strategy_cross_priority(r) == "strong sell"


def test_cross_priority_falls_back_to_trend():
    r = _row(hts_trend="Wzrostowy", macd_trend="Wzrostowy")
    assert strategy_cross_priority(r) == "buy"


def test_pca_buckets_strong_buy_low_value():
    assert strategy_pca_buckets(_row(pca="15.0")) == "strong buy"


def test_pca_buckets_neutral_middle():
    assert strategy_pca_buckets(_row(pca="50.0")) == "neutral"


def test_pca_buckets_strong_sell_high_value():
    assert strategy_pca_buckets(_row(pca="85.0")) == "strong sell"


def test_pca_buckets_empty_without_value():
    assert strategy_pca_buckets(_row()) == ""


def test_pca_values_preferred_over_legacy_pca_value():
    row = _row(pca="12.54 (Niebieski)")
    row["PCA_Value"] = "35.50"
    out = compute_signals(row)
    assert out["pca_buckets"] == "strong buy"


def test_scoring_strong_buy_all_aligned():
    r = _row(hts_trend="Wzrostowy", macd_trend="Wzrostowy", pca="35.0")
    assert strategy_scoring(r) == "strong buy"


def test_scoring_strong_sell_all_aligned():
    r = _row(hts_trend="Spadkowy", macd_trend="Spadkowy", pca="80.0")
    assert strategy_scoring(r) == "strong sell"


def test_scoring_neutral_zero_sum():
    r = _row(hts_trend="Wzrostowy", macd_trend="Spadkowy", pca="50.0")
    assert strategy_scoring(r) == "neutral"


def test_compute_signals_returns_all_strategies():
    r = _row(hts_trend="Wzrostowy", macd_trend="Wzrostowy", pca="35.0")
    out = compute_signals(r)
    assert set(out.keys()) == {
        "trend_only",
        "cross_priority",
        "pca_buckets",
        "scoring",
    }
    assert out["trend_only"] == "strong buy"  # 2× Wzrostowy, PCA<=40
    assert out["scoring"] == "strong buy"  # +1 +1 +1 (PCA<=40) = 3
    assert out["pca_buckets"] == "buy"  # 35 ∈ (20, 40]
    assert out["cross_priority"] == "buy"  # brak crossów, fallback do trendu
