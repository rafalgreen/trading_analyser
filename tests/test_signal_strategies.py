from signal_strategies import (
    compute_band_touch,
    compute_signals,
    strategy_band_touch,
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
        pca="18.0 (color: rgb(0, 128, 255);)",
    )
    assert strategy_trend_only(r) == "strong buy"


def test_trend_only_buy_with_pca_up_to_40():
    r = _row(
        hts_trend="Wzrostowy",
        macd_trend="Wzrostowy",
        pca="22.10 (color: rgb(0, 128, 255);)",
    )
    assert strategy_trend_only(r) == "buy"


def test_trend_only_neutral_when_uptrend_but_pca_midrange():
    r = _row(hts_trend="Wzrostowy", macd_trend="Wzrostowy", pca="55.0")
    assert strategy_trend_only(r) == "neutral"


def test_trend_only_sell_when_uptrend_but_pca_high():
    r = _row(hts_trend="Wzrostowy", macd_trend="Wzrostowy", pca="72.0")
    assert strategy_trend_only(r) == "sell"


def test_trend_only_strong_sell_when_uptrend_but_pca_very_high():
    r = _row(hts_trend="Wzrostowy", macd_trend="Wzrostowy", pca="90.19")
    assert strategy_trend_only(r) == "strong sell"


def test_trend_only_strong_sell_with_very_high_pca():
    r = _row(hts_trend="Spadkowy", macd_trend="Spadkowy", pca="85.0")
    assert strategy_trend_only(r) == "strong sell"


def test_trend_only_sell_with_pca_from_60():
    r = _row(hts_trend="Spadkowy", macd_trend="Spadkowy", pca="72.0")
    assert strategy_trend_only(r) == "sell"


def test_trend_only_neutral_when_downtrend_but_pca_midrange():
    r = _row(hts_trend="Spadkowy", macd_trend="Spadkowy", pca="45.0")
    assert strategy_trend_only(r) == "neutral"


def test_trend_only_buy_when_downtrend_but_pca_low():
    r = _row(hts_trend="Spadkowy", macd_trend="Spadkowy", pca="35.0")
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
        "band_touch",
    }
    assert out["trend_only"] == "buy"  # 2× Wzrostowy, PCA 35 ∈ (20, 40]
    assert out["scoring"] == "strong buy"  # +1 +1 +1 (PCA<=40) = 3
    assert out["pca_buckets"] == "buy"  # 35 ∈ (20, 40]
    assert out["cross_priority"] == "buy"  # brak crossów, fallback do trendu


def test_compute_signals_empty_when_any_indicator_missing():
    """Stale PCA alone must not produce buy badges (KWEB-like partial merge)."""
    row = _row(pca="15.0")
    indicators = ["PCA", "HTS Panel", "MacD"]
    out = compute_signals(row, indicators=indicators)
    assert all(v == "" for v in out.values())
    assert strategy_scoring(row, indicators=indicators) == ""


def test_compute_signals_requires_macd_line_not_trend_only():
    row = _row(
        hts_trend="Wzrostowy",
        macd_trend="Wzrostowy",
        pca="35.0",
    )
    indicators = ["PCA", "HTS Panel", "MacD"]
    out = compute_signals(row, indicators=indicators)
    assert all(v == "" for v in out.values())


# ---------------------------------------------------------------------------
# Band touch (dotknięcie czerwonej wstęgi HTS Slow)
# ---------------------------------------------------------------------------


def _band_row(
    price: str,
    slow_high: str,
    slow_low: str,
    hts_trend: str = "Wzrostowy",
    pca: str = "25.0",
    interval: str = "1D",
    fast_high: str = "",
    fast_low: str = "",
):
    row = _row(hts_trend=hts_trend, pca=pca)
    row["Current_Price"] = price
    row["HTS Panel_Slow_High"] = slow_high
    row["HTS Panel_Slow_Low"] = slow_low
    if fast_high:
        row["HTS Panel_Fast_High"] = fast_high
    if fast_low:
        row["HTS Panel_Fast_Low"] = fast_low
    row["Interval"] = interval
    return row


def test_band_touch_inside_band_is_touch():
    out = compute_band_touch(_band_row("100", "101 (Czerwony)", "98 (Czerwony)"))
    assert out["state"] == "touch"
    assert out["side"] == "inside"
    assert out["distance_pct"] == 0.0


def test_band_touch_near_above_within_tolerance():
    out = compute_band_touch(_band_row("101.5", "100", "97"), tolerance_pct=2.0)
    assert out["state"] == "near"
    assert out["side"] == "above"
    assert out["distance_pct"] <= 2.0


def test_band_touch_far_above_is_none():
    out = compute_band_touch(_band_row("120", "100", "97"), tolerance_pct=2.0)
    assert out["state"] == "none"
    assert out["side"] == "above"


def test_band_touch_polish_and_us_number_formats():
    out = compute_band_touch(
        _band_row("77 613,93", "77,613.93 (Niebieski)", "74 000,00")
    )
    assert out["state"] == "touch"


