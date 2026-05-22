from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def pca_html():
    return (ROOT / "tests" / "fixtures" / "minimal_legend_pca.html").read_text(
        encoding="utf-8"
    )


def test_parse_indicators_pca(pca_html: str):
    from tv_scraper import parse_indicators

    r = parse_indicators(pca_html, ["PCA"])
    assert r.get("PCA_Value") == "77.37"
    assert "PCA_Values" in r
    assert "77.37" in r["PCA_Values"]


def test_parse_indicators_pca_prefers_legend_values_over_settings():
    from tv_scraper import parse_indicators

    html = """
    <div data-qa-id="legend-source-item">
      <div data-qa-id="title-wrapper legend-source-title">PCA-RI</div>
      <div class="valueValue">9</div>
      <div class="valueValue">12</div>
      <div data-qa-id="legend-source-values">
        <div class="valueItem">
          <div class="valueValue" style="color: rgb(255, 107, 0);">77.37</div>
        </div>
      </div>
      <div class="valueValue">999</div>
    </div>
    """
    r = parse_indicators(html, ["PCA"])
    assert r["PCA_Value"] == "77.37"
    assert "999" not in r["PCA_Values"]


def test_parse_indicators_pca_ignores_valueitem_container_text():
    from tv_scraper import parse_indicators

    html = """
    <div data-qa-id="legend-source-item">
      <div data-qa-id="title-wrapper legend-source-title">PCA Risk</div>
      <div class="valueItem">
        <div class="valueTitle">PC1</div>
        <div class="valueValue" style="color: rgb(0, 188, 212);">35.37</div>
      </div>
      <div class="valueItem">1000</div>
    </div>
    """
    r = parse_indicators(html, ["PCA"])
    assert r["PCA_Value"] == "35.37"
    assert "PC1" not in r["PCA_Values"]


def test_to_float_handles_nbsp_and_unicode_minus():
    from tv_scraper import _to_float

    assert _to_float("1\u00a0234,56") == 1234.56
    assert _to_float("77,613.90") == 77613.90
    assert _to_float("86,600.46") == 86600.46
    assert _to_float("77.613,90") == 77613.90
    assert _to_float("\u22125,7") == -5.7
    assert _to_float("  42 ") == 42.0
    assert _to_float("abc") is None
    assert _to_float(None) is None
    assert _to_float("") is None


def test_parse_indicators_survives_nonnumeric_hts_values():
    from tv_scraper import parse_indicators

    html = """
    <div data-qa-id="legend-source-item">
      <div data-qa-id="title-wrapper legend-source-title">HTS Panel</div>
      <div class="valueValue">abc</div>
      <div class="valueValue">def</div>
      <div class="valueValue">ghi</div>
      <div class="valueValue">jkl</div>
    </div>
    """
    r = parse_indicators(html, ["HTS Panel"])
    assert r["HTS Panel_Fast_High"].startswith("abc")
    assert r["HTS Panel_Trend"] == "Brak trendu"
    assert r["HTS Panel_Cross"] == "Brak Crossa"


def test_parse_indicators_no_matching_block_returns_placeholder():
    from tv_scraper import parse_indicators

    r = parse_indicators("<html></html>", ["PCA", "HTS Panel"])
    assert r["PCA_Values"] == "Brak danych na wykresie"
    assert r["HTS Panel_Values"] == "Brak danych na wykresie"


def test_parse_indicators_hts_panel_version_title():
    """Tytuł typu ``HTS PANEL v3.0.2`` — dopasowanie po ``hts`` + ``panel``."""
    from tv_scraper import parse_indicators

    html = """
    <div data-qa-id="legend-source-item">
      <div data-qa-id="title-wrapper legend-source-title">HTS PANEL v3.0.2</div>
      <div class="valueValue">1.1</div>
      <div class="valueValue">2.2</div>
      <div class="valueValue">3.3</div>
      <div class="valueValue">4.4</div>
    </div>
    """
    r = parse_indicators(html, ["HTS Panel"])
    assert r["HTS Panel_Fast_High"].startswith("1.1")


def test_parse_indicators_hts_uses_legend_source_values_wrapper():
    from tv_scraper import parse_indicators

    html = """
    <div data-qa-id="legend-source-item">
      <div data-qa-id="title-wrapper legend-source-title">HTS Panel</div>
      <div data-qa-id="legend-source-values">
        <div class="valueValue">10</div>
        <div class="valueValue">20</div>
        <div class="valueValue">30</div>
        <div class="valueValue">40</div>
      </div>
      <div class="valueValue">99</div>
    </div>
    """
    r = parse_indicators(html, ["HTS Panel"])
    assert r["HTS Panel_Fast_High"].startswith("10")
    assert "99" not in r["HTS Panel_Fast_High"]


