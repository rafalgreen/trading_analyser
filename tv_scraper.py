import time
import re
import os
import json
import argparse
import logging
from datetime import datetime
from typing import Any, Dict, Optional

import pandas as pd
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup

from results_store import (
    CSV_META_COLUMNS,
    apply_final_scrape_status,
    ensure_meta_columns as _ensure_meta_columns,
    get_row_for_ticker_interval,
    load_results_dataframe,
    merge_existing_row_into_row_data,
    order_result_columns as _order_result_columns,
    record_skipped_ticker,
    row_has_indicator_data,
    row_interval_complete,
    save_results_row,
    ticker_fully_done_in_csv,
    ticker_marked_skipped_for_day,
)

STATUS_FILE = "scraper_status.json"
CONFIG_FILE = "scraper_config.json"


def resolve_cdp_port(
    config: Optional[Dict[str, Any]] = None, cli_port: Optional[int] = None
) -> int:
    """Kolejność: --cdp-port (CLI), potem TV_CDP_PORT, potem scraper_config.json → cdp_port, domyślnie 9222."""
    if cli_port is not None:
        if not (1 <= cli_port <= 65535):
            raise ValueError(f"cdp_port poza zakresem 1–65535: {cli_port}")
        return cli_port
    env = (os.environ.get("TV_CDP_PORT") or "").strip()
    if env:
        try:
            p = int(env)
            if 1 <= p <= 65535:
                return p
        except ValueError:
            pass
        logger.warning("TV_CDP_PORT=%r — ignoruję, używam domyślnego portu z configu.", env)
    if config:
        raw = config.get("cdp_port", 9222)
        if isinstance(raw, int) and 1 <= raw <= 65535:
            return raw
        if isinstance(raw, str) and raw.isdigit():
            p = int(raw)
            if 1 <= p <= 65535:
                return p
    return 9222

SLEEP_AFTER_INDICATOR_MODAL_S = 2
SLEEP_AFTER_INDICATOR_QUERY_S = 3
SLEEP_AFTER_INDICATOR_COMPUTE_S = 4
SLEEP_AFTER_TICKER_ENTER_S = 3
SLEEP_AFTER_INTERVAL_CHANGE_S = 2
SLEEP_AFTER_SMALL_ACTION_S = 1
SLEEP_AFTER_MICRO_ACTION_S = 0.5
SYMBOL_SEARCH_LIST_WAIT_MS = 4500

logger = logging.getLogger("tv_scraper")

_SYMBOL_SEARCH_NOISE_UPPER = frozenset(
    {
        "STOCK",
        "STOCKS",
        "ETF",
        "ETFS",
        "FUND",
        "FUNDS",
        "INDEX",
        "INDICES",
        "FOREX",
        "FX",
        "CRYPTO",
        "CRYPTOCURRENCY",
        "FUTURES",
        "BOND",
        "BONDS",
        "OPTION",
        "OPTIONS",
        "PERPETUAL",
        "DELAYED",
        "DATA",
        "REAL-TIME",
        "AKCJE",
        "AKCJA",
        "FUNDUSZ",
        "FUNDUSZE",
        "INDEKS",
        "WALUTY",
        "WALUTA",
    }
)

_SYMBOL_SEARCH_EXCH_UPPER = frozenset(
    {
        "NYSE",
        "NASDAQ",
        "AMEX",
        "ARCA",
        "OTC",
        "OTCQX",
        "OTCQB",
        "PINK",
        "LSE",
        "FWB",
        "XETR",
        "GPW",
        "WSE",
        "TSX",
        "TSXV",
        "ASX",
        "HKEX",
        "SSE",
        "SZSE",
        "TSE",
        "SGX",
        "EURONEXT",
        "BME",
        "BIST",
        "SIX",
        "OMX",
        "MIB",
    }
)


def _symbol_search_line_is_noise(line: str) -> bool:
    lu = line.upper().strip()
    if lu in _SYMBOL_SEARCH_NOISE_UPPER or lu in _SYMBOL_SEARCH_EXCH_UPPER:
        return True
    if re.fullmatch(r"[\d\s\.,+%▼▲N/A$-]+", line, re.IGNORECASE):
        return True
    return False


def _first_line_open_ticker(line: str) -> str:
    m = re.match(r"^([A-Za-z0-9]+)", (line or "").strip())
    return m.group(1).upper() if m else ""


def _exchange_suffix_ticker(line: str) -> str:
    if ":" not in line:
        return ""
    tail = line.rsplit(":", 1)[-1].strip()
    m = re.match(r"^([A-Za-z0-9]+)", tail)
    return m.group(1).upper() if m else ""


def parse_symbol_search_modal_blob(blob: str, ticker: str) -> Dict[str, str]:
    """Wyciąga ``{name, exchange}`` z tekstu pojedynczego wiersza Symbol Search (EN/PL UI).

    Pierwsza linia musi być dopasowana do `ticker` (sam ticker albo ``EXCH:TICKER``);
    w przeciwnym razie zwraca puste pola. Najdłuższa nie-szumowa linia z literami
    leci jako nazwa firmy. Giełda to pierwszy token z whitelisty
    :data:`_SYMBOL_SEARCH_EXCH_UPPER` (np. ``"NYSE"``, ``"GPW"``) — może być na
    osobnej linii albo w prefiksie tickera (``GPW:ATC``).
    """
    out = {"name": "", "exchange": ""}
    ticker_u = (ticker or "").strip().upper()
    if not ticker_u or not (blob or "").strip():
        return out
    lines = [
        re.sub(r"\s+", " ", x.strip()) for x in blob.splitlines() if x.strip()
    ]
    if not lines:
        return out
    first = lines[0]
    ticker_match = _first_line_open_ticker(first) == ticker_u or (
        _exchange_suffix_ticker(first) == ticker_u
    )
    if not ticker_match:
        return out

    # Giełda — preferujemy prefix w pierwszej linii ("GPW:ATC"), inaczej osobny token.
    if ":" in first:
        prefix = first.split(":", 1)[0].strip().upper()
        if prefix in _SYMBOL_SEARCH_EXCH_UPPER:
            out["exchange"] = prefix

    best = ""
    for line in lines[1:]:
        lu = line.upper().strip()
        if not out["exchange"] and lu in _SYMBOL_SEARCH_EXCH_UPPER:
            out["exchange"] = lu
            continue
        if _symbol_search_line_is_noise(line):
            continue
        if lu == ticker_u:
            continue
        if len(line) < 3 or not re.search(r"[A-Za-zÀ-ÖØ-öø-ÿ]", line):
            continue
        if len(line) > len(best):
            best = line.strip()
    out["name"] = best
    return out