def test_band_touch_empty_without_price_or_band():
    assert compute_band_touch(_band_row("", "100", "97"))["state"] == ""
    assert compute_band_touch(_band_row("100", "", ""))["state"] == ""


def test_band_touch_rejects_nan_price_string_from_csv():
    """CSV zapisuje brak ceny jako literalny string 'nan' — nie wolno liczyć odległości."""
    out = compute_band_touch(_band_row("nan", "40.02 (Czerwony)", "38.09 (Czerwony)"))
    assert out["state"] == ""
    assert out["distance_pct"] is None


def test_strategy_band_touch_strong_buy_uptrend_touch_low_pca():
    r = _band_row("100", "101", "98", hts_trend="Wzrostowy", pca="25.0")
    assert strategy_band_touch(r) == "strong buy"


def test_strategy_band_touch_buy_uptrend_near_low_pca():
    r = _band_row("101.5", "100", "97", hts_trend="Wzrostowy", pca="30.0")
    assert strategy_band_touch(r) == "buy"


def test_strategy_band_touch_neutral_when_pca_not_low():
    r = _band_row("100", "101", "98", hts_trend="Wzrostowy", pca="55.0")
    assert strategy_band_touch(r) == "neutral"


def test_strategy_band_touch_sell_downtrend_near_band_from_below():
    r = _band_row("99", "102", "100", hts_trend="Spadkowy", pca="50.0")
    out = strategy_band_touch(r)
    assert out == "sell"


def test_strategy_band_touch_strong_sell_downtrend_touch_high_pca():
    r = _band_row("101", "102", "100", hts_trend="Spadkowy", pca="70.0")
    assert strategy_band_touch(r) == "strong sell"


def test_strategy_band_touch_skips_monthly_interval():
    r = _band_row("100", "101", "98", interval="1M")
    assert strategy_band_touch(r) == ""


def test_strategy_band_touch_weekly_interval_allowed():
    r = _band_row("100", "101", "98", interval="1W")
    assert strategy_band_touch(r) == "strong buy"


def test_strategy_band_touch_neutral_when_far_from_band():
    r = _band_row("150", "101", "98", hts_trend="Wzrostowy", pca="20.0")
    assert strategy_band_touch(r) == "neutral"


def test_strategy_band_touch_computed_even_without_macd():
    """band_touch nie używa MacD — brak MacD_Line nie blokuje sygnału."""
    r = _band_row("100", "101", "98", hts_trend="Wzrostowy", pca="25.0")
    indicators = ["PCA", "HTS Panel", "MacD"]
    out = compute_signals(r, indicators=indicators)
    assert out["band_touch"] == "strong buy"
    assert out["scoring"] == ""  # klasyczne strategie wciąż gate'owane


def test_strategy_band_touch_custom_params():
    r = _band_row("103", "100", "97", hts_trend="Wzrostowy", pca="40.0")
    # Domyślnie: distance 3% ≤ 4% tolerancji i PCA 40 → buy
    assert strategy_band_touch(r) == "buy"
    # Za wysokie PCA przy domyślnych progach → neutral
    r_high_pca = _band_row("103", "100", "97", hts_trend="Wzrostowy", pca="41.0")
    assert strategy_band_touch(r_high_pca) == "neutral"
    # Luźniejszy próg PCA → buy
    out = strategy_band_touch(r_high_pca, params={"buy_pca_max": 45.0})
    assert out == "buy"


def test_band_touch_wmt_d1_red_edge_counts_as_touch():
    """NASDAQ:WMT 1D — cena tuż nad czerwoną Slow, wizualnie dotyka krawędzi."""
    r = _band_row(
        "113.7",
        "113.45 (Czerwony)",
        "110.54 (Czerwony)",
        fast_high="121.17 (Niebieski)",
        fast_low="117.92 (Niebieski)",
        hts_trend="Wzrostowy",
        pca="25.0",
        interval="1D",
    )
    touch = compute_band_touch(r, tolerance_pct=4.0, edge_touch_pct=0.3)
    assert touch["state"] == "touch"
    assert touch["ribbon"] == "red"
    assert touch["distance_pct"] == 0.22
    assert strategy_band_touch(r) == "strong buy"


def test_band_touch_wmt_w1_blue_fast_band():
    """NASDAQ:WMT 1W — cena przy niebieskiej Fast, czerwona Slow daleko."""
    r = _band_row(
        "113.7",
        "78.37 (Czerwony)",
        "74.24 (Czerwony)",
        fast_high="113.65 (Niebieski)",
        fast_low="107.14 (Niebieski)",
        hts_trend="Wzrostowy",
        pca="25.53",
        interval="1W",
    )
    touch = compute_band_touch(r, tolerance_pct=4.0, edge_touch_pct=0.3)
    assert touch["state"] == "touch"
    assert touch["ribbon"] == "blue"
    assert strategy_band_touch(r) == "strong buy"