def test_parse_indicators_macd_title_case_insensitive():
    from tv_scraper import parse_indicators

    html = """
    <div data-qa-id="legend-source-item">
      <div data-qa-id="title-wrapper legend-source-title">MACD 12 26 close</div>
      <div class="valueValue">1</div>
      <div class="valueValue">2</div>
      <div class="valueValue">3</div>
      <div class="valueValue">4</div>
    </div>
    """
    r = parse_indicators(html, ["MacD"])
    assert r["MacD_Fast_High"].startswith("1")
    assert r["MacD_Trend"] == "Spadkowy"
    assert r["MacD_Cross"] == "Brak Crossa"


def test_indicator_title_matches_macd_only_exact_config_name():
    from tv_scraper import indicator_title_matches

    assert indicator_title_matches("MACD 12 26", "MacD") is True
    assert indicator_title_matches("Moving Average", "MacD") is False


def test_resolve_company_name_prefers_title_over_duplicate_legend_ticker():
    from tv_scraper import resolve_company_name

    title = "Palantir Technologies Inc. Class A · 4h · NASDAQ — TradingView"
    assert (
        resolve_company_name(title, "PLTR", "PLTR")
        == "Palantir Technologies Inc. Class A"
    )


def test_resolve_company_name_falls_back_when_title_is_only_ticker(monkeypatch):
    import company_names
    from tv_scraper import resolve_company_name

    monkeypatch.setattr(company_names, "lookup_company_name", lambda t: "")

    title = "FCX — TradingView"
    assert resolve_company_name(title, "FCX", "FCX") == "FCX"


def test_resolve_company_name_uses_header_toolbar_second_line():
    from tv_scraper import resolve_company_name

    title = "NKE · 1D · NYSE — TradingView"
    header = "NKE\nNike, Inc."
    assert resolve_company_name(title, "NKE", "NKE", header) == "Nike, Inc."


def test_resolve_company_name_prefers_symbol_search_modal():
    from tv_scraper import resolve_company_name

    title = "NKE — TradingView"
    assert (
        resolve_company_name(
            title,
            "NKE",
            "NKE",
            "NKE",
            "NIKE, Inc. Class B",
        )
        == "NIKE, Inc. Class B"
    )


def test_company_name_from_symbol_search_modal_text_nke():
    from tv_scraper import company_name_from_symbol_search_modal_text

    blob = "NKE\nNIKE, Inc. Class B\nstock\nNYSE"
    assert company_name_from_symbol_search_modal_text(blob, "NKE") == "NIKE, Inc. Class B"


def test_parse_symbol_search_modal_blob_extracts_exchange_from_separate_line():
    from tv_scraper import parse_symbol_search_modal_blob

    blob = "ZIM\nZIM Integrated Shipping Services Ltd.\nstock\nNYSE"
    info = parse_symbol_search_modal_blob(blob, "ZIM")
    assert info == {
        "name": "ZIM Integrated Shipping Services Ltd.",
        "exchange": "NYSE",
    }


def test_parse_symbol_search_modal_blob_extracts_exchange_from_ticker_prefix():
    from tv_scraper import parse_symbol_search_modal_blob

    blob = "GPW:ATC\nArctic Paper SA\nstock"
    info = parse_symbol_search_modal_blob(blob, "ATC")
    assert info["exchange"] == "GPW"
    assert info["name"] == "Arctic Paper SA"


def test_parse_symbol_search_modal_blob_no_exchange_returns_empty_string():
    from tv_scraper import parse_symbol_search_modal_blob

    blob = "BTCUSDT\nBitcoin / Tether\nspot"
    info = parse_symbol_search_modal_blob(blob, "BTCUSDT")
    assert info["name"] == "Bitcoin / Tether"
    assert info["exchange"] == ""


def test_parse_symbol_search_modal_blob_ticker_mismatch_returns_blank():
    from tv_scraper import parse_symbol_search_modal_blob

    blob = "ZIM\nZIM Integrated Shipping Services Ltd.\nstock\nNYSE"
    assert parse_symbol_search_modal_blob(blob, "NKE") == {"name": "", "exchange": ""}


