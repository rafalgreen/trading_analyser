"""Lookup company names via TradingView's public symbol-search REST API.

Used as a fallback when the scraper fails to capture the company name from
the chart DOM/symbol-search modal. Results are cached on disk in
`data/.company_names_cache.json` (also negative cache, so we don't retry
unsupported tickers on every API call).
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from typing import Dict, Optional

import urllib.parse
import urllib.request


logger = logging.getLogger(__name__)

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_CACHE_PATH = os.path.join(_BASE_DIR, "data", ".company_names_cache.json")

_TV_SEARCH_URL = (
    "https://symbol-search.tradingview.com/symbol_search/v3/"
    "?text={text}&hl=1&exchange=&lang=en&search_type=undefined"
    "&domain=production&sort_by_country=US"
)

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

_REQUEST_TIMEOUT_S = 3.0
_NEGATIVE_RETRY_AFTER_S = 60 * 60 * 24  # 24h before retrying empty results

_lock = threading.Lock()
_cache: Optional[Dict[str, dict]] = None


def _load_cache() -> Dict[str, dict]:
    global _cache
    if _cache is not None:
        return _cache
    try:
        with open(_CACHE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            _cache = data
            return _cache
    except FileNotFoundError:
        pass
    except Exception as exc:  # noqa: BLE001
        logger.debug("Could not read company-name cache: %s", exc)
    _cache = {}
    return _cache


def _save_cache() -> None:
    global _cache
    if _cache is None:
        return
    try:
        os.makedirs(os.path.dirname(_CACHE_PATH), exist_ok=True)
        tmp = _CACHE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_cache, f, ensure_ascii=False, indent=2, sort_keys=True)
        os.replace(tmp, _CACHE_PATH)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Could not save company-name cache: %s", exc)


_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(s: str) -> str:
    if not s:
        return ""
    return _TAG_RE.sub("", s).strip()


def _ticker_key(ticker: str) -> str:
    return (ticker or "").strip().upper()


def _normalize_symbol(sym: str) -> str:
    if not sym:
        return ""
    s = sym.strip().upper()
    if ":" in s:
        s = s.split(":", 1)[-1]
    return s


def _fetch_raw_items(ticker: str) -> list:
    """Wykonuje request do TV symbol-search i zwraca listę dictów (lub [] przy błędzie)."""
    text = urllib.parse.quote_plus(ticker)
    url = _TV_SEARCH_URL.format(text=text)
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": _USER_AGENT,
            "Accept": "application/json, text/plain, */*",
            "Origin": "https://www.tradingview.com",
            "Referer": "https://www.tradingview.com/",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT_S) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except Exception as exc:  # noqa: BLE001
        logger.debug("TV symbol-search request failed for %s: %s", ticker, exc)
        return []

    try:
        data = json.loads(raw)
    except Exception as exc:  # noqa: BLE001
        logger.debug("TV symbol-search JSON parse failed for %s: %s", ticker, exc)
        return []

    items: list = []
    if isinstance(data, dict):
        for key in ("symbols", "symbols_remote", "items"):
            val = data.get(key)
            if isinstance(val, list):
                items.extend(val)
    elif isinstance(data, list):
        items = data
    return [it for it in items if isinstance(it, dict)]


def _fetch_from_api(ticker: str) -> str:
    items = _fetch_raw_items(ticker)
    if not items:
        return ""

    target = _ticker_key(ticker)
    best_exact = ""
    best_any = ""
    for it in items:
        sym_raw = it.get("symbol") or it.get("ticker") or ""
        sym = _normalize_symbol(_strip_html(str(sym_raw)))
        descr = _strip_html(str(it.get("description") or ""))
        if not descr:
            continue
        if sym == target and not best_exact:
            best_exact = descr
        if not best_any:
            best_any = descr
        if best_exact:
            break

    return best_exact or best_any or ""


def _matches_cache_key(ticker: str) -> str:
    return f"@matches:{_ticker_key(ticker)}"


def _search_cache_key(query: str) -> str:
    q = re.sub(r"\s+", " ", (query or "").strip().upper())
    return f"@search:{q}"


def _normalized_search_item(it: dict) -> dict:
    sym_raw = _strip_html(str(it.get("symbol") or it.get("ticker") or ""))
    sym = _normalize_symbol(sym_raw)
    exch_raw = it.get("exchange") or it.get("exchange-traded") or ""
    exch = _strip_html(str(exch_raw)).strip().upper()
    descr = _strip_html(str(it.get("description") or ""))
    typ = _strip_html(str(it.get("type") or it.get("type_disp") or ""))
    country = _strip_html(str(it.get("country") or ""))
    return {
        "symbol": sym,
        "exchange": exch,
        "description": descr,
        "type": typ,
        "country": country,
        "raw_symbol": sym_raw,
    }


def search_symbols(query: str) -> list:
    """Search TradingView symbols by free-text query (usually company name).

    Returns normalized dicts:
    ``symbol``, ``exchange``, ``description``, ``type``, ``country``,
    ``raw_symbol``. Results are cached under ``@search:<QUERY>`` and this
    function never raises.
    """
    q = re.sub(r"\s+", " ", (query or "").strip())
    if not q:
        return []

    cache_key = _search_cache_key(q)
    with _lock:
        cache = _load_cache()
        entry = cache.get(cache_key)
        now = time.time()
        cached = None
        if isinstance(entry, dict):
            ts = float(entry.get("ts") or 0.0)
            items = entry.get("items")
            if isinstance(items, list):
                if items or (now - ts) < _NEGATIVE_RETRY_AFTER_S:
                    cached = items

    if cached is None:
        raw_items = _fetch_raw_items(q)
        items = [
            item for item in (_normalized_search_item(it) for it in raw_items)
            if item.get("symbol") and item.get("exchange")
        ]
        with _lock:
            cache = _load_cache()
            cache[cache_key] = {"items": items, "ts": time.time()}
            _save_cache()
    else:
        items = cached

    if items:
        logger.debug("search_symbols(%r) -> %d item(s)", q, len(items))
    return list(items)


def _fetch_matches(ticker: str) -> list:
    """Pobiera surowe matche z TV REST i normalizuje do {symbol, exchange, description}."""
    items = _fetch_raw_items(ticker)
    target = _ticker_key(ticker)
    out: list = []
    for it in items:
        sym_raw = it.get("symbol") or it.get("ticker") or ""
        sym = _normalize_symbol(_strip_html(str(sym_raw)))
        if sym != target:
            continue
        exch_raw = it.get("exchange") or it.get("exchange-traded") or ""
        exch = _strip_html(str(exch_raw)).strip().upper()
        descr = _strip_html(str(it.get("description") or ""))
        if not exch:
            continue
        out.append(
            {
                "symbol": f"{exch}:{sym}",
                "exchange": exch,
                "description": descr,
            }
        )
    return out


def lookup_symbol_match(ticker: str, exchanges) -> list:
    """Zwraca listę dopasowań dla ``ticker`` ograniczoną do giełd z ``exchanges``.

    Każdy element: ``{"symbol": "GPW:SHO", "exchange": "GPW", "description": "Shoper SA"}``.
    Wynik jest cache'owany w ``data/.company_names_cache.json`` pod kluczem
    ``@matches:<TICKER>`` (osobno od cache nazw firm). Cicho zwraca ``[]`` przy
    każdym błędzie sieci/parsowania.
    """
    key = _ticker_key(ticker)
    if not key:
        return []
    allowed = {str(e).strip().upper() for e in (exchanges or []) if str(e).strip()}
    if not allowed:
        return []

    cache_key = _matches_cache_key(key)
    with _lock:
        cache = _load_cache()
        entry = cache.get(cache_key)
        now = time.time()
        cached_matches = None
        if isinstance(entry, dict):
            ts = float(entry.get("ts") or 0.0)
            cm = entry.get("matches")
            if isinstance(cm, list):
                if cm or (now - ts) < _NEGATIVE_RETRY_AFTER_S:
                    cached_matches = cm

    if cached_matches is None:
        matches = _fetch_matches(key)
        with _lock:
            cache = _load_cache()
            cache[cache_key] = {"matches": matches, "ts": time.time()}
            _save_cache()
    else:
        matches = cached_matches

    filtered = [
        m for m in matches
        if isinstance(m, dict) and str(m.get("exchange") or "").upper() in allowed
    ]
    if filtered:
        logger.debug(
            "lookup_symbol_match(%s, %s) -> %d match(es)",
            key,
            sorted(allowed),
            len(filtered),
        )
    return filtered


def lookup_company_name(ticker: str) -> str:
    """Return company name for `ticker` via TV REST (with cache).

    Returns "" on any failure — never raises.
    """

    key = _ticker_key(ticker)
    if not key:
        return ""

    with _lock:
        cache = _load_cache()
        entry = cache.get(key)
        now = time.time()
        if isinstance(entry, dict):
            name = str(entry.get("name") or "")
            ts = float(entry.get("ts") or 0.0)
            if name:
                return name
            if (now - ts) < _NEGATIVE_RETRY_AFTER_S:
                return ""

    name = _fetch_from_api(key)

    with _lock:
        cache = _load_cache()
        cache[key] = {"name": name, "ts": time.time()}
        _save_cache()

    if name:
        logger.debug("Resolved %s via TV REST -> %r", key, name)
    else:
        logger.debug("No TV REST match for %s (cached negative)", key)
    return name


def lookup_exchange(ticker: str) -> str:
    """Zwraca podstawową giełdę (np. ``"NYSE"``) dla `ticker` z TV REST.

    Reużywa cache pod kluczem ``@matches:<TICKER>`` (ten sam co
    :func:`lookup_symbol_match`), żeby nie generować duplikatu requestów.
    Zwraca pierwszy match z niepustym ``exchange`` (TV typowo zwraca w kolejności
    od najbardziej trafnego), albo ``""`` przy każdym błędzie / braku matchy.
    """
    key = _ticker_key(ticker)
    if not key:
        return ""

    cache_key = _matches_cache_key(key)
    with _lock:
        cache = _load_cache()
        entry = cache.get(cache_key)
        now = time.time()
        cached_matches = None
        if isinstance(entry, dict):
            ts = float(entry.get("ts") or 0.0)
            cm = entry.get("matches")
            if isinstance(cm, list):
                if cm or (now - ts) < _NEGATIVE_RETRY_AFTER_S:
                    cached_matches = cm

    if cached_matches is None:
        matches = _fetch_matches(key)
        with _lock:
            cache = _load_cache()
            cache[cache_key] = {"matches": matches, "ts": time.time()}
            _save_cache()
    else:
        matches = cached_matches

    for m in matches:
        if not isinstance(m, dict):
            continue
        exch = str(m.get("exchange") or "").strip().upper()
        if exch:
            logger.debug("lookup_exchange(%s) -> %s", key, exch)
            return exch
    return ""


def clear_cache() -> None:
    """Test helper — drop in-memory cache (file is left untouched)."""
    global _cache
    with _lock:
        _cache = None
