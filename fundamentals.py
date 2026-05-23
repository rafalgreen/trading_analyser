"""Pobieranie wskaźników fundamentalnych z yfinance + opcjonalny fallback TradingView.

Moduł dostarcza:

* ``is_crypto(ticker)`` – heurystyka rozpoznająca krypto (które nie ma fundamentów).
* ``to_yahoo_symbol(ticker)`` – mapowanie nazw używanych w configu (``GPW:TXT``,
  ``NASDAQ:AAPL``, ``AAPL``) na symbole Yahoo Finance (``TXT.WA``, ``AAPL``).
  Krypto zwraca ``None``.
* ``fetch_fundamentals(ticker, *, tv_fallback_page=None, cache_path, ttl_hours=24)``
  – główny entrypoint. Najpierw sprawdza JSON cache (TTL), potem yfinance,
  a w razie braku danych dla GPW – opcjonalnie ``_tv_financials_fallback`` na
  podanej stronie Playwright.

Cache jest plikiem JSON (dict ``{TICKER: {...row, "_cached_at": float}}``),
trzymany dla uproszczenia w pamięci procesu jako jeden dict – API publiczne to
``fetch_fundamentals``.  Funkcja zawsze zwraca słownik z pełnym kompletem
kluczy ``Fund_*`` (``None`` gdy brak wartości) plus ``Fund_Source`` oraz
``Fund_Updated_At`` (ISO 8601 UTC).
"""

from __future__ import annotations

import contextlib
import json
import logging
import math
import os
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


logger = logging.getLogger(__name__)

_YF_IMPORT_ERROR: Optional[str] = None


FUND_KEYS = (
    "Fund_PE",
    "Fund_PB",
    "Fund_EV_EBITDA",
    "Fund_ROE",
    "Fund_NetMargin",
    "Fund_DE",
    "Fund_FCF",
    "Fund_DividendYield",
    "Fund_DividendRate",
)

_YF_FIELD_MAP = {
    "Fund_PE": "trailingPE",
    "Fund_PB": "priceToBook",
    "Fund_EV_EBITDA": "enterpriseToEbitda",
    "Fund_ROE": "returnOnEquity",
    "Fund_NetMargin": "profitMargins",
    "Fund_DE": "debtToEquity",
    "Fund_FCF": "freeCashflow",
    "Fund_DividendYield": "dividendYield",
    "Fund_DividendRate": "dividendRate",
}

_YF_DIVIDEND_RATE_FALLBACK = "trailingAnnualDividendRate"

_TV_ROW_LABELS = {
    "Fund_PE": ("price to earnings ratio", "p/e ratio", "price-to-earnings"),
    "Fund_PB": ("price to book ratio", "p/b ratio", "price-to-book"),
    "Fund_EV_EBITDA": ("enterprise value to ebitda", "ev/ebitda"),
    "Fund_ROE": ("return on equity",),
    "Fund_NetMargin": ("net margin", "net profit margin"),
    "Fund_DE": ("debt to equity ratio", "debt-to-equity"),
    "Fund_FCF": ("free cash flow",),
    "Fund_DividendYield": ("dividend yield",),
    "Fund_DividendRate": (
        "dividend per share",
        "dividend rate",
        "annual dividend",
        "trailing annual dividend",
    ),
}

# Granice sensownych wartości (poza zakresem → traktujemy jak brak danych).
_FUND_SANITY_BOUNDS: Dict[str, Tuple[Optional[float], Optional[float]]] = {
    "Fund_PE": (0.0, 1000.0),
    "Fund_PB": (0.0, 100.0),
    "Fund_EV_EBITDA": (0.0, 500.0),
    "Fund_ROE": (-5.0, 5.0),
    "Fund_NetMargin": (-1.0, 1.0),
    "Fund_DE": (0.0, 10_000.0),
    "Fund_DividendYield": (0.0, 1.0),
    "Fund_DividendRate": (0.0, 10_000.0),
}

_NUMBER_TOKEN_RE = re.compile(
    r"[-+]?"
    r"(?:\d{1,3}(?:[ \u00a0.,'\u2019]\d{3})+|\d+)"
    r"(?:[.,]\d+)?"
    r"%?"
)