def test_resolve_exchange_prefers_symbol_search_over_other_sources(monkeypatch):
    import company_names
    from tv_scraper import resolve_exchange

    def _no_rest(_t):  # pragma: no cover — defence against accidental network
        raise AssertionError("REST should not be called when modal value is set")

    monkeypatch.setattr(company_names, "lookup_exchange", _no_rest)

    assert resolve_exchange("ZIM", symbol_search_exchange="NYSE") == "NYSE"


def test_resolve_exchange_falls_back_to_ticker_prefix(monkeypatch):
    import company_names
    from tv_scraper import resolve_exchange

    monkeypatch.setattr(company_names, "lookup_exchange", lambda _t: "")

    assert resolve_exchange("GPW:ATC") == "GPW"


def test_resolve_exchange_uses_header_blob_when_others_empty(monkeypatch):
    import company_names
    from tv_scraper import resolve_exchange

    monkeypatch.setattr(company_names, "lookup_exchange", lambda _t: "")

    blob = "Charts · ZIM Integrated · NYSE · 1D"
    assert resolve_exchange("ZIM", header_blob=blob) == "NYSE"


def test_resolve_exchange_falls_back_to_rest_lookup(monkeypatch):
    import company_names
    from tv_scraper import resolve_exchange

    monkeypatch.setattr(company_names, "lookup_exchange", lambda _t: "NASDAQ")

    assert resolve_exchange("AAPL") == "NASDAQ"


def test_resolve_exchange_returns_empty_when_no_source_known(monkeypatch):
    import company_names
    from tv_scraper import resolve_exchange

    monkeypatch.setattr(company_names, "lookup_exchange", lambda _t: "")

    assert resolve_exchange("FOO") == ""


def test_resolve_company_name_header_parenthetical():
    from tv_scraper import resolve_company_name

    assert (
        resolve_company_name("NKE — TradingView", "NKE", "NKE", "Nike, Inc. (NKE)")
        == "Nike, Inc."
    )


def test_company_name_from_title_parenthetical_with_ticker():
    from tv_scraper import _company_name_from_title

    t = "Nike, Inc. (NKE) · 1D — TradingView"
    assert _company_name_from_title(t, "NKE") == "Nike, Inc."


def test_pick_company_from_toolbar_before_parenthetical():
    from tv_scraper import _pick_company_line_from_header_blob

    blob = "Charts Nike, Inc. (NKE) · 1D · NYSE"
    assert _pick_company_line_from_header_blob(blob, "NKE") == "Nike, Inc."


def test_resolve_company_name_falls_back_to_rest_lookup(monkeypatch):
    """Gdy żadne źródło DOM nie zwraca sensownej nazwy, używamy TV REST."""
    import company_names
    import tv_scraper

    calls = []

    def fake_lookup(ticker: str) -> str:
        calls.append(ticker)
        return "Palantir Technologies Inc."

    monkeypatch.setattr(company_names, "lookup_company_name", fake_lookup)

    result = tv_scraper.resolve_company_name(
        title_text="PLTR — TradingView",
        legend_description="PLTR",
        ticker="PLTR",
        header_toolbar_text="PLTR",
        symbol_search_text="",
    )

    assert result == "Palantir Technologies Inc."
    assert calls == ["PLTR"]


def test_resolve_company_name_rest_lookup_blank_returns_ticker(monkeypatch):
    """Gdy REST też zwróci pusto, kończymy na samym tickerze (bez 'Nieznana')."""
    import company_names
    import tv_scraper

    monkeypatch.setattr(company_names, "lookup_company_name", lambda t: "")

    result = tv_scraper.resolve_company_name(
        title_text="ZZZZZ — TradingView",
        legend_description="ZZZZZ",
        ticker="ZZZZZ",
        header_toolbar_text="ZZZZZ",
        symbol_search_text="",
    )

    assert result == "ZZZZZ"


def _hts_legend_html(
    fh: str,
    fl: str,
    sh: str,
    sl: str,
    *,
    double_dom: bool = False,
) -> str:
    """HTML legendy HTS; ``double_dom`` symuluje valueItem + valueValue (jak TV)."""
    rows = [(fh, "rgb(0, 188, 212)"), (fl, "rgb(0, 188, 212)"), (sh, "rgb(242, 54, 69)"), (sl, "rgb(242, 54, 69)")]
    parts = []
    for text, color in rows:
        parts.append(
            f'<div class="valueItem">'
            f'<div class="valueValue" style="color: {color};">{text}</div>'
            f"</div>"
        )
        if double_dom:
            parts.append(f'<div class="valueValue">{text}</div>')
    return f"""
    <div data-qa-id="legend-source-item">
      <div data-qa-id="title-wrapper legend-source-title">HTS PANEL v3.0.2</div>
      <div data-qa-id="legend-source-values">
        {"".join(parts)}
      </div>
    </div>
    """


