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