_CRYPTO_PREFIXES = ("BINANCE:", "BITFINEX:", "COINBASE:", "KRAKEN:", "BYBIT:")
_CRYPTO_SUFFIXES = ("USDT", "USD", "USDC", "BUSD")
# Lista znanych tickerów krypto które bez sufiksu mogą się jednak pojawić
# (np. ``BTC``, ``ETH`` jako proste skróty). Trzymamy minimalny set bo
# konfiguracja zwykle używa wariantów ``BTCUSDT``.
_CRYPTO_BASE_TOKENS = frozenset({"BTC", "ETH", "SOL", "XRP", "DOGE", "ADA"})

_US_EXCHANGES = frozenset(
    {"NASDAQ", "NYSE", "AMEX", "BATS", "ARCA", "OTC", "OTCQX", "OTCQB", "NCM", "NGM", "NMS"}
)

_EXCHANGE_YAHOO_SUFFIX: Dict[str, str] = {
    "GPW": ".WA",
    "XETR": ".DE",
    "FWB": ".F",
    "GETTEX": ".F",
    "BER": ".BE",
    "DUS": ".DU",
    "HAM": ".HM",
    "SWB": ".SG",
    "KRX": ".KS",
    "KOSDAQ": ".KQ",
    "HKEX": ".HK",
    "HK": ".HK",
    "SSE": ".SS",
    "SZSE": ".SZ",
    "SGX": ".SI",
    "LSE": ".L",
    "TSX": ".TO",
    "TSXV": ".V",
    "ASX": ".AX",
    "SIX": ".SW",
    "MIL": ".MI",
    "BME": ".MC",
    "EPA": ".PA",
    "PAR": ".PA",
    "AMS": ".AS",
    "BRU": ".BR",
    "LIS": ".LS",
}

_yfinance_logging_configured = False


def is_crypto(ticker: str) -> bool:
    """Czy ticker jest krypto (brak fundamentów)?

    Zasady:
      * prefiks giełdy krypto (BINANCE:, BITFINEX:, COINBASE:, …) → True.
      * sufiks ``USDT``/``USD``/``USDC`` (np. ``BTCUSDT``) → True, ale tylko jeśli
        to nie jest klasyczna para FX typu ``EURPLN``/``USDPLN`` (sufiks USD ma
        tylko 3 znaki w parach fx, więc ``EURUSD`` byłoby uznane za krypto – w
        praktyce w tej aplikacji niewielki zbiór par fx jest oznaczany inaczej
        i nigdy nie trafia jako 6-znakowy ``XXXUSD`` na yahoo).
      * krótkie tokeny BTC/ETH/... bez giełdy → True.

    Wszystko inne (zwłaszcza ``GPW:TXT``, ``AAPL``) → False.
    """
    if not ticker:
        return False
    t = str(ticker).strip().upper()
    if not t:
        return False
    for pref in _CRYPTO_PREFIXES:
        if t.startswith(pref):
            return True
    # Z giełdy NIE-krypto (NASDAQ:, NYSE:, GPW:, SSE:, SGX:, …) — to nie krypto,
    # niezależnie od sufiksu.
    if ":" in t:
        return False
    if t in _CRYPTO_BASE_TOKENS:
        return True
    for suf in _CRYPTO_SUFFIXES:
        if t.endswith(suf) and len(t) > len(suf):
            base = t[: -len(suf)]
            # ``EURPLN`` / ``USDPLN`` to pary FX (nie krypto), ale tu mamy ``USDT``
            # więc jeśli sufiks to USDT/USDC/BUSD — bezpieczne klasyfikujemy jako
            # krypto. Dla samego ``USD`` wymagamy by baza wyglądała na coin
            # (alfa-numeryczna, >=3 znaki). To wystarcza na ``BTCUSD`` /
            # ``ETHUSD`` i nie łapie typowych par fx.
            if suf == "USD":
                # ``EURPLN`` nie pasuje (nie kończy się na USD). ``EURUSD``
                # ma 6 znaków: traktujemy jako krypto/forex – pas, ale w configu
                # tej aplikacji par fx nie ma, więc nie eskalujemy.
                return base.isalnum()
            return True
    return False


def configure_yfinance_logging() -> None:
    global _yfinance_logging_configured
    if _yfinance_logging_configured:
        return
    logging.getLogger("yfinance").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    _yfinance_logging_configured = True