def test_hts_txt_d1_bear_cross_after_valuevalue_only():
    """GPW:TXT 1D — Fast pod Slow → BEAR CROSS + Spadkowy."""
    from tv_scraper import parse_indicators

    html = _hts_legend_html("40.15", "38.75", "49.46", "47.71")
    r = parse_indicators(html, ["HTS Panel"])
    assert r["HTS Panel_Fast_High"].startswith("40.15")
    assert r["HTS Panel_Slow_Low"].startswith("47.71")
    assert r["HTS Panel_Trend"] == "Spadkowy"
    assert "BEAR CROSS" in r["HTS Panel_Cross"]


def test_hts_double_dom_ignored_valueitem():
    """valueItem + valueValue nie mogą podwajać slotów."""
    from tv_scraper import parse_indicators

    html = _hts_legend_html("40.15", "38.75", "49.46", "47.71", double_dom=True)
    r = parse_indicators(html, ["HTS Panel"])
    assert r["HTS Panel_Slow_High"].startswith("49.46")
    assert r["HTS Panel_Trend"] == "Spadkowy"
    assert "BULL CROSS" not in (r.get("HTS Panel_Cross") or "")


def test_hts_bull_cross_wzrostowy():
    from tv_scraper import parse_indicators

    html = _hts_legend_html("50", "48", "45", "44")
    r = parse_indicators(html, ["HTS Panel"])
    assert r["HTS Panel_Trend"] == "Wzrostowy"
    assert "BULL CROSS" in r["HTS Panel_Cross"]


def test_hts_btc_d1_us_number_format_bear_cross():
    from tv_scraper import parse_indicators

    html = _hts_legend_html("77,613.90", "74,972.69", "86,600.46", "83,273.50")
    r = parse_indicators(html, ["HTS Panel"])
    assert r["HTS Panel_Trend"] == "Spadkowy"
    assert "BEAR CROSS" in r["HTS Panel_Cross"]


def test_hts_btc_w1_us_number_format_bull_cross():
    from tv_scraper import parse_indicators

    html = _hts_legend_html("90,464.40", "81,192.04", "61,844.24", "54,835.09")
    r = parse_indicators(html, ["HTS Panel"])
    assert r["HTS Panel_Trend"] == "Wzrostowy"
    assert "BULL CROSS" in r["HTS Panel_Cross"]


def test_macd_cm_ult_mtf_txt_d1():
    from tv_scraper import parse_indicators

    html = """
    <div data-qa-id="legend-source-item">
      <div data-qa-id="title-wrapper legend-source-title">CM_Ult_MacD_MTF 60 12 26 12</div>
      <div data-qa-id="legend-source-values">
        <div class="valueItem">
          <div class="valueTitle">MACD</div>
          <div class="valueValue" style="color: rgb(242, 54, 69);">0.4866</div>
        </div>
        <div class="valueItem">
          <div class="valueTitle">Signal</div>
          <div class="valueValue" style="color: rgb(255, 235, 59);">0.6206</div>
        </div>
        <div class="valueItem">
          <div class="valueTitle">Histogram</div>
          <div class="valueValue" style="color: rgb(242, 54, 69);">-0.1340</div>
        </div>
        <div class="valueItem">
          <div class="valueTitle">Cross</div>
          <div class="valueValue" style="color: rgb(242, 54, 69);">0.6318</div>
        </div>
      </div>
    </div>
    """
    r = parse_indicators(html, ["MacD"])
    assert r["MacD_Line"].startswith("0.4866")
    assert r["MacD_Signal"].startswith("0.6206")
    assert r["MacD_Trend"] == "Spadkowy"
    assert r["MacD_Cross"] == "BEAR CROSS"


