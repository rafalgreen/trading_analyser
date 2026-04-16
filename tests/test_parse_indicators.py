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