@contextlib.contextmanager
def _suppress_yfinance_stderr():
    """yfinance drukuje HTTP 404 bezpośrednio na stderr — tłumimy to przy pobieraniu."""
    configure_yfinance_logging()
    with open(os.devnull, "w", encoding="utf-8") as devnull:
        with contextlib.redirect_stderr(devnull):
            yield


def _exchange_to_yahoo_suffix(exchange: str) -> Optional[str]:
    exch = str(exchange or "").strip().upper()
    if not exch:
        return None
    if "AMSTERDAM" in exch:
        return ".AS"
    if "PARIS" in exch:
        return ".PA"
    if "BRUSSELS" in exch or "EURONEXT BR" in exch:
        return ".BR"
    if "LISBON" in exch:
        return ".LS"
    if exch.startswith("EURONEXT"):
        if "AMSTERDAM" in exch:
            return ".AS"
        if "PARIS" in exch:
            return ".PA"
        if "BRUSSELS" in exch:
            return ".BR"
        return ".PA"
    return _EXCHANGE_YAHOO_SUFFIX.get(exch)


def _yahoo_symbol_with_suffix(symbol: str, suffix: str) -> str:
    sym = str(symbol or "").strip().upper()
    if not sym or not suffix:
        return sym
    if sym.endswith(suffix):
        return sym
    return f"{sym}{suffix}"


def _bare_ticker_yahoo_suffix(symbol: str) -> Optional[str]:
    sym = str(symbol or "").strip().upper()
    if re.fullmatch(r"\d{6}", sym):
        return ".KS"
    if re.fullmatch(r"\d{4}", sym):
        return ".HK"
    return None


def _lookup_yahoo_suffix(symbol: str) -> Optional[str]:
    try:
        from company_names import lookup_exchange

        exch = lookup_exchange(symbol)
    except Exception:  # noqa: BLE001
        return None
    return _exchange_to_yahoo_suffix(exch)


def to_yahoo_symbol(ticker: str) -> Optional[str]:
    """Mapowanie tickerów configu na symbole Yahoo Finance.

    * Krypto → ``None`` (nie pobieramy fundamentów).
    * ``GPW:XXX`` → ``XXX.WA``.
    * ``NASDAQ:XXX`` / ``NYSE:XXX`` / ``BATS:XXX`` → ``XXX``.
    * ``XETR:XXX`` / ``KRX:XXX`` / ``HKEX:XXX`` / … → ``XXX`` + sufiks giełdy Yahoo.
    * Plain ``XXX`` → heurystyki (``000660`` → ``000660.KS``) lub lookup giełdy z TV REST.
    """
    if is_crypto(ticker):
        return None
    if not ticker:
        return None
    t = str(ticker).strip()
    if not t:
        return None
    if ":" in t:
        exch, sym = t.split(":", 1)
        exch_u = exch.strip().upper()
        sym = sym.strip()
        if not sym:
            return None
        if exch_u in _US_EXCHANGES:
            return sym.upper()
        suffix = _EXCHANGE_YAHOO_SUFFIX.get(exch_u) or _exchange_to_yahoo_suffix(exch_u)
        if suffix:
            return _yahoo_symbol_with_suffix(sym, suffix)
        return sym.upper()
    sym_u = t.upper()
    bare_suffix = _bare_ticker_yahoo_suffix(sym_u)
    if bare_suffix:
        return _yahoo_symbol_with_suffix(sym_u, bare_suffix)
    looked_up = _lookup_yahoo_suffix(sym_u)
    if looked_up:
        return _yahoo_symbol_with_suffix(sym_u, looked_up)
    return sym_u


def _yahoo_symbol_candidates(ticker: str) -> List[str]:
    """Kolejność prób symboli Yahoo (primary + sensowne fallbacki)."""
    primary = to_yahoo_symbol(ticker)
    out: List[str] = []
    if primary:
        out.append(primary)
    sym_u = str(ticker or "").strip().upper()
    if ":" in sym_u:
        sym_u = sym_u.split(":", 1)[1].strip().upper()
    if not sym_u:
        return out
    bare_suffix = _bare_ticker_yahoo_suffix(sym_u)
    if bare_suffix:
        cand = _yahoo_symbol_with_suffix(sym_u, bare_suffix)
        if cand not in out:
            out.append(cand)
    if primary and primary.endswith(".DE"):
        alt = primary[:-3] + ".F"
        if alt not in out:
            out.append(alt)
    return out