def test_macd_prefers_ult_mtf_over_builtin():
    from tv_scraper import parse_indicators

    html = """
    <div data-qa-id="legend-source-item">
      <div data-qa-id="title-wrapper legend-source-title">MACD 12 26 close</div>
      <div class="valueValue">0.1</div>
      <div class="valueValue">0.2</div>
      <div class="valueValue">0.3</div>
      <div class="valueValue">0.4</div>
    </div>
    <div data-qa-id="legend-source-item">
      <div data-qa-id="title-wrapper legend-source-title">CM_Ult_MacD_MTF 60 12 26 12</div>
      <div class="valueValue">0.4866</div>
      <div class="valueValue">0.6206</div>
      <div class="valueValue">-0.1340</div>
      <div class="valueValue">0.6318</div>
    </div>
    """
    r = parse_indicators(html, ["MacD"])
    assert r["MacD_Line"].startswith("0.4866")


def test_indicator_search_query_from_config():
    from tv_scraper import _indicator_search_query

    q = _indicator_search_query("MacD", {"MacD": "CM_Ult_MacD_MTF"})
    assert q == "CM_Ult_MacD_MTF"


def test_get_color_name_macd_green_yellow():
    from tv_scraper import get_color_name

    assert get_color_name("color: rgb(0, 255, 0);") == "Zielony"
    assert get_color_name("color: rgb(255, 255, 0);") == "Żółty"
    assert get_color_name("color: rgb(255, 235, 59);") == "Żółty"


def test_macd_tv_green_yellow_legend():
    from tv_scraper import parse_indicators

    html = """
    <div data-qa-id="legend-source-item">
      <div data-qa-id="title-wrapper legend-source-title">CM_Ult_MacD_MTF</div>
      <div data-qa-id="legend-source-values">
        <div class="valueItem"><div class="valueValue" style="color: rgb(0, 255, 0);">0,6850</div></div>
        <div class="valueItem"><div class="valueValue" style="color: rgb(255, 255, 0);">−0,1787</div></div>
        <div class="valueItem"><div class="valueValue" style="color: rgb(242, 54, 69);">-0,1340</div></div>
        <div class="valueItem"><div class="valueValue" style="color: rgb(242, 54, 69);">0,6318</div></div>
      </div>
    </div>
    """
    r = parse_indicators(html, ["MacD"])
    assert "Zielony" in r["MacD_Line"]
    assert "Żółty" in r["MacD_Signal"]
    assert r["MacD_Trend"] == "Wzrostowy"


def _labeled_value_item(label: str, text: str, color: str) -> str:
    return (
        f'<div class="valueItem">'
        f'<div class="valueTitle">{label}</div>'
        f'<div class="valueValue" style="color: {color};">{text}</div>'
        f"</div>"
    )

def test_macd_green_line_red_cross_badge_independent_of_trend():
    """MACD > Signal → Wzrostowy; badge Cross osobno z koloru Cross."""
    from tv_scraper import parse_indicators

    items = [
        _labeled_value_item("MACD", "-13,47", "rgb(0, 255, 0)"),
        _labeled_value_item("Signal", "-14,12", "rgb(255, 235, 59)"),
        _labeled_value_item("Cross", "0,12", "rgb(242, 54, 69)"),
    ]
    html = f"""
    <div data-qa-id="legend-source-item">
      <div data-qa-id="title-wrapper legend-source-title">CM_Ult_MacD_MTF</div>
      <div data-qa-id="legend-source-values">{"".join(items)}</div>
    </div>
    """
    r = parse_indicators(html, ["MacD"])
    assert r["MacD_Trend"] == "Wzrostowy"
    assert r["MacD_Cross"] == "BEAR CROSS"
    assert "Zielony" in r["MacD_Line"]


def test_macd_green_line_only_wzrostowy():
    from tv_scraper import parse_indicators

    html = f"""
    <div data-qa-id="legend-source-item">
      <div data-qa-id="title-wrapper legend-source-title">CM_Ult_MacD_MTF</div>
      <div data-qa-id="legend-source-values">
        {_labeled_value_item("MACD", "1,5", "rgb(0, 255, 0)")}
        {_labeled_value_item("Signal", "0,5", "rgb(255, 235, 59)")}
      </div>
    </div>
    """
    r = parse_indicators(html, ["MacD"])
    assert r["MacD_Trend"] == "Wzrostowy"
    assert r["MacD_Cross"] == "Brak Crossa"