def company_name_from_symbol_search_modal_text(blob: str, ticker: str) -> str:
    """Wyciąga pełną nazwę firmy z tekstu pojedynczego wiersza listy Symbol Search (EN/PL UI).

    Cienki wrapper nad :func:`parse_symbol_search_modal_blob` — zwraca tylko
    ``name`` (zachowane API dla testów i innych callerów).
    """
    return parse_symbol_search_modal_blob(blob, ticker)["name"]


def read_symbol_search_modal_company_info(page: Any, ticker: str) -> Dict[str, str]:
    """Czyta `{name, exchange}` z otwartego Symbol Search zanim wybrano Enter.

    Iteruje po widocznych wierszach listy i bierze pierwszy, dla którego nazwa
    firmy została rozpoznana. Giełda — z tego samego wiersza (jeśli była).
    """
    tu = (ticker or "").strip().upper()
    if not tu:
        return {"name": "", "exchange": ""}
    try:
        page.wait_for_selector(
            'div[data-role="list-item"]:visible',
            timeout=SYMBOL_SEARCH_LIST_WAIT_MS,
        )
    except Exception:
        return {"name": "", "exchange": ""}
    try:
        items = page.locator('div[data-role="list-item"]:visible')
        n = min(items.count(), 25)
    except Exception:
        return {"name": "", "exchange": ""}
    for i in range(n):
        try:
            blob = items.nth(i).inner_text(timeout=1500)
        except Exception:
            continue
        info = parse_symbol_search_modal_blob(blob, tu)
        if info.get("name"):
            return info
    return {"name": "", "exchange": ""}


def read_symbol_search_modal_company_name(page: Any, ticker: str) -> str:
    """Czyta pierwszy sensowny wiersz nazwy z otwartego Symbol Search zanim wybrano Enter.

    Zachowane dla wstecznej kompatybilności — używa
    :func:`read_symbol_search_modal_company_info` i zwraca tylko nazwę.
    """
    return read_symbol_search_modal_company_info(page, ticker)["name"]