# ---------------------------------------------------------------------------
# Cache (JSON disk)
# ---------------------------------------------------------------------------

_cache_lock = threading.Lock()


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read_cache(cache_path: Path) -> Dict[str, Any]:
    if not cache_path or not os.path.exists(cache_path):
        return {}
    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except Exception as exc:  # noqa: BLE001
        logger.debug("Nie można odczytać cache fundamentów %s: %s", cache_path, exc)
    return {}


def _write_cache(cache_path: Path, data: Dict[str, Any]) -> None:
    try:
        cache_path = Path(cache_path)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = cache_path.with_suffix(cache_path.suffix + ".tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, cache_path)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Nie można zapisać cache fundamentów %s: %s", cache_path, exc)


# ---------------------------------------------------------------------------
# yfinance + TV fallback
# ---------------------------------------------------------------------------


def _normalize_numeric_token(token: str) -> Optional[float]:
    """Parsuje pojedynczy token liczbowy (PL/US format, opcjonalnie %)."""
    if not token:
        return None
    s = token.strip().replace("\u00a0", " ")
    pct = s.endswith("%")
    if pct:
        s = s[:-1].strip()
    s = s.replace(" ", "")
    if "," in s and "." in s:
        s = s.replace(",", "")
    elif "," in s and s.count(",") == 1 and s.rfind(",") >= len(s) - 4:
        s = s.replace(",", ".")
    try:
        v = float(s)
    except ValueError:
        return None
    if pct:
        v = v / 100.0
    if math.isnan(v) or math.isinf(v):
        return None
    return v