def test_hts_1w_overlap_spadkowy():
    """GPW:TXT 1W — nakładające się wstęgi: fl < sh → Spadkowy."""
    from tv_scraper import parse_indicators

    html = f"""
    <div data-qa-id="legend-source-item">
      <div data-qa-id="title-wrapper legend-source-title">HTS PANEL v3.0.2</div>
      <div data-qa-id="legend-source-values">
        {_labeled_value_item("Fast High", "103", "rgb(0, 188, 212)")}
        {_labeled_value_item("Fast Low", "95", "rgb(0, 188, 212)")}
        {_labeled_value_item("Slow High", "99", "rgb(242, 54, 69)")}
        {_labeled_value_item("Slow Low", "90", "rgb(242, 54, 69)")}
      </div>
    </div>
    """
    r = parse_indicators(html, ["HTS Panel"])
    assert r["HTS Panel_Trend"] == "Spadkowy"
    assert r["HTS Panel_Cross"] == "Brak Crossa"
    assert r["HTS Panel_Fast_Low"].startswith("95")


def test_hts_trend_change_label():
    from tv_scraper import parse_indicators

    html = f"""
    <div data-qa-id="legend-source-item">
      <div data-qa-id="title-wrapper legend-source-title">HTS Panel</div>
      <div data-qa-id="legend-source-values">
        {_labeled_value_item("Fast High", "50", "rgb(0, 188, 212)")}
        {_labeled_value_item("Fast Low", "48", "rgb(0, 188, 212)")}
        {_labeled_value_item("Slow High", "45", "rgb(242, 54, 69)")}
        {_labeled_value_item("Slow Low", "44", "rgb(242, 54, 69)")}
        {_labeled_value_item("TREND Change", "2,5", "rgb(0, 255, 0)")}
      </div>
    </div>
    """
    r = parse_indicators(html, ["HTS Panel"])
    assert "2,5" in r["HTS Panel_Trend_Change"]
    assert "Zielony" in r["HTS Panel_Trend_Change"]

def test_macd_macd_below_signal_spadkowy():
    """D1-like: MACD < Signal → Spadkowy."""
    from tv_scraper import parse_indicators

    html = f"""
    <div data-qa-id="legend-source-item">
      <div data-qa-id="title-wrapper legend-source-title">CM_Ult_MacD_MTF</div>
      <div data-qa-id="legend-source-values">
        {_labeled_value_item("MACD", "0,4866", "rgb(242, 54, 69)")}
        {_labeled_value_item("Signal", "0,6206", "rgb(255, 235, 59)")}
      </div>
    </div>
    """

    r = parse_indicators(html, ["MacD"])
    assert r["MacD_Trend"] == "Spadkowy"


def test_hts_partial_fast_only_m1():
    """M1: tylko Fast High/Low → wartości Fast, trend Brak."""
    from tv_scraper import parse_indicators

    html = f"""
    <div data-qa-id="legend-source-item">
      <div data-qa-id="title-wrapper legend-source-title">HTS PANEL v3.0.2</div>
      <div data-qa-id="legend-source-values">
        {_labeled_value_item("Fast High", "81,37", "rgb(0, 188, 212)")}
        {_labeled_value_item("Fast Low", "75,20", "rgb(0, 188, 212)")}
      </div>
    </div>
    """
    r = parse_indicators(html, ["HTS Panel"])
    assert r["HTS Panel_Fast_High"].startswith("81,37")
    assert r["HTS Panel_Fast_Low"].startswith("75,20")
    assert r.get("HTS Panel_Slow_High") is None
    assert r["HTS Panel_Trend"] == "Brak trendu"
    assert r["HTS Panel_Cross"] == "Brak Crossa"


def test_hts_preserves_empty_slots_before_later_zeroes():
    """Puste slow sloty nie mogą zostać zastąpione późniejszymi zerami z legendy."""
    from tv_scraper import parse_indicators

    html = """
    <div data-qa-id="legend-source-item">
      <div data-qa-id="title-wrapper legend-source-title">HTS PANEL v3.0.2</div>
      <div data-qa-id="legend-source-values">
        <div class="valueItem"><div class="valueValue" style="color: rgb(0, 188, 212);">79.77</div></div>
        <div class="valueItem"><div class="valueValue" style="color: rgb(0, 188, 212);">65.33</div></div>
        <div class="valueItem"><div class="valueValue">∅</div></div>
        <div class="valueItem"><div class="valueValue">∅</div></div>
        <div class="valueItem"><div class="valueValue" style="color: rgb(242, 54, 69);">0.0000</div></div>
        <div class="valueItem"><div class="valueValue" style="color: rgb(0, 188, 212);">0.0000</div></div>
      </div>
    </div>
    """
    r = parse_indicators(html, ["HTS Panel"])
    assert r["HTS Panel_Fast_High"].startswith("79.77")
    assert r["HTS Panel_Fast_Low"].startswith("65.33")
    assert r.get("HTS Panel_Slow_High") is None
    assert r.get("HTS Panel_Slow_Low") is None
    assert r["HTS Panel_Trend"] == "Brak trendu"
    assert r["HTS Panel_Cross"] == "Brak Crossa"


