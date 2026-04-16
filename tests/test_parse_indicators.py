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
