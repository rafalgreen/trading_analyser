"""Tests for the TV symbol-search REST fallback (`company_names.lookup_company_name`)."""

from __future__ import annotations

import json
from unittest.mock import patch


def _fake_response(payload):
    body = json.dumps(payload).encode("utf-8")

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return body

    return _Resp()


def _isolated_company_names(tmp_path, monkeypatch):
    """Redirect company_names cache file into tmp_path and return fresh module."""
    import importlib
    import company_names as cn
    importlib.reload(cn)
    monkeypatch.setattr(cn, "_CACHE_PATH", str(tmp_path / ".company_names_cache.json"))
    cn.clear_cache()
    return cn


def test_lookup_company_name_reads_description(tmp_path, monkeypatch):
    cn = _isolated_company_names(tmp_path, monkeypatch)
    payload = {
        "symbols": [
            {"symbol": "PLTR", "description": "Palantir Technologies Inc."},
            {"symbol": "PLTR.OTHER", "description": "Some other thing"},
        ]
    }
    with patch.object(
        cn.urllib.request,
        "urlopen",
        return_value=_fake_response(payload),
    ) as mocked:
        out = cn.lookup_company_name("PLTR")
        assert out == "Palantir Technologies Inc."
        assert mocked.call_count == 1


def test_lookup_company_name_strips_html_highlights(tmp_path, monkeypatch):
    cn = _isolated_company_names(tmp_path, monkeypatch)
    payload = {
        "symbols": [
            {
                "symbol": "<em>NKE</em>",
                "description": "<em>NIKE</em>, Inc. Class B",
            }
        ]
    }
    with patch.object(
        cn.urllib.request, "urlopen", return_value=_fake_response(payload)
    ):
        assert cn.lookup_company_name("NKE") == "NIKE, Inc. Class B"


def test_lookup_company_name_caches_positive_result(tmp_path, monkeypatch):
    cn = _isolated_company_names(tmp_path, monkeypatch)
    payload = {"symbols": [{"symbol": "NKE", "description": "Nike, Inc."}]}

    with patch.object(
        cn.urllib.request, "urlopen", return_value=_fake_response(payload)
    ) as mocked:
        first = cn.lookup_company_name("NKE")
    assert first == "Nike, Inc."
    assert mocked.call_count == 1

    # Second call: must not hit the network at all (cached in-memory).
    with patch.object(cn.urllib.request, "urlopen") as mocked2:
        second = cn.lookup_company_name("NKE")
    assert second == "Nike, Inc."
    mocked2.assert_not_called()


def test_lookup_company_name_caches_negative_result(tmp_path, monkeypatch):
    cn = _isolated_company_names(tmp_path, monkeypatch)
    payload = {"symbols": []}

    with patch.object(
        cn.urllib.request, "urlopen", return_value=_fake_response(payload)
    ) as mocked:
        out = cn.lookup_company_name("ZZZZZ")
    assert out == ""
    assert mocked.call_count == 1

    # Second call: still empty, but no second network hit (within retry window).
    with patch.object(cn.urllib.request, "urlopen") as mocked2:
        out2 = cn.lookup_company_name("ZZZZZ")
    assert out2 == ""
    mocked2.assert_not_called()


def test_lookup_company_name_network_error_returns_empty(tmp_path, monkeypatch):
    cn = _isolated_company_names(tmp_path, monkeypatch)

    def _boom(*a, **kw):
        raise RuntimeError("boom")

    with patch.object(cn.urllib.request, "urlopen", side_effect=_boom):
        assert cn.lookup_company_name("AAPL") == ""


def test_lookup_company_name_empty_ticker_short_circuits(tmp_path, monkeypatch):
    cn = _isolated_company_names(tmp_path, monkeypatch)
    with patch.object(cn.urllib.request, "urlopen") as mocked:
        assert cn.lookup_company_name("") == ""
        assert cn.lookup_company_name("   ") == ""
    mocked.assert_not_called()


def test_lookup_company_name_prefers_exact_ticker_match(tmp_path, monkeypatch):
    cn = _isolated_company_names(tmp_path, monkeypatch)
    payload = {
        "symbols": [
            {"symbol": "AAPL.SOMETHING", "description": "Wrong One"},
            {"symbol": "AAPL", "description": "Apple Inc."},
        ]
    }
    with patch.object(
        cn.urllib.request, "urlopen", return_value=_fake_response(payload)
    ):
        assert cn.lookup_company_name("AAPL") == "Apple Inc."


