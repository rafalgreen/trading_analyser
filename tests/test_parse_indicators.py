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


def test_to_float_handles_nbsp_and_unicode_minus():
    from tv_scraper import _to_float

    assert _to_float("1\u00a0234,56") == 1234.56
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
      <div class="valueItem">abc</div>
      <div class="valueItem">def</div>
      <div class="valueItem">ghi</div>
      <div class="valueItem">jkl</div>
    </div>
    """
    r = parse_indicators(html, ["HTS Panel"])
    assert r["HTS Panel_Fast_High"].startswith("abc")
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
      <div class="valueItem">1.1</div>
      <div class="valueItem">2.2</div>
      <div class="valueItem">3.3</div>
      <div class="valueItem">4.4</div>
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
        <div class="valueItem">10</div>
        <div class="valueItem">20</div>
        <div class="valueItem">30</div>
        <div class="valueItem">40</div>
      </div>
      <div class="valueItem">99</div>
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
      <div class="valueItem">1</div>
      <div class="valueItem">2</div>
      <div class="valueItem">3</div>
      <div class="valueItem">4</div>
    </div>
    """
    r = parse_indicators(html, ["MacD"])
    assert r["MacD_Fast_High"].startswith("1")


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


def test_resolve_company_name_falls_back_when_title_is_only_ticker():
    from tv_scraper import resolve_company_name

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