def _coerce_number(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
            return None
        return float(value)
    s = str(value).strip()
    if not s or s.lower() in ("none", "nan", "-", "—", "n/a"):
        return None
    # Nie sklejaj wielu liczb (np. lata osi wykresu TV: „2013 2014 …”).
    match = _NUMBER_TOKEN_RE.search(s.replace("\u00a0", " "))
    if not match:
        return None
    return _normalize_numeric_token(match.group(0))


def _normalize_dividend_yield(value: Optional[float]) -> Optional[float]:
    """yfinance zwraca ułamek (0.025 = 2.5%); czasem procent (2.5)."""
    if value is None:
        return None
    if value > 1.0:
        value = value / 100.0
    return value


def _is_sane_fund_value(fund_key: str, value: Optional[float]) -> bool:
    if value is None:
        return False
    bounds = _FUND_SANITY_BOUNDS.get(fund_key)
    if bounds is None:
        return True
    lo, hi = bounds
    if lo is not None and value <= lo:
        return False
    if hi is not None and value > hi:
        return False
    return True


def _sanitize_fund_values(
    values: Optional[Dict[str, Optional[float]]],
) -> Dict[str, Optional[float]]:
    out: Dict[str, Optional[float]] = {k: None for k in FUND_KEYS}
    if not values:
        return out
    for key in FUND_KEYS:
        raw = values.get(key)
        coerced = _coerce_number(raw) if not isinstance(raw, (int, float)) else raw
        if isinstance(coerced, float) and (math.isnan(coerced) or math.isinf(coerced)):
            coerced = None
        if _is_sane_fund_value(key, coerced):
            out[key] = coerced
    return out


def check_yfinance_available() -> Tuple[bool, Optional[str]]:
    """Sprawdza czy pakiet yfinance da się zaimportować.

    Zwraca ``(True, None)`` albo ``(False, komunikat_błędu)``.
    """
    global _YF_IMPORT_ERROR
    try:
        import yfinance  # type: ignore  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        _YF_IMPORT_ERROR = str(exc)
        return False, _YF_IMPORT_ERROR
    _YF_IMPORT_ERROR = None
    return True, None


def fundamentals_row_has_values(data: Optional[Dict[str, Any]]) -> bool:
    """Czy wiersz fundamentów ma choć jedną sensowną wartość wskaźnika."""
    if not data:
        return False
    source = str(data.get("Fund_Source") or "").strip().lower()
    if source and source not in ("none", "n/a"):
        sanitized = _sanitize_fund_values({k: data.get(k) for k in FUND_KEYS})
        if any(v is not None for v in sanitized.values()):
            return True
    return any(
        _is_sane_fund_value(k, _coerce_number(data.get(k))) for k in FUND_KEYS
    )


def fundamentals_fetch_attempted(data: Optional[Dict[str, Any]]) -> bool:
    """True gdy ticker był już pobierany (nawet bez sensownych wartości)."""
    if not data:
        return False
    updated = str(data.get("Fund_Updated_At") or "").strip()
    if updated:
        return True
    source = str(data.get("Fund_Source") or "").strip().lower()
    return source in ("none", "yfinance", "tradingview", "n/a")


def _yf_fetch(symbol: str) -> Optional[Dict[str, Optional[float]]]:
    """Czyta ``yfinance.Ticker(symbol).info`` i mapuje pola na ``Fund_*``.

    Zwraca ``None`` gdy yfinance nie zwrócił żadnego sensownego ``info``
    (np. import się nie udał, sieci nie ma).  Pojedyncze brakujące pola są
    tolerowane jako ``None``.
    """
    global _YF_IMPORT_ERROR
    if not symbol:
        return None
    try:
        import yfinance  # type: ignore
    except Exception as exc:  # noqa: BLE001
        _YF_IMPORT_ERROR = str(exc)
        logger.warning("yfinance niezainstalowany / nieczynny: %s", exc)
        return None
    _YF_IMPORT_ERROR = None

    try:
        with _suppress_yfinance_stderr():
            tkr = yfinance.Ticker(symbol)
            info = getattr(tkr, "info", None)
    except Exception as exc:  # noqa: BLE001
        logger.debug("yfinance Ticker(%s).info zawiódł: %s", symbol, exc)
        return None

    if not isinstance(info, dict) or len(info) <= 1:
        logger.debug("yfinance Ticker(%s) bez danych (404 lub pusty info)", symbol)
        return None

    out: Dict[str, Optional[float]] = {}
    for fund_key, yf_field in _YF_FIELD_MAP.items():
        out[fund_key] = _coerce_number(info.get(yf_field))
    if out.get("Fund_DividendRate") is None:
        out["Fund_DividendRate"] = _coerce_number(info.get(_YF_DIVIDEND_RATE_FALLBACK))
    out["Fund_DividendYield"] = _normalize_dividend_yield(out.get("Fund_DividendYield"))
    sanitized = _sanitize_fund_values(out)
    if not any(v is not None for v in sanitized.values()):
        return None
    return sanitized


def _yf_fetch_best(ticker: str) -> Optional[Dict[str, Optional[float]]]:
    for symbol in _yahoo_symbol_candidates(ticker):
        data = _yf_fetch(symbol)
        if data and any(v is not None for v in data.values()):
            return data
    return None


def _tv_financials_url(ticker: str) -> Optional[str]:
    t = str(ticker or "").strip()
    if ":" not in t:
        return None
    exch, sym = t.split(":", 1)
    exch = exch.strip().upper()
    sym = sym.strip().upper()
    if not exch or not sym:
        return None
    return (
        f"https://www.tradingview.com/symbols/{exch}-{sym}"
        "/financials-statistics-and-ratios/"
    )


def _tv_element_in_chart(el: Any) -> bool:
    """Czy element należy do kontenera wykresu (oś lat itp.)?"""
    try:
        for node in [el, *el.parents]:
            classes = " ".join(node.get("class") or []).lower()
            if "chart" in classes:
                return True
    except Exception:
        return False
    return False


def _tv_extract_value_for_label(el: Any, needle: str, text: str) -> Optional[float]:
    """Wyciąga wartość wskaźnika z wiersza HTML TradingView."""
    try:
        row = el if el.name == "tr" else el.find_parent("tr")
        if row is not None:
            cells = row.find_all(["td", "th"], recursive=False)
            if len(cells) >= 2:
                label_cell = None
                value_cell = None
                for cell in cells:
                    cell_text = (cell.get_text(" ", strip=True) or "").lower()
                    if needle in cell_text:
                        label_cell = cell
                    elif label_cell is not None and cell is not label_cell:
                        value_cell = cell
                        break
                if value_cell is not None:
                    return _coerce_number(value_cell.get_text(" ", strip=True))
    except Exception:
        pass

    candidate = ""
    try:
        for sib in el.find_all(["span", "div", "td"], recursive=False):
            sib_text = (sib.get_text(" ", strip=True) or "").lower()
            if needle in sib_text:
                continue
            if sib_text:
                candidate = sib.get_text(" ", strip=True)
                break
        if not candidate:
            idx = text.find(needle)
            candidate = text[idx + len(needle) :].strip(" :\u2014-|")
    except Exception:
        candidate = ""

    return _coerce_number(candidate)


def _parse_tv_financials_html(html: str) -> Optional[Dict[str, Optional[float]]]:
    """Parsuje HTML strony TV Financials na wskaźniki ``Fund_*``."""
    if not html:
        return None
    try:
        from bs4 import BeautifulSoup  # type: ignore
    except Exception:
        logger.warning("BeautifulSoup brak — TV fallback nieczynny.")
        return None

    soup = BeautifulSoup(html, "lxml")
    out: Dict[str, Optional[float]] = {}
    text_blocks = []
    for el in soup.find_all(["div", "li", "tr"]):
        if _tv_element_in_chart(el):
            continue
        try:
            text = (el.get_text(" ", strip=True) or "").lower()
        except Exception:
            continue
        if not text or len(text) > 200:
            continue
        text_blocks.append((text, el))

    for fund_key, labels in _TV_ROW_LABELS.items():
        value: Optional[float] = None
        for needle in labels:
            for text, el in text_blocks:
                if needle not in text:
                    continue
                parsed = _tv_extract_value_for_label(el, needle, text)
                if parsed is not None and _is_sane_fund_value(fund_key, parsed):
                    value = parsed
                    break
            if value is not None:
                break
        out[fund_key] = value

    sanitized = _sanitize_fund_values(out)
    if all(v is None for v in sanitized.values()):
        return None
    return sanitized


def _tv_financials_http_fallback(ticker: str) -> Optional[Dict[str, Optional[float]]]:
    """Fallback do TradingView Financials przez HTTP (bez Playwright)."""
    import urllib.error
    import urllib.request

    url = _tv_financials_url(ticker)
    if not url:
        return None
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (compatible; trading_analyser/1.0)"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            html = resp.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        logger.warning("TV financials HTTP %s zawiódł: %s", url, exc)
        return None
    return _parse_tv_financials_html(html)


def _tv_financials_fallback(
    ticker: str, page: Any
) -> Optional[Dict[str, Optional[float]]]:
    """Fallback do TradingView Financials (Playwright).

    Minimalna ale działająca implementacja: otwiera stronę
    ``/symbols/{EXCH}-{SYM}/financials-statistics-and-ratios/`` i parsuje
    wiersze po tekście etykiety.  Gdy ``page`` jest ``None`` – natychmiast
    zwraca ``None``.
    """
    if page is None or not ticker:
        return None
    url = _tv_financials_url(ticker)
    if not url:
        return None
    try:
        page.goto(url, wait_until="domcontentloaded")
    except Exception as exc:  # noqa: BLE001
        logger.warning("TV financials goto %s zawiódł: %s", url, exc)
        return None

    try:
        html = page.content()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Nie udało się pobrać HTML TV financials: %s", exc)
        return None

    return _parse_tv_financials_html(html)


# ---------------------------------------------------------------------------
# Public entrypoint
# ---------------------------------------------------------------------------


def _empty_row(ticker: str, source: str = "none") -> Dict[str, Any]:
    row: Dict[str, Any] = {"Ticker": str(ticker).strip()}
    for key in FUND_KEYS:
        row[key] = None
    row["Fund_Source"] = source
    row["Fund_Updated_At"] = _now_utc_iso()
    return row


def fetch_fundamentals(
    ticker: str,
    *,
    tv_fallback_page: Any = None,
    tv_http_fallback: bool = False,
    cache_path: Path,
    ttl_hours: int = 24,
    force_refresh: bool = False,
) -> Dict[str, Any]:
    """Główny entrypoint pobierania fundamentów dla jednego tickera.

    Przepływ:
      1. Krypto → ``Fund_Source='none'``, wszystkie pola ``None``.
      2. Cache hit (TTL) → zwrot z dysku.
      3. yfinance (mapowanie ``to_yahoo_symbol``) → jeśli przyniosło
         przynajmniej jeden niepusty wskaźnik, zapisz cache i zwróć.
      4. Gdy yfinance pusty – TV fallback (Playwright ``tv_fallback_page``
         albo HTTP ``tv_http_fallback`` dla tickerów z prefixem giełdy).
      5. W ostateczności ``Fund_Source='none'``.
    """
    ticker_norm = str(ticker or "").strip()
    if not ticker_norm:
        raise ValueError("Empty ticker")

    cache_path = Path(cache_path)

    # 1) crypto
    if is_crypto(ticker_norm):
        row = _empty_row(ticker_norm, source="none")
        with _cache_lock:
            cache = _read_cache(cache_path)
            cache[ticker_norm.upper()] = {**row, "_cached_at": time.time()}
            _write_cache(cache_path, cache)
        return row

    # 2) cache hit
    ttl_seconds = max(0, float(ttl_hours)) * 3600.0
    if not force_refresh:
        with _cache_lock:
            cache = _read_cache(cache_path)
            entry = cache.get(ticker_norm.upper())
            if isinstance(entry, dict):
                cached_at = entry.get("_cached_at")
                try:
                    cached_at_f = float(cached_at) if cached_at is not None else None
                except (TypeError, ValueError):
                    cached_at_f = None
                if cached_at_f is not None and (time.time() - cached_at_f) < ttl_seconds:
                    out = {k: v for k, v in entry.items() if not k.startswith("_")}
                    for k in FUND_KEYS:
                        out.setdefault(k, None)
                    out.setdefault("Ticker", ticker_norm)
                    out.setdefault("Fund_Source", "none")
                    out.setdefault("Fund_Updated_At", _now_utc_iso())
                    sanitized = _sanitize_fund_values({k: out.get(k) for k in FUND_KEYS})
                    for k in FUND_KEYS:
                        out[k] = sanitized.get(k)
                    if fundamentals_row_has_values(out):
                        return out
                    source = str(out.get("Fund_Source") or "").strip().lower()
                    if source in ("none", "n/a"):
                        return out

    # 3) yfinance
    yf_values: Optional[Dict[str, Optional[float]]] = None
    if to_yahoo_symbol(ticker_norm):
        yf_values = _yf_fetch_best(ticker_norm)

    used_source: Optional[str] = None
    values: Dict[str, Optional[float]] = {k: None for k in FUND_KEYS}
    if yf_values and any(v is not None for v in yf_values.values()):
        for k in FUND_KEYS:
            values[k] = yf_values.get(k)
        used_source = "yfinance"

    # 4) TV fallback (gdy yfinance pusty — uzupełnij brakujące pola)
    if tv_fallback_page is not None or tv_http_fallback:
        need_tv = used_source is None
        if not need_tv:
            need_tv = any(values.get(k) is None for k in FUND_KEYS)
        if need_tv:
            tv_values: Optional[Dict[str, Optional[float]]] = None
            try:
                if tv_fallback_page is not None:
                    tv_values = _tv_financials_fallback(ticker_norm, tv_fallback_page)
                elif tv_http_fallback:
                    tv_values = _tv_financials_http_fallback(ticker_norm)
            except Exception as exc:  # noqa: BLE001
                logger.warning("TV fallback dla %s rzucił wyjątkiem: %s", ticker_norm, exc)
                tv_values = None
            if tv_values and any(v is not None for v in tv_values.values()):
                if used_source is None:
                    used_source = "tradingview"
                for k in FUND_KEYS:
                    if values.get(k) is None and tv_values.get(k) is not None:
                        values[k] = tv_values.get(k)

    values = _sanitize_fund_values(values)
    if used_source is not None and not any(v is not None for v in values.values()):
        used_source = None

    row: Dict[str, Any] = {"Ticker": ticker_norm}
    for k in FUND_KEYS:
        row[k] = values.get(k)
    row["Fund_Source"] = used_source or "none"
    row["Fund_Updated_At"] = _now_utc_iso()

    with _cache_lock:
        cache = _read_cache(cache_path)
        cache[ticker_norm.upper()] = {**row, "_cached_at": time.time()}
        _write_cache(cache_path, cache)
    return row