def test_lookup_symbol_match_filters_by_exchange(tmp_path, monkeypatch):
    cn = _isolated_company_names(tmp_path, monkeypatch)
    payload = {
        "symbols": [
            {"symbol": "AMB", "exchange": "MIL", "description": "Ambromobiliare S.p.A."},
            {"symbol": "AMB", "exchange": "CME", "description": "BTIC on Micro BTC"},
            {"symbol": "AMB", "exchange": "GPW", "description": "Ambra S.A."},
        ]
    }
    with patch.object(
        cn.urllib.request, "urlopen", return_value=_fake_response(payload)
    ):
        matches = cn.lookup_symbol_match("AMB", ["GPW"])

    assert len(matches) == 1
    assert matches[0]["symbol"] == "GPW:AMB"
    assert matches[0]["exchange"] == "GPW"
    assert matches[0]["description"] == "Ambra S.A."


def test_lookup_symbol_match_no_match_returns_empty_list(tmp_path, monkeypatch):
    cn = _isolated_company_names(tmp_path, monkeypatch)
    payload = {
        "symbols": [
            {"symbol": "FOO", "exchange": "NASDAQ", "description": "Foo Inc."},
        ]
    }
    with patch.object(
        cn.urllib.request, "urlopen", return_value=_fake_response(payload)
    ):
        assert cn.lookup_symbol_match("FOO", ["GPW"]) == []


def test_lookup_symbol_match_strips_html_in_symbol(tmp_path, monkeypatch):
    cn = _isolated_company_names(tmp_path, monkeypatch)
    payload = {
        "symbols": [
            {
                "symbol": "<em>SHO</em>",
                "exchange": "GPW",
                "description": "<em>Shoper</em> SA",
            }
        ]
    }
    with patch.object(
        cn.urllib.request, "urlopen", return_value=_fake_response(payload)
    ):
        matches = cn.lookup_symbol_match("SHO", ["GPW"])

    assert matches == [
        {"symbol": "GPW:SHO", "exchange": "GPW", "description": "Shoper SA"}
    ]


def test_lookup_symbol_match_caches_result(tmp_path, monkeypatch):
    cn = _isolated_company_names(tmp_path, monkeypatch)
    payload = {
        "symbols": [
            {"symbol": "ATC", "exchange": "GPW", "description": "Arctic Paper SA"},
        ]
    }
    with patch.object(
        cn.urllib.request, "urlopen", return_value=_fake_response(payload)
    ) as mocked:
        first = cn.lookup_symbol_match("ATC", ["GPW"])
    assert first[0]["symbol"] == "GPW:ATC"
    assert mocked.call_count == 1

    with patch.object(cn.urllib.request, "urlopen") as mocked2:
        second = cn.lookup_symbol_match("ATC", ["GPW"])
    assert second[0]["symbol"] == "GPW:ATC"
    mocked2.assert_not_called()


def test_fetch_symbol_matches_returns_all_exchanges(tmp_path, monkeypatch):
    cn = _isolated_company_names(tmp_path, monkeypatch)
    payload = {
        "symbols": [
            {"symbol": "601088", "exchange": "SSE", "description": "China Railway"},
            {"symbol": "601088", "exchange": "GPW", "description": "Wrong"},
        ]
    }
    with patch.object(
        cn.urllib.request, "urlopen", return_value=_fake_response(payload)
    ):
        matches = cn.fetch_symbol_matches("601088")

    assert len(matches) == 2
    assert matches[0]["symbol"] == "SSE:601088"
    assert matches[1]["symbol"] == "GPW:601088"


def test_fetch_symbol_matches_caches_with_lookup_symbol_match(tmp_path, monkeypatch):
    cn = _isolated_company_names(tmp_path, monkeypatch)
    payload = {
        "symbols": [
            {"symbol": "ATC", "exchange": "GPW", "description": "Arctic Paper SA"},
        ]
    }
    with patch.object(
        cn.urllib.request, "urlopen", return_value=_fake_response(payload)
    ) as mocked:
        cn.lookup_symbol_match("ATC", ["GPW"])
    assert mocked.call_count == 1

    with patch.object(cn.urllib.request, "urlopen") as mocked2:
        all_m = cn.fetch_symbol_matches("ATC")
    assert all_m[0]["symbol"] == "GPW:ATC"
    mocked2.assert_not_called()