def test_hts_labeled_slow_zero_treated_as_missing():
    """GPW:TXT 1M — etykiety Slow z placeholderem 0.0000 nie wolno trafić do CSV."""
    from tv_scraper import parse_indicators

    html = f"""
    <div data-qa-id="legend-source-item">
      <div data-qa-id="title-wrapper legend-source-title">HTS PANEL v3.0.2</div>
      <div data-qa-id="legend-source-values">
        {_labeled_value_item("Fast High", "81,37", "rgb(0, 188, 212)")}
        {_labeled_value_item("Fast Low", "75,20", "rgb(0, 188, 212)")}
        {_labeled_value_item("Slow High", "0.0000", "rgb(242, 54, 69)")}
        <div class="valueItem"><div class="valueTitle">Slow Low</div><div class="valueValue">∅</div></div>
        {_labeled_value_item("TREND Change", "2,5", "rgb(0, 255, 0)")}
      </div>
    </div>
    """
    r = parse_indicators(html, ["HTS Panel"])
    assert r["HTS Panel_Fast_High"].startswith("81,37")
    assert r["HTS Panel_Fast_Low"].startswith("75,20")
    assert r.get("HTS Panel_Slow_High") is None
    assert r.get("HTS Panel_Slow_Low") is None
    assert r["HTS Panel_Trend"] == "Brak trendu"
    assert "2,5" in r["HTS Panel_Trend_Change"]


def test_macd_gpw_txt_1w_unicode_minus_wzrostowy():
    """GPW:TXT 1W — ujemny MACD nad Signal, zielona linia → Wzrostowy."""
    from tv_scraper import parse_indicators

    html = f"""
    <div data-qa-id="legend-source-item">
      <div data-qa-id="title-wrapper legend-source-title">CM_Ult_MacD_MTF</div>
      <div data-qa-id="legend-source-values">
        {_labeled_value_item("MACD", "−1,839", "rgb(0, 255, 0)")}
        {_labeled_value_item("Signal", "−3,066", "rgb(255, 235, 59)")}
      </div>
    </div>
    """
    r = parse_indicators(html, ["MacD"])
    assert r["MacD_Line"].startswith("−1,839")
    assert r["MacD_Signal"].startswith("−3,066")
    assert r["MacD_Trend"] == "Wzrostowy"
    assert "Zielony" in r["MacD_Line"]


def test_macd_uses_value_title_color_for_trend():
    from tv_scraper import parse_indicators

    html = """
    <div data-qa-id="legend-source-item">
      <div data-qa-id="title-wrapper legend-source-title">CM_Ult_MacD_MTF</div>
      <div data-qa-id="legend-source-values">
        <div class="valueItem">
          <div class="valueTitle" style="color: rgb(0, 255, 0);">MACD</div>
          <div class="valueValue">-15.38</div>
        </div>
        <div class="valueItem">
          <div class="valueTitle">Signal</div>
          <div class="valueValue" style="color: rgb(255, 235, 59);">-10.37</div>
        </div>
      </div>
    </div>
    """
    r = parse_indicators(html, ["MacD"])
    assert "Zielony" in r["MacD_Line"]
    assert r["MacD_Trend"] == "Wzrostowy"


def test_macd_color_takes_priority_over_signal_compare():
    from tv_scraper import parse_indicators

    html = f"""
    <div data-qa-id="legend-source-item">
      <div data-qa-id="title-wrapper legend-source-title">CM_Ult_MacD_MTF</div>
      <div data-qa-id="legend-source-values">
        {_labeled_value_item("MACD", "1.00", "rgb(0, 255, 0)")}
        {_labeled_value_item("Signal", "2.00", "rgb(255, 235, 59)")}
      </div>
    </div>
    """
    r = parse_indicators(html, ["MacD"])
    assert r["MacD_Trend"] == "Wzrostowy"


