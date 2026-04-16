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


def test_is_dirty_company_name():
    import app as m

    assert m.is_dirty_company_name("X", "") is True
    assert m.is_dirty_company_name("MSFT", "MSFT 123") is True
    assert m.is_dirty_company_name("MSFT", "▼ −1%") is True
    assert m.is_dirty_company_name("MSFT", "Microsoft") is False