def test_lookup_symbol_match_empty_exchanges_returns_empty(tmp_path, monkeypatch):
    cn = _isolated_company_names(tmp_path, monkeypatch)
    with patch.object(cn.urllib.request, "urlopen") as mocked:
        assert cn.lookup_symbol_match("AAPL", []) == []
        assert cn.lookup_symbol_match("AAPL", ["", "  "]) == []
    mocked.assert_not_called()


# --- lookup_exchange ------------------------------------------------------

def test_lookup_exchange_returns_first_exchange(tmp_path, monkeypatch):
    cn = _isolated_company_names(tmp_path, monkeypatch)
    payload = {
        "symbols": [
            {"symbol": "ZIM", "exchange": "NYSE", "description": "ZIM Integrated"},
            {"symbol": "ZIM", "exchange": "GETTEX", "description": "Zimmer"},
        ]
    }
    with patch.object(
        cn.urllib.request, "urlopen", return_value=_fake_response(payload)
    ):
        assert cn.lookup_exchange("ZIM") == "NYSE"


def test_lookup_exchange_no_match_returns_empty(tmp_path, monkeypatch):
    cn = _isolated_company_names(tmp_path, monkeypatch)
    payload = {"symbols": []}
    with patch.object(
        cn.urllib.request, "urlopen", return_value=_fake_response(payload)
    ):
        assert cn.lookup_exchange("NOPE") == ""


def test_lookup_exchange_uses_matches_cache_with_lookup_symbol_match(
    tmp_path, monkeypatch
):
    """Cache współdzielony — lookup_symbol_match wcześniej zapełnia cache,
    drugie lookup_exchange() nie woła sieci."""
    cn = _isolated_company_names(tmp_path, monkeypatch)
    payload = {
        "symbols": [
            {"symbol": "ATC", "exchange": "GPW", "description": "Arctic Paper SA"},
        ]
    }
    with patch.object(
        cn.urllib.request, "urlopen", return_value=_fake_response(payload)
    ) as mocked:
        cn.lookup_symbol_match("ATC", ["GPW"])
    assert mocked.call_count == 1

    with patch.object(cn.urllib.request, "urlopen") as mocked2:
        assert cn.lookup_exchange("ATC") == "GPW"
    mocked2.assert_not_called()


def test_lookup_exchange_handles_network_error(tmp_path, monkeypatch):
    cn = _isolated_company_names(tmp_path, monkeypatch)

    def _boom(*a, **kw):
        raise OSError("network down")

    with patch.object(cn.urllib.request, "urlopen", side_effect=_boom):
        assert cn.lookup_exchange("ZIM") == ""


# --- search_symbols -------------------------------------------------------

def test_search_symbols_normalizes_and_caches_free_text_query(tmp_path, monkeypatch):
    cn = _isolated_company_names(tmp_path, monkeypatch)
    payload = {
        "symbols": [
            {
                "symbol": "<em>PAS</em>",
                "exchange": "GPW",
                "description": "<em>Passus</em> SA",
                "type": "stock",
                "country": "Poland",
            }
        ]
    }
    with patch.object(
        cn.urllib.request, "urlopen", return_value=_fake_response(payload)
    ) as mocked:
        rows = cn.search_symbols("Passus SA")
    assert rows == [
        {
            "symbol": "PAS",
            "exchange": "GPW",
            "description": "Passus SA",
            "type": "stock",
            "country": "Poland",
            "raw_symbol": "PAS",
        }
    ]
    assert mocked.call_count == 1

    with patch.object(cn.urllib.request, "urlopen") as mocked2:
        assert cn.search_symbols("Passus SA")[0]["symbol"] == "PAS"
    mocked2.assert_not_called()


def test_search_symbols_empty_query_short_circuits(tmp_path, monkeypatch):
    cn = _isolated_company_names(tmp_path, monkeypatch)
    with patch.object(cn.urllib.request, "urlopen") as mocked:
        assert cn.search_symbols("") == []
        assert cn.search_symbols("   ") == []
    mocked.assert_not_called()
