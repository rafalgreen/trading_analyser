from scripts.preview_watchlist_ticker_updates import (
    build_preview_rows,
    choose_suggestion,
    find_current_config_ticker,
    search_candidates_for_row,
    watchlist_exchange_hints,
)


def _cand(symbol, exchange, description, typ="stock"):
    return {
        "symbol": symbol,
        "exchange": exchange,
        "description": description,
        "type": typ,
        "country": "",
        "raw_symbol": symbol,
    }


def test_watchlist_exchange_hints_for_reuters_suffixes():
    assert watchlist_exchange_hints("PAS1.WA") == ["GPW"]
    assert watchlist_exchange_hints("PLTR.O") == ["NASDAQ"]
    assert "NYSE" in watchlist_exchange_hints("BWXT.K")
    assert watchlist_exchange_hints("F34.SI") == ["SGX"]


def test_choose_suggestion_passus_prefers_gpw_match():
    result = choose_suggestion(
        "Passus SA",
        "PAS1.WA",
        [
            _cand("PAS", "GPW", "Passus SA"),
            _cand("PAS", "MIL", "Pasquarelli Auto S.p.A."),
        ],
    )
    assert result["confidence"] == "high_confidence"
    assert result["candidate"]["symbol"] == "PAS"
    assert result["candidate"]["exchange"] == "GPW"


def test_choose_suggestion_newag_prefers_gpw_symbol():
    result = choose_suggestion(
        "Newag",
        "NWGP.WA",
        [
            _cand("NWG", "GPW", "Newag SA"),
            _cand("NEW", "NYSE", "New Fortress Energy Inc."),
        ],
    )
    assert result["confidence"] == "high_confidence"
    assert result["candidate"]["symbol"] == "NWG"
    assert result["candidate"]["exchange"] == "GPW"


def test_choose_suggestion_diagnostyka_maps_to_gpw_dia():
    result = choose_suggestion(
        "Diagnostyka SA",
        "DIAP.WA",
        [
            _cand("DIA", "GPW", "Diagnostyka SA"),
            _cand("DIA", "AMEX", "SPDR Dow Jones Industrial Average ETF"),
        ],
    )
    assert result["confidence"] == "high_confidence"
    assert result["candidate"]["symbol"] == "DIA"
    assert result["candidate"]["exchange"] == "GPW"


def test_choose_suggestion_marks_ambiguous_match_for_review():
    result = choose_suggestion(
        "Alpha Beta",
        "ABC",
        [
            _cand("AAA", "NYSE", "Alpha Beta Holdings"),
            _cand("BBB", "NASDAQ", "Alpha Beta Group"),
        ],
    )
    assert result["confidence"] == "needs_review"


def test_find_current_config_ticker_does_not_guess_by_position():
    config = ["FCX", "PLTR", "GPW:PAS"]
    assert find_current_config_ticker("PAS1.WA", config, 2) == ("", "")


def test_build_preview_rows_marks_change_config_for_high_confidence():
    rows = build_preview_rows(
        [{"Name": "Passus SA", "Symbol": "PAS1.WA"}],
        ["GPW:PAS1"],
        search_fn=lambda _q: [_cand("PAS", "GPW", "Passus SA")],
    )
    assert rows[0]["Suggested_TV_Ticker"] == "GPW:PAS"
    assert rows[0]["Confidence"] == "high_confidence"
    assert rows[0]["Action"] == "change_config"


def test_build_preview_rows_matches_config_by_suggested_symbol():
    rows = build_preview_rows(
        [{"Name": "Passus SA", "Symbol": "PAS1.WA"}],
        ["GPW:PAS"],
        search_fn=lambda _q: [_cand("PAS", "GPW", "Passus SA")],
    )
    assert rows[0]["Current_Config_Ticker"] == "GPW:PAS"
    assert rows[0]["Config_Match_Method"] == "suggested_exact"
    assert rows[0]["Action"] == "keep"


def test_search_candidates_for_row_uses_name_and_symbol_queries():
    calls = []

    def fake_search(query):
        calls.append(query)
        if query == "PKO Bank Polski SA":
            return [_cand("FPKO", "GPW", "PKO Bank Polski SA Futures")]
        if query == "PKO":
            return [_cand("PKO", "GPW", "PKO Bank Polski SA")]
        return []

    candidates = search_candidates_for_row("PKO Bank Polski SA", "PKO.WA", fake_search)
    assert calls == ["PKO Bank Polski SA", "PKO"]
    assert [c["symbol"] for c in candidates] == ["FPKO", "PKO"]


def test_build_preview_rows_prefers_exact_symbol_over_futures_candidate():
    def fake_search(query):
        if query == "PKO Bank Polski SA":
            return [_cand("FPKO", "GPW", "PKO Bank Polski SA Futures", typ="")]
        if query == "PKO":
            return [_cand("PKO", "GPW", "PKO Bank Polski SA")]
        return []

    rows = build_preview_rows(
        [{"Name": "PKO Bank Polski SA", "Symbol": "PKO.WA"}],
        ["GPW:PKO"],
        search_fn=fake_search,
    )
    assert rows[0]["Suggested_TV_Ticker"] == "GPW:PKO"
    assert rows[0]["Action"] == "keep"


def test_choose_suggestion_penalizes_non_equity_instruments():
    result = choose_suggestion(
        "Cognor SA",
        "COGP.WA",
        [
            _cand("COG0129", "GPW", "Cognor SA FRN 15-JAN-2029", typ="bond"),
            _cand("COG", "GPW", "Cognor Holding SA"),
        ],
    )
    assert result["candidate"]["symbol"] == "COG"