def test_macd_falls_back_to_signal_compare_when_color_missing():
    from tv_scraper import parse_indicators

    html = """
    <div data-qa-id="legend-source-item">
      <div data-qa-id="title-wrapper legend-source-title">CM_Ult_MacD_MTF</div>
      <div data-qa-id="legend-source-values">
        <div class="valueItem"><div class="valueTitle">MACD</div><div class="valueValue">1.00</div></div>
        <div class="valueItem"><div class="valueTitle">Signal</div><div class="valueValue">2.00</div></div>
      </div>
    </div>
    """
    r = parse_indicators(html, ["MacD"])
    assert r["MacD_Trend"] == "Spadkowy"


def test_macd_title_score_prefers_cm_ult_mtf():
    from tv_scraper import _macd_title_score

    assert _macd_title_score("CM_Ult_MacD_MTF 60 12 26") == 3
    assert _macd_title_score("MACD 12 26 close") == 1
    assert _macd_title_score("Moving Average") == 0


def test_verify_indicator_present_retries(monkeypatch):
    """`_verify_indicator_present` retries until legenda zwróci True."""
    from tv_scraper import _verify_indicator_present

    states = [False, False, True]
    calls: list = []

    def fake_has(_page, name):
        calls.append(name)
        return states.pop(0) if states else False

    monkeypatch.setattr("tv_scraper._page_legend_has_indicator", fake_has)
    monkeypatch.setattr("tv_scraper.time.sleep", lambda _s: None)

    assert _verify_indicator_present(None, "MacD", attempts=3, delay_s=0) is True
    assert calls == ["MacD", "MacD", "MacD"]


def test_verify_indicator_present_returns_false_when_never_appears(monkeypatch):
    from tv_scraper import _verify_indicator_present

    monkeypatch.setattr(
        "tv_scraper._page_legend_has_indicator", lambda _p, _n: False
    )
    monkeypatch.setattr("tv_scraper.time.sleep", lambda _s: None)

    assert _verify_indicator_present(None, "MacD", attempts=3, delay_s=0) is False


def test_macd_scoring_picks_cm_ult_with_both_legend_items_visible():
    """Dwa wpisy legendy jednocześnie: generic MACD i CM_Ult_MacD_MTF (12, 26, 9, ...).

    Scoring (``_macd_title_score`` w ``parse_indicators``) musi wybrać CM_Ult.
    """
    from tv_scraper import parse_indicators

    html = """
    <div data-qa-id="legend-source-item">
      <div data-qa-id="title-wrapper legend-source-title">MACD 12 26 close</div>
      <div data-qa-id="legend-source-values">
        <div class="valueItem">
          <div class="valueTitle">MACD</div>
          <div class="valueValue" style="color: rgb(242, 54, 69);">0.1111</div>
        </div>
        <div class="valueItem">
          <div class="valueTitle">Signal</div>
          <div class="valueValue" style="color: rgb(255, 235, 59);">0.2222</div>
        </div>
      </div>
    </div>
    <div data-qa-id="legend-source-item">
      <div data-qa-id="title-wrapper legend-source-title">CM_Ult_MacD_MTF (12, 26, 9, close)</div>
      <div data-qa-id="legend-source-values">
        <div class="valueItem">
          <div class="valueTitle">MACD</div>
          <div class="valueValue" style="color: rgb(0, 255, 0);">0.9999</div>
        </div>
        <div class="valueItem">
          <div class="valueTitle">Signal</div>
          <div class="valueValue" style="color: rgb(255, 235, 59);">0.5555</div>
        </div>
      </div>
    </div>
    """
    r = parse_indicators(html, ["MacD"])
    assert r["MacD_Line"].startswith("0.9999")
    assert "0.1111" not in r["MacD_Line"]


def test_macd_generic_only_still_parses_score_1_path():
    """Tylko wbudowany ``MACD`` (score=1) — parsowanie i tak ma wypełnić MacD_Line."""
    from tv_scraper import parse_indicators

    html = """
    <div data-qa-id="legend-source-item">
      <div data-qa-id="title-wrapper legend-source-title">MACD 12 26 close</div>
      <div data-qa-id="legend-source-values">
        <div class="valueItem">
          <div class="valueTitle">MACD</div>
          <div class="valueValue" style="color: rgb(242, 54, 69);">0.5</div>
        </div>
        <div class="valueItem">
          <div class="valueTitle">Signal</div>
          <div class="valueValue" style="color: rgb(255, 235, 59);">0.7</div>
        </div>
      </div>
    </div>
    """
    r = parse_indicators(html, ["MacD"])
    assert r.get("MacD_Line", "").startswith("0.5")
    assert r["MacD_Trend"] == "Spadkowy"