def _configure_logging() -> None:
    """Konfiguruje logging zgodnie ze zmienną TV_LOG_LEVEL (domyślnie INFO)."""
    if logger.handlers:
        return
    level_name = os.environ.get("TV_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    )
    logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False


def _format_duration(seconds: float) -> str:
    """Czytelny czas trwania (np. ``45.2s``, ``12m 3s``)."""
    if seconds < 0:
        seconds = 0.0
    if seconds < 60:
        return f"{seconds:.1f}s"
    m, s = divmod(int(seconds), 60)
    if m < 60:
        return f"{m}m {s}s"
    h, m = divmod(m, 60)
    return f"{h}h {m}m {s}s"


def write_scraper_status(
    status,
    progress="",
    current_ticker="",
    error="",
    duration_seconds=None,
    duration_human=None,
):
    """Write scraper status to JSON file for web UI polling."""
    data = {
        "status": status,
        "progress": progress,
        "current_ticker": current_ticker,
        "error": error,
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    if duration_seconds is not None:
        data["duration_seconds"] = round(float(duration_seconds), 2)
    if duration_human:
        data["duration_human"] = duration_human
    try:
        with open(STATUS_FILE, "w") as f:
            json.dump(data, f)
    except Exception:
        pass


def get_color_name(rgb_str):
    """Pomocnicza funkcja do nazywania podstawowych kolorów TradingView"""
    if not rgb_str:
        return "Brak"
    if "242, 54, 69" in rgb_str or "red" in rgb_str.lower():
        return "Czerwony"
    if "0, 188, 212" in rgb_str or "blue" in rgb_str.lower():
        return "Niebieski"
    if "8, 153, 129" in rgb_str or "green" in rgb_str.lower():
        return "Zielony"
    if "255, 170, 0" in rgb_str or "orange" in rgb_str.lower():
        return "Pomarańczowy"
    return rgb_str


def _to_float(text) -> Optional[float]:
    """Parsuje liczby z TradingView: NBSP/tysięczne, przecinek dziesiętny, unicode minus."""
    if text is None:
        return None
    s = str(text)
    s = re.sub(r"\s+", "", s)
    s = s.replace("\u2212", "-").replace(",", ".")
    if not s:
        return None
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def _company_name_from_title(title_text: str, ticker: str = "") -> str:
    """Wyciąga nazwę instrumentu z tytułu karty TradingView (przed ``—`` / ``-``)."""
    core = (
        (title_text or "")
        .split(" Wskaźnik")[0]
        .split(" Wykres")[0]
        .split(" —")[0]
        .split(" -")[0]
        .strip()
    )
    if not core:
        return ""
    tk = (ticker or "").strip().upper()
    first_seg = core.split("·")[0].strip() if "·" in core else core.strip()
    if tk and first_seg:
        m = re.match(rf"^(.+?)\s*\(\s*{re.escape(tk)}\s*\)\s*$", first_seg, re.IGNORECASE)
        if m and len(m.group(1).strip()) >= 2:
            return m.group(1).strip()
    if "·" in core:
        return first_seg
    match = re.search(r"^(.+?)\s+(\d+[\.,]\d+|\d+)\s*$", core)
    if match:
        return match.group(1).strip()
    return core.split()[0] if core.split() else ""


def _pick_company_line_from_header_blob(blob: str, ticker_u: str) -> str:
    """Z tekstu toolbaru wybiera nazwę spółki (linia obok tickera, ``Nazwa (TICKER)`` lub fragment przed ``(TICKER)``)."""
    if not blob or not ticker_u:
        return ""
    flat = re.sub(r"\s+", " ", blob.replace("\r", "\n")).strip()
    high = flat.upper()
    needle = f"({ticker_u.upper()})"
    idx = high.rfind(needle)
    if idx > 0:
        left = flat[:idx].strip()
        for sep in (" · ", " | ", " — ", " - ", " / "):
            if sep in left:
                left = left.split(sep)[-1].strip()
        parts = re.split(r"\s{2,}", left)
        left = parts[-1].strip() if parts else left
        left = re.sub(r"^[\W\d_]+", "", left).strip()
        left = re.sub(
            r"^(?:charts|screeners|news|watchlist|symbol|ideas|trade|community)\s+",
            "",
            left,
            flags=re.IGNORECASE,
        ).strip()
        if len(left) >= 3 and left.upper().replace(" ", "") != ticker_u:
            return left
    mflat = re.search(
        rf"([\w\s,'\.\-]{{3,120}})\s*\(\s*{re.escape(ticker_u)}\s*\)",
        flat,
        re.IGNORECASE,
    )
    if mflat:
        name = mflat.group(1).strip()
        if len(name) >= 2 and name.upper().replace(" ", "") != ticker_u:
            return name
    lines = [
        re.sub(r"\s+", " ", x.strip())
        for x in re.split(r"[\n\r]+", blob)
        if x.strip()
    ]
    for line in lines:
        m = re.match(
            rf"^(.+?)\s*\(\s*{re.escape(ticker_u)}\s*\)\s*$",
            line,
            re.IGNORECASE,
        )
        if m and len(m.group(1).strip()) >= 2:
            return m.group(1).strip()
    best = ""
    for line in lines:
        if line.upper().replace(" ", "") == ticker_u:
            continue
        if len(line) > len(best):
            best = line
    return best.strip()


def read_chart_symbol_header_blob(target_page, ticker: str = "") -> str:
    """Zbiera tekst z przycisku symbolu i z całego ``header-toolbar`` (TV bywa niespójne w DOM)."""
    selectors = [
        'button[data-name="header-toolbar-symbol-search"]',
        '[data-name="header-toolbar-symbol-search"]',
        "button#header-toolbar-symbol-search",
        '[data-name="header-toolbar-symbol-details"]',
        '[class*="symbolNameText"]',
        '[data-name="legend-source-title"]',
        '[data-qa-id="title-wrapper legend-source-title"]',
    ]
    chunks: list[str] = []
    for sel in selectors:
        try:
            loc = target_page.locator(sel).first
            if loc.count() > 0:
                t = loc.inner_text(timeout=2500).strip()
                if t:
                    chunks.append(t)
        except Exception:
            continue
    try:
        extra = target_page.evaluate(
            """selList => {
                const parts = [];
                const add = (t) => {
                    const s = (t || '').trim();
                    if (s) parts.push(s);
                };
                for (const sel of selList) {
                    const el = document.querySelector(sel);
                    if (el) add(el.innerText || el.textContent);
                }
                const bar = document.querySelector('[data-name="header-toolbar"]');
                if (bar) add(bar.innerText || bar.textContent);
                return [...new Set(parts)].join('\\n');
            }""",
            selectors,
        )
        if extra and str(extra).strip():
            chunks.append(str(extra).strip())
    except Exception:
        pass
    seen: set[str] = set()
    out: list[str] = []
    for c in chunks:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return "\n".join(out)


def resolve_company_name(
    title_text: str,
    legend_description: str,
    ticker: str,
    header_toolbar_text: str = "",
    symbol_search_text: str = "",
) -> str:
    """Kolejność: lista Symbol Search, toolbar nagłówka, tytuł okna, opis legendy.

    Jako ostatni fallback (gdy żadne z 4 źródeł DOM nie dało sensownej nazwy)
    odpytujemy publiczny endpoint TV ``symbol-search`` przez
    :func:`company_names.lookup_company_name`. To i tak jest cache'owane,
    więc nie generuje znaczącego ruchu sieciowego.
    """
    ticker_u = (ticker or "").strip().upper()
    leg = (legend_description or "").strip()
    from_symbol_search = (symbol_search_text or "").strip()
    header_pick = _pick_company_line_from_header_blob(
        (header_toolbar_text or "").strip(), ticker_u
    )
    from_title = _company_name_from_title(title_text or "", ticker)

    def _looks_like_price_only(s: str) -> bool:
        return bool(re.fullmatch(r"[\d\s\.,+%▼▲N/A]+", s, re.IGNORECASE))

    def _sensible(s: str) -> bool:
        if not s or len(s) < 2:
            return False
        if s.strip().upper() == ticker_u:
            return False
        if _looks_like_price_only(s):
            return False
        return True

    sources = (
        ("symbol_search", from_symbol_search),
        ("header_toolbar", header_pick),
        ("window_title", from_title),
        ("legend_description", leg),
    )
    try:
        logger.debug(
            "resolve_company_name(%s) sources: %s",
            ticker_u,
            {k: (v[:80] if isinstance(v, str) else v) for k, v in sources},
        )
    except Exception:
        pass

    for src, candidate in sources:
        if _sensible(candidate):
            try:
                logger.debug(
                    "resolve_company_name(%s) -> %r [src=%s]",
                    ticker_u,
                    candidate.strip(),
                    src,
                )
            except Exception:
                pass
            return candidate.strip()
    for src, candidate in sources:
        if candidate and candidate.strip():
            c = candidate.strip()
            if c.upper() != ticker_u:
                try:
                    logger.debug(
                        "resolve_company_name(%s) -> %r [src=%s,fallback]",
                        ticker_u,
                        c,
                        src,
                    )
                except Exception:
                    pass
                return c

    # REST fallback (TV symbol-search) — last resort before plain ticker.
    if ticker_u:
        try:
            from company_names import lookup_company_name as _lookup_rest
            rest_name = _lookup_rest(ticker_u)
        except Exception as exc:  # noqa: BLE001
            try:
                logger.debug(
                    "resolve_company_name REST lookup failed for %s: %s",
                    ticker_u,
                    exc,
                )
            except Exception:
                pass
            rest_name = ""
        if rest_name and rest_name.strip().upper() != ticker_u:
            try:
                logger.debug(
                    "resolve_company_name(%s) -> %r [src=tv_rest]",
                    ticker_u,
                    rest_name,
                )
            except Exception:
                pass
            return rest_name.strip()
        return ticker_u
    return "Nieznana"


def _exchange_from_header_blob(blob: str) -> str:
    """Z tekstu toolbaru/legendy znajduje pierwszy token z whitelisty giełd."""
    if not blob:
        return ""
    for tok in re.findall(r"[A-Z]{2,8}", blob.upper()):
        if tok in _SYMBOL_SEARCH_EXCH_UPPER:
            return tok
    return ""


def resolve_exchange(
    ticker: str,
    *,
    symbol_search_exchange: str = "",
    header_blob: str = "",
) -> str:
    """Zwraca symbol giełdy (np. ``"NYSE"``) dla `ticker`.

    Priorytet: symbol-search modal → prefix tickera (``GPW:ATC``) → header blob
    (whitelist tokenów) → REST ``lookup_exchange`` (cached). Zwraca ``""`` gdy
    żadne źródło nie da pewnej giełdy.
    """
    cand = (symbol_search_exchange or "").strip().upper()
    if cand and cand in _SYMBOL_SEARCH_EXCH_UPPER:
        logger.debug("resolve_exchange(%s) -> %s [src=symbol_search]", ticker, cand)
        return cand

    tk = (ticker or "").strip()
    if ":" in tk:
        prefix = tk.split(":", 1)[0].strip().upper()
        if prefix in _SYMBOL_SEARCH_EXCH_UPPER:
            logger.debug(
                "resolve_exchange(%s) -> %s [src=ticker_prefix]", ticker, prefix
            )
            return prefix

    from_header = _exchange_from_header_blob(header_blob or "")
    if from_header:
        logger.debug(
            "resolve_exchange(%s) -> %s [src=header_blob]", ticker, from_header
        )
        return from_header

    bare_ticker = tk.split(":", 1)[-1].strip()
    if bare_ticker:
        try:
            from company_names import lookup_exchange as _lookup_rest_exch
            rest_exch = (_lookup_rest_exch(bare_ticker) or "").strip().upper()
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "resolve_exchange REST lookup failed for %s: %s", ticker, exc
            )
            rest_exch = ""
        if rest_exch:
            logger.debug(
                "resolve_exchange(%s) -> %s [src=tv_rest]", ticker, rest_exch
            )
            return rest_exch
    return ""


def indicator_title_matches(title_text: str, ind_name: str) -> bool:
    """Zgodność tytułu bloku legendy z nazwą wskaźnika z konfiguracji (jak ``parse_indicators``)."""
    tl = (title_text or "").lower()
    raw = (ind_name or "").strip()
    il = raw.lower()

    if raw == "PCA" or il == "pca":
        return "pca-ri" in tl or "pca risk" in tl or "pca" in tl
    if il == "macd":
        return "macd" in tl
    if il == "hts panel" or il.replace(" ", "") == "htspanel":
        return "hts" in tl and "panel" in tl
    return il in tl


def _ensure_legend_expanded(target_page) -> None:
    """Rozwija legendę wykresu, jeśli TV pokazuje zwinięty stan (CDP)."""
    for sel in (
        '[data-name="legend-expand-action"]',
        '[data-name="legend-toggle-action"]',
    ):
        try:
            loc = target_page.locator(sel).first
            if loc.count() > 0 and loc.is_visible(timeout=300):
                loc.click(force=True, timeout=1500)
                time.sleep(SLEEP_AFTER_MICRO_ACTION_S)
        except Exception:
            pass


def _page_legend_has_indicator(target_page, ind_name: str) -> bool:
    """Czy w DOM jest widoczny blok legendy dla danego wskaźnika."""
    try:
        items = target_page.locator('[data-qa-id="legend-source-item"]')
        n = items.count()
        for i in range(min(n, 40)):
            tw = items.nth(i).locator(
                '[data-qa-id="title-wrapper legend-source-title"]'
            )
            if tw.count() == 0:
                continue
            title = tw.inner_text(timeout=800)
            if indicator_title_matches(title, ind_name):
                return True
    except Exception:
        return False
    return False


def _wait_for_legend_indicator_ready(
    target_page, ind_name: str, max_attempts: int = 5, delay_s: float = 1.0
) -> None:
    """Czeka na pojawienie się wskaźnika w legendzie; rozwija legendę między próbami."""
    for attempt in range(max_attempts):
        _ensure_legend_expanded(target_page)
        if _page_legend_has_indicator(target_page, ind_name):
            if attempt > 0:
                logger.info(
                    "Legenda: wskaźnik %s widoczny w DOM (próba %d/%d).",
                    ind_name,
                    attempt + 1,
                    max_attempts,
                )
            return
        time.sleep(delay_s)
    logger.warning(
        "Legenda: nie udało się potwierdzić obecności %s w DOM po %d próbach — kontynuuję odczyt.",
        ind_name,
        max_attempts,
    )


def parse_indicators(html_content, indicators_to_find):
    """Pobiera i parsuje wartości wskaźników z html dla podanej listy nazw."""
    soup = BeautifulSoup(html_content, "lxml")
    legend_items = soup.find_all("div", attrs={"data-qa-id": "legend-source-item"})
    if logger.isEnabledFor(logging.DEBUG):
        titles_dbg = []
        for _it in legend_items:
            _te = _it.find(
                "div", attrs={"data-qa-id": "title-wrapper legend-source-title"}
            )
            if _te:
                titles_dbg.append(_te.get_text(strip=True))
        logger.debug("Legenda: %d bloków, tytuły: %s", len(legend_items), titles_dbg)

    results = {}
    for ind in indicators_to_find:
        results[f"{ind}_Values"] = "Brak danych na wykresie"

    results["PCA_Value"] = None
    results["PCA_Color"] = None

    for item in legend_items:
        title_el = item.find(
            "div", attrs={"data-qa-id": "title-wrapper legend-source-title"}
        )
        if not title_el:
            continue

        title_text = title_el.get_text(strip=True)

        for ind_name in indicators_to_find:
            if not indicator_title_matches(title_text, ind_name):
                continue

            try:
                if ind_name == "PCA":
                    _parse_pca_block(item, results, ind_name)
                else:
                    _parse_hts_like_block(item, results, ind_name)
            except Exception as exc:
                logger.warning("Błąd parsowania bloku %s: %s", ind_name, exc)

    return results


def _parse_pca_block(item, results, ind_name: str) -> None:
    values = []
    for div in item.find_all("div"):
        classes = div.get("class", [])
        if any("valueValue" in c for c in classes) or any(
            "valueItem" in c for c in classes
        ):
            text = div.get_text(strip=True)
            style = div.get("style", "")
            if text and text != "\u2205":
                values.append({"text": text, "style": style})

    if not values:
        return
    last_val = values[-1]
    results["PCA_Value"] = last_val["text"]
    results["PCA_Color"] = get_color_name(last_val["style"])
    results[f"{ind_name}_Values"] = (
        f"{last_val['text']} ({results['PCA_Color']})"
    )


def _parse_hts_like_block(item, results, ind_name: str) -> None:
    values = []
    values_root = item.find("div", attrs={"data-qa-id": "legend-source-values"})
    search_roots = [values_root] if values_root else [item]
    for root in search_roots:
        if root is None:
            continue
        for div in root.find_all("div"):
            classes = div.get("class", [])
            if any("valueValue" in c for c in classes) or any(
                "valueItem" in c for c in classes
            ):
                text = div.get_text(strip=True)
                style = div.get("style", "")
                if text and text not in ("\u2205", "0", "0.00", "0,00"):
                    values.append({"text": text, "color": get_color_name(style)})

    dedup_values = []
    for v in values:
        if v not in dedup_values:
            dedup_values.append(v)

    if len(dedup_values) < 4:
        str_vals = [f"{v['text']} ({v['color']})" for v in dedup_values]
        results[f"{ind_name}_Values"] = (
            " | ".join(str_vals) if str_vals else "Brak poprawnych danych"
        )
        return

    fh_raw, fl_raw, sh_raw, sl_raw = (
        dedup_values[0],
        dedup_values[1],
        dedup_values[2],
        dedup_values[3],
    )
    results[f"{ind_name}_Fast_High"] = f"{fh_raw['text']} ({fh_raw['color']})"
    results[f"{ind_name}_Fast_Low"] = f"{fl_raw['text']} ({fl_raw['color']})"
    results[f"{ind_name}_Slow_High"] = f"{sh_raw['text']} ({sh_raw['color']})"
    results[f"{ind_name}_Slow_Low"] = f"{sl_raw['text']} ({sl_raw['color']})"

    results[f"{ind_name}_Trend"] = (
        "Wzrostowy" if fl_raw["color"] == "Niebieski" else "Spadkowy"
    )

    fh = _to_float(fh_raw["text"])
    fl = _to_float(fl_raw["text"])
    sh = _to_float(sh_raw["text"])
    sl = _to_float(sl_raw["text"])
    if None in (fh, fl, sh, sl):
        logger.debug(
            "Nie udało się sparsować liczb dla %s: %s",
            ind_name,
            [fh_raw["text"], fl_raw["text"], sh_raw["text"], sl_raw["text"]],
        )
        results[f"{ind_name}_Cross"] = "Brak Crossa"
        return

    cross_info = "Brak Crossa"
    if fl > sh:
        cross_info = "BULL CROSS (Wstęgi się przecięły w górę)"
    elif fh < sl:
        cross_info = "BEAR CROSS (Wstęgi się przecięły w dół)"
    results[f"{ind_name}_Cross"] = cross_info


def add_indicator_to_chart(target_page, ind_name: str, ticker: str) -> None:
    """Otwiera modal wskaźników, wybiera pierwszy wynik, zamyka modal."""
    target_page.keyboard.press("/")
    time.sleep(SLEEP_AFTER_INDICATOR_MODAL_S)
    target_page.keyboard.type(ind_name, delay=100)
    time.sleep(SLEEP_AFTER_INDICATOR_QUERY_S)
    try:
        target_page.wait_for_selector(
            'div[data-role="list-item"]', state="visible", timeout=3000
        )
        target_page.locator('div[data-role="list-item"]').first.click(force=True)
    except Exception as e:
        raise RuntimeError(
            f"Zbyt długi czas oczekiwania na listę wskaźników ({ind_name}) dla {ticker}. Błąd: {e}"
        )
    time.sleep(SLEEP_AFTER_SMALL_ACTION_S)
    target_page.keyboard.press("Escape")
    logger.info(
        "Czekam na przeliczenie wskaźnika (%ss)...", SLEEP_AFTER_INDICATOR_COMPUTE_S
    )
    time.sleep(SLEEP_AFTER_INDICATOR_COMPUTE_S)


def remove_active_indicator(target_page, ind_name: str, ticker: str) -> None:
    """Usuwa aktywny wskaźnik z wykresu. Błędy są logowane, przebieg nie jest przerywany."""
    logger.info("Usuwam wskaźnik (%s), by oczyścić widok...", ind_name)
    try:
        options_btn = target_page.locator(
            'button[aria-label="Usuń opcje"], button[aria-label="Remove options"]'
        )
        if options_btn.count() > 0:
            options_btn.first.click(force=True)
            time.sleep(SLEEP_AFTER_MICRO_ACTION_S)
            menu_items = target_page.locator('[data-role="menuitem"]').all()
            for el in menu_items:
                text = el.inner_text().strip()
                if re.search(
                    r"Usuń.*wskaźnik|Remove.*indicator",
                    text,
                    re.IGNORECASE,
                ) and "rysun" not in text.lower() and "drawing" not in text.lower():
                    el.click(force=True)
                    break
    except Exception as e:
        logger.warning(
            "Nie udało się usunąć wskaźnika '%s' dla %s: %s (kontynuuję).",
            ind_name,
            ticker,
            e,
        )


def run_scraper(tickers, intervals, indicators, port=9222, is_partial=False):
    _configure_logging()
    # Use 127.0.0.1 (not "localhost"): on many systems localhost resolves to ::1 first,
    # while Chromium's CDP often listens only on IPv4 — then connect_over_cdp fails with ECONNREFUSED ::1:9222.
    cdp_url = f"http://127.0.0.1:{port}"
    logger.info("Łączenie z przeglądarką przez CDP: %s", cdp_url)

    state_file = "scraper_state.json"
    processed_combos = set()
    current_run_file = None

    if not is_partial and os.path.exists(state_file):
        try:
            with open(state_file, "r") as f:
                state = json.load(f)
                if "current_file" in state and os.path.exists(state["current_file"]):
                    current_run_file = state["current_file"]
                    processed_combos = set(
                        tuple(x) for x in state.get("processed", [])
                    )
                    logger.info(
                        "Wznawiam pracę z poprzedniej sesji. Plik: %s (pominięto %d kombinacji)",
                        current_run_file,
                        len(processed_combos),
                    )
        except Exception as e:
            logger.warning("Błąd odczytu pliku stanu: %s", e)

    if not current_run_file:
        os.makedirs("results", exist_ok=True)
        date_str = datetime.now().strftime("%Y-%m-%d")
        current_run_file = os.path.join(
            "results", f"tradingview_results_{date_str}.csv"
        )
        logger.info(
            "Rozpoczynam nową sesję pobierania. Plik docelowy: %s", current_run_file
        )

    def update_state(ticker_val, interval_val):
        if is_partial:
            return
        processed_combos.add((ticker_val, interval_val))
        try:
            with open(state_file, "w") as f:
                json.dump(
                    {
                        "current_file": current_run_file,
                        "processed": list(processed_combos),
                    },
                    f,
                )
        except Exception as exc:
            logger.warning("Nie udało się zapisać stanu sesji: %s", exc)

    with sync_playwright() as p:
        run_t0 = None
        try:
            try:
                browser = p.chromium.connect_over_cdp(cdp_url)
            except Exception as conn_err:
                err_s = str(conn_err)
                if "ECONNREFUSED" in err_s or "ConnectError" in err_s:
                    raise RuntimeError(
                        f"Brak nasłuchu CDP pod {cdp_url}. Uruchom Brave lub Chrome "
                        f"z flagą --remote-debugging-port={port} (osobna instancja z Terminala — "
                        f"ikona z Docka zwykle NIE ma CDP), potem otwórz wykres TradingView w tej sesji. "
                        f"Test: curl -sS http://127.0.0.1:{port}/json/version | head -c 200"
                    ) from conn_err
                raise
            default_context = browser.contexts[0]

            def _find_tv_page():
                try:
                    for page in default_context.pages:
                        try:
                            if "tradingview.com" in (page.url or ""):
                                return page
                            if "TradingView" in (page.title() or ""):
                                return page
                        except Exception:
                            continue
                except Exception:
                    pass
                return None

            target_page = _find_tv_page()
            if target_page is None:
                logger.info(
                    "Karta TradingView jeszcze niewidoczna — czekam aż Brave/Chrome wstanie…"
                )
                deadline = time.time() + 12.0
                while time.time() < deadline:
                    target_page = _find_tv_page()
                    if target_page is not None:
                        break
                    time.sleep(0.5)

            if target_page is None:
                logger.info(
                    "Brak karty TV — otwieram ją sam (https://www.tradingview.com/chart/)…"
                )
                try:
                    target_page = default_context.new_page()
                    target_page.goto(
                        "https://www.tradingview.com/chart/",
                        wait_until="domcontentloaded",
                        timeout=45000,
                    )
                    try:
                        target_page.wait_for_load_state("load", timeout=20000)
                    except Exception:
                        pass
                except Exception as open_err:
                    raise RuntimeError(
                        f"Nie znaleziono otwartej karty TradingView w przeglądarce podpiętej pod port {port}, "
                        f"a próba otwarcia nowej karty zawiodła: {open_err}"
                    )

            target_page.on("dialog", lambda dialog: dialog.accept())

            logger.info("Podłączono do karty: %s", target_page.title())
            target_page.bring_to_front()
            run_t0 = time.perf_counter()

            logger.info("Czyszczę wykres ze starych wskaźników przed pomiarem...")
            try:
                options_btn = target_page.locator(
                    'button[aria-label="Usuń opcje"], button[aria-label="Remove options"]'
                )
                if options_btn.count() > 0:
                    options_btn.first.click(force=True)
                    time.sleep(SLEEP_AFTER_SMALL_ACTION_S)
                    menu_items = target_page.locator('[data-role="menuitem"]').all()
                    for el in menu_items:
                        text = el.inner_text().strip()
                        if (
                            re.search(
                                r"Usuń.*wskaźnik|Remove.*indicator",
                                text,
                                re.IGNORECASE,
                            )
                            and "rysun" not in text.lower()
                            and "drawing" not in text.lower()
                        ):
                            el.click(force=True)
                            time.sleep(SLEEP_AFTER_SMALL_ACTION_S)
                            break
            except Exception as e:
                logger.warning("Nie powiodło się pełne czyszczenie ekranu: %s", e)

            if not indicators:
                logger.warning("Lista wskaźników jest pusta — przerwano.")
                elapsed = time.perf_counter() - run_t0
                dh = _format_duration(elapsed)
                write_scraper_status(
                    "done",
                    "0/0",
                    "",
                    "",
                    duration_seconds=elapsed,
                    duration_human=dh,
                )
                return

            n_inds = len(indicators)

            for ind_idx, ind_name in enumerate(indicators):
                logger.info(
                    "=== Faza wskaźnika: %s (%d/%d) ===",
                    ind_name,
                    ind_idx + 1,
                    n_inds,
                )
                logger.info("Dodaję wskaźnik na wykres (raz na fazę): %s", ind_name)
                add_indicator_to_chart(target_page, ind_name, ind_name)

                for ticker_idx, ticker in enumerate(tickers):
                    write_scraper_status(
                        "running",
                        f"{ticker_idx + 1}/{len(tickers)} · wsk. {ind_idx + 1}/{n_inds}",
                        ticker,
                    )

                    existing_df = load_results_dataframe(current_run_file)

                    if not is_partial and ticker_fully_done_in_csv(
                        existing_df, ticker, intervals, indicators
                    ):
                        logger.info(
                            "Pomijam %s — na dziś w CSV są już wszystkie wymagane dane (lub SKIPPED).",
                            ticker,
                        )
                        for interval in intervals:
                            update_state(ticker, interval)
                        continue

                    all_done_for_ticker = all(
                        (ticker, interval) in processed_combos for interval in intervals
                    )
                    if all_done_for_ticker:
                        logger.info(
                            "Pomijam cały ticker %s — wszystkie interwały oznaczone w stanie sesji.",
                            ticker,
                        )
                        continue

                    logger.info("Przełączam na ticker: %s", ticker)
                    target_page.locator("body").click(force=True)
                    time.sleep(SLEEP_AFTER_MICRO_ACTION_S)
                    target_page.keyboard.type(ticker, delay=100)
                    time.sleep(SLEEP_AFTER_SMALL_ACTION_S)
                    symbol_search_info = read_symbol_search_modal_company_info(
                        target_page, ticker
                    )
                    symbol_search_name = symbol_search_info.get("name", "")
                    symbol_search_exchange = symbol_search_info.get("exchange", "")
                    if symbol_search_name:
                        logger.debug(
                            "Symbol search: %s → %s (giełda: %s)",
                            ticker,
                            symbol_search_name,
                            symbol_search_exchange or "?",
                        )
                    target_page.keyboard.press("Enter")
                    time.sleep(SLEEP_AFTER_TICKER_ENTER_S)

                    try:
                        search_box = target_page.locator('input[type="search"]')
                        if search_box.count() > 0 and search_box.first.is_visible():
                            logger.warning(
                                "Ticker %s nie znaleziony (okno wyszukiwania wciąż otwarte). Pomijam.",
                                ticker,
                            )
                            target_page.keyboard.press("Escape")
                            time.sleep(SLEEP_AFTER_SMALL_ACTION_S)
                            record_skipped_ticker(
                                current_run_file,
                                ticker,
                                "Nie znaleziono w wyszukiwarce (brak dopasowania lub zły format)",
                            )
                            for interval in intervals:
                                update_state(ticker, interval)
                            continue
                    except Exception:
                        pass

                    company_name = "Nieznana"
                    exchange = ""
                    current_price = ""
                    try:
                        title_text = target_page.title()
                        if (
                            "Błędny symbol" in title_text
                            or "Invalid symbol" in title_text
                            or "Nie znaleziono" in title_text
                        ):
                            logger.warning("Ticker %s nie istnieje. Pomijam...", ticker)
                            record_skipped_ticker(
                                current_run_file,
                                ticker,
                                "Błędny symbol / nie znaleziono na TradingView",
                            )
                            for interval in intervals:
                                update_state(ticker, interval)
                            continue

                        legend_desc = ""
                        try:
                            legend_desc = target_page.locator(
                                'div[data-name="legend-source-description"]'
                            ).first.inner_text(timeout=2000)
                        except Exception:
                            pass

                        header_blob = read_chart_symbol_header_blob(
                            target_page, ticker
                        )
                        company_name = resolve_company_name(
                            title_text,
                            legend_desc,
                            ticker,
                            header_blob,
                            symbol_search_name,
                        )
                        exchange = resolve_exchange(
                            ticker,
                            symbol_search_exchange=symbol_search_exchange,
                            header_blob=" ".join(
                                filter(
                                    None,
                                    [header_blob, title_text, legend_desc],
                                )
                            ),
                        )

                        title_clean = (
                            title_text.split(" Wskaźnik")[0]
                            .split(" Wykres")[0]
                            .split(" —")[0]
                            .split(" -")[0]
                            .strip()
                        )
                        match_price = re.search(
                            r"\s+(\d+[\.,]\d+|\d+)", title_clean
                        )
                        if match_price:
                            current_price = match_price.group(1)
                    except Exception as e:
                        raise RuntimeError(
                            f"Błąd podczas pobierania danych dla {ticker}: {e}"
                        )

                    logger.info(
                        "(Spółka: %s | Giełda: %s | Cena: %s)",
                        company_name,
                        exchange or "?",
                        current_price,
                    )

                    is_last_indicator = ind_idx == n_inds - 1

                    for interval in intervals:
                        existing_df = load_results_dataframe(current_run_file)
                        erow = get_row_for_ticker_interval(
                            existing_df, ticker, interval
                        )

                        if (
                            not is_partial
                            and erow is not None
                            and row_interval_complete(erow, indicators)
                        ):
                            logger.info(
                                "Pomijam %s - %s — w CSV jest już komplet wskaźników.",
                                ticker,
                                interval,
                            )
                            update_state(ticker, interval)
                            continue

                        if (ticker, interval) in processed_combos:
                            if erow is not None and not row_interval_complete(
                                erow, indicators
                            ):
                                logger.info(
                                    "Sesja wskazywała na %s/%s, CSV niepełny — ponawiam pomiar.",
                                    ticker,
                                    interval,
                                )
                            elif erow is None:
                                logger.info(
                                    "Sesja wskazywała na %s/%s, brak wiersza w CSV — ponawiam.",
                                    ticker,
                                    interval,
                                )
                            else:
                                logger.info(
                                    "Pomijam %s - %s (wznów + CSV OK).",
                                    ticker,
                                    interval,
                                )
                                continue

                        logger.info("Ustawiam interwał: %s", interval)
                        target_page.keyboard.type(interval, delay=100)
                        time.sleep(SLEEP_AFTER_SMALL_ACTION_S)
                        target_page.keyboard.press("Enter")
                        time.sleep(SLEEP_AFTER_INTERVAL_CHANGE_S)

                        row_data = {
                            "Ticker": ticker,
                            "Company_Name": company_name,
                            "Exchange": exchange,
                            "Current_Price": current_price,
                            "Interval": interval,
                            "Scrape_Status": "",
                            "Scrape_Error": "",
                        }
                        merge_existing_row_into_row_data(row_data, erow)

                        if erow is not None and row_has_indicator_data(
                            erow, ind_name
                        ):
                            logger.info(
                                "Pomijam wskaźnik %s — już zapisany w CSV dla %s/%s",
                                ind_name,
                                ticker,
                                interval,
                            )
                        else:
                            logger.info(
                                "Odczyt HTML dla wskaźnika: %s (legenda + %ss)",
                                ind_name,
                                SLEEP_AFTER_INDICATOR_COMPUTE_S,
                            )
                            _wait_for_legend_indicator_ready(
                                target_page,
                                ind_name,
                                max_attempts=5,
                                delay_s=1.0,
                            )
                            time.sleep(SLEEP_AFTER_INDICATOR_COMPUTE_S)
                            _ensure_legend_expanded(target_page)
                            html_content = target_page.content()
                            indicator_data = parse_indicators(
                                html_content, [ind_name]
                            )
                            for key, val in indicator_data.items():
                                if (
                                    key == "PCA_Value"
                                    or key == "PCA_Color"
                                    or key.startswith(ind_name)
                                ):
                                    if (
                                        key != f"{ind_name}_Values"
                                        or ind_name != "PCA"
                                    ):
                                        logger.debug("[%s]: %s", key, val)
                                    row_data[key] = val

                        if is_last_indicator:
                            apply_final_scrape_status(row_data, indicators)
                            save_results_row(current_run_file, row_data)
                            update_state(ticker, interval)
                        else:
                            row_data["Scrape_Status"] = ""
                            row_data["Scrape_Error"] = ""
                            save_results_row(current_run_file, row_data)

                remove_active_indicator(target_page, ind_name, "faza")

            elapsed = time.perf_counter() - run_t0
            dh = _format_duration(elapsed)
            logger.info(
                "Zakończono pełny przebieg w %s (%.2fs). Pobrane dane są w: %s",
                dh,
                elapsed,
                current_run_file,
            )
            write_scraper_status(
                "done",
                f"{len(tickers)}/{len(tickers)} · wsk. {len(indicators)}/{len(indicators)}",
                "",
                "",
                duration_seconds=elapsed,
                duration_human=dh,
            )
            if not is_partial and os.path.exists(state_file):
                os.remove(state_file)

        except Exception as e:
            elapsed = None
            if run_t0 is not None:
                elapsed = time.perf_counter() - run_t0
            logger.error("Błąd podczas scrapowania: %s", e)
            write_scraper_status(
                "error",
                "",
                "",
                str(e),
                duration_seconds=elapsed,
                duration_human=_format_duration(elapsed) if elapsed is not None else None,
            )
            raise


if __name__ == "__main__":
    _configure_logging()
    parser = argparse.ArgumentParser(description="TradingView Web Scraper")
    parser.add_argument(
        "--ticker",
        type=str,
        help="Comma-separated tickers to run (e.g., PLTR,FCX)",
    )
    parser.add_argument(
        "--interval",
        type=str,
        help="Specify a single interval to run (e.g., 1D)",
    )
    parser.add_argument(
        "--indicator",
        type=str,
        help="Specify a single indicator to run (e.g., PCA)",
    )
    parser.add_argument(
        "--cdp-port",
        type=int,
        default=None,
        metavar="PORT",
        help="Port zdalnego debugowania przeglądarki (domyślnie: TV_CDP_PORT, potem cdp_port z JSON, 9222)",
    )
    args = parser.parse_args()

    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r") as f:
            config = json.load(f)
        TICKERS = config.get("tickers", [])
        INTERVALS = config.get("intervals", ["1D", "1W", "1M"])
        INDICATORS = config.get("indicators", ["PCA", "HTS Panel", "MacD"])
    else:
        logger.warning("Config file %s not found, using defaults.", CONFIG_FILE)
        config = {}
        TICKERS = ["FCX", "PLTR"]
        INTERVALS = ["1D", "1W", "1M"]
        INDICATORS = ["PCA", "HTS Panel", "MacD"]

    cdp_port = resolve_cdp_port(config, cli_port=args.cdp_port)

    is_partial = False
    if args.ticker:
        TICKERS = [t.strip() for t in args.ticker.split(",")]
        is_partial = True
    if args.interval:
        INTERVALS = [args.interval]
        is_partial = True
    if args.indicator:
        INDICATORS = [args.indicator]
        is_partial = True

    write_scraper_status("running", "0/" + str(len(TICKERS)), "")
    try:
        run_scraper(TICKERS, INTERVALS, INDICATORS, port=cdp_port, is_partial=is_partial)
    except Exception:
        # Błąd i ewentualny czas do `scraper_status.json` zapisuje już `run_scraper`.
        raise
