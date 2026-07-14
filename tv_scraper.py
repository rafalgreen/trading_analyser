import time
import re
import os
import json
import argparse
import logging
import urllib.request
from datetime import datetime
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

import pandas as pd
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup

from results_store import (
    CSV_META_COLUMNS,
    ResultsBuffer,
    apply_final_scrape_status,
    cell_nonempty,
    ensure_meta_columns as _ensure_meta_columns,
    get_row_for_ticker_interval,
    load_results_dataframe,
    merge_existing_row_into_row_data,
    merge_indicator_into_row,
    order_result_columns as _order_result_columns,
    row_has_indicator_data,
    row_interval_complete,
    save_fundamentals_row,
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


def is_tradingview_chart_url(url: str) -> bool:
    """Czy URL wygląda na kartę wykresu / aplikacji TradingView (do reuse przez CDP)."""
    u = (url or "").strip().lower()
    if "tradingview.com" not in u:
        return False
    if "/chart" in u:
        return True
    if re.search(
        r"tradingview\.com/(?:symbols?|watchlists|markets|ideas|script)",
        u,
    ):
        return True
    # Dowolna inna podstrona tradingview.com (zgodnie z dawnym _find_tv_page)
    return bool(re.search(r"https?://(?:[\w.-]+\.)?tradingview\.com/", u))


def _target_url(target: Any) -> str:
    if isinstance(target, dict):
        return str(target.get("url") or "")
    return str(getattr(target, "url", None) or "")


def _target_title(target: Any) -> str:
    if isinstance(target, dict):
        return str(target.get("title") or "")
    try:
        return str(getattr(target, "title", lambda: "")() or "")
    except Exception:
        return ""


def pick_tradingview_chart_page(
    targets: Iterable[Any],
) -> Optional[Any]:
    """Wybiera najlepszą kartę TradingView z listy stron Playwright lub wpisów ``/json/list``.

    Priorytet: URL z ``/chart``, potem inne ``tradingview.com``, na końcu tytuł
    zawierający „TradingView”.
    """
    chart_hits: List[Any] = []
    other_tv: List[Any] = []
    title_hits: List[Any] = []
    for target in targets:
        url = _target_url(target)
        if is_tradingview_chart_url(url):
            if "/chart" in url.lower():
                chart_hits.append(target)
            else:
                other_tv.append(target)
            continue
        if "TradingView" in _target_title(target):
            title_hits.append(target)
    if chart_hits:
        return chart_hits[0]
    if other_tv:
        return other_tv[0]
    if title_hits:
        return title_hits[0]
    return None


def cdp_list_targets(
    port: int, host: str = "127.0.0.1", timeout_s: float = 2.0
) -> List[Dict[str, Any]]:
    """Zwraca listę celów CDP (karty, service workers…) z ``/json/list``."""
    url = f"http://{host}:{int(port)}/json/list"
    with urllib.request.urlopen(url, timeout=timeout_s) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data if isinstance(data, list) else []


def cdp_find_tradingview_chart_url(
    port: int, host: str = "127.0.0.1", timeout_s: float = 2.0
) -> Optional[str]:
    """URL pierwszej karty typu ``page`` z wykresem TradingView, albo ``None``."""
    pages = [
        t
        for t in cdp_list_targets(port, host=host, timeout_s=timeout_s)
        if str(t.get("type") or "") == "page"
    ]
    picked = pick_tradingview_chart_page(pages)
    if picked is None:
        return None
    url = _target_url(picked)
    return url or None


SLEEP_AFTER_INDICATOR_MODAL_S = 2
SLEEP_AFTER_INDICATOR_QUERY_S = 3
SLEEP_AFTER_INDICATOR_COMPUTE_S = 4
SLEEP_AFTER_TICKER_ENTER_S = 3
SLEEP_AFTER_INTERVAL_CHANGE_S = 2
SLEEP_AFTER_SMALL_ACTION_S = 1
SLEEP_AFTER_MICRO_ACTION_S = 0.5
SYMBOL_SEARCH_LIST_WAIT_MS = 4500

_NORMAL_TIMINGS = {
    "indicator_modal": 2.0,
    "indicator_query": 3.0,
    "indicator_compute": 4.0,
    "ticker_enter": 3.0,
    "interval_change": 2.0,
    "interval_settle": 0.5,
    "small_action": 1.0,
    "micro_action": 0.5,
}

_FAST_TIMINGS = {
    "indicator_modal": 1.5,
    "indicator_query": 2.0,
    "indicator_compute": 2.0,
    "ticker_enter": 1.5,
    "interval_change": 2.0,
    "interval_settle": 0.3,
    "small_action": 0.5,
    "micro_action": 0.25,
}


class ScraperPerformance:
    """Konfiguracja opóźnień scrapera (normal / fast) i trybu pętli."""

    def __init__(self, cfg: Optional[Dict[str, Any]] = None) -> None:
        cfg = cfg or {}
        mode = str(cfg.get("mode", "normal")).strip().lower()
        base = _FAST_TIMINGS if mode == "fast" else _NORMAL_TIMINGS
        self.mode = mode
        self.loop_mode = str(cfg.get("loop_mode", "indicator_first")).strip().lower()
        self.keyboard_delay_ms = int(
            cfg.get("keyboard_delay_ms", 30 if mode == "fast" else 100)
        )
        self.max_compute_wait_s = float(cfg.get("max_compute_wait_s", 6.0))
        self.min_compute_wait_s = float(cfg.get("min_compute_wait_s", 0.5))
        self.poll_interval_s = float(cfg.get("poll_interval_s", 0.2))
        self.symbol_search_wait_ms = int(
            cfg.get("symbol_search_wait_ms", SYMBOL_SEARCH_LIST_WAIT_MS)
        )
        raw_max = cfg.get("max_indicators_on_chart", 2)
        try:
            self.max_indicators_on_chart = max(1, int(raw_max))
        except (TypeError, ValueError):
            self.max_indicators_on_chart = 2
        self.indicator_modal_s = float(base["indicator_modal"])
        self.indicator_query_s = float(base["indicator_query"])
        self.indicator_compute_s = float(base["indicator_compute"])
        self.ticker_enter_s = float(base["ticker_enter"])
        self.interval_change_s = float(
            cfg.get("interval_change_s", base["interval_change"])
        )
        self.interval_settle_s = float(
            cfg.get("interval_settle_s", base["interval_settle"])
        )
        default_settle_active = 0.1 if mode == "fast" else 0.3
        self.interval_settle_active_s = float(
            cfg.get("interval_settle_active_s", default_settle_active)
        )
        self.small_action_s = float(base["small_action"])
        self.micro_action_s = float(base["micro_action"])

    def apply_to_module_globals(self) -> None:
        """Synchronizuje globalne stałe dla wstecznej kompatybilności testów."""
        global SLEEP_AFTER_INDICATOR_MODAL_S
        global SLEEP_AFTER_INDICATOR_QUERY_S
        global SLEEP_AFTER_INDICATOR_COMPUTE_S
        global SLEEP_AFTER_TICKER_ENTER_S
        global SLEEP_AFTER_INTERVAL_CHANGE_S
        global SLEEP_AFTER_SMALL_ACTION_S
        global SLEEP_AFTER_MICRO_ACTION_S
        global SYMBOL_SEARCH_LIST_WAIT_MS
        SLEEP_AFTER_INDICATOR_MODAL_S = self.indicator_modal_s
        SLEEP_AFTER_INDICATOR_QUERY_S = self.indicator_query_s
        SLEEP_AFTER_INDICATOR_COMPUTE_S = self.indicator_compute_s
        SLEEP_AFTER_TICKER_ENTER_S = self.ticker_enter_s
        SLEEP_AFTER_INTERVAL_CHANGE_S = self.interval_change_s
        SLEEP_AFTER_SMALL_ACTION_S = self.small_action_s
        SLEEP_AFTER_MICRO_ACTION_S = self.micro_action_s
        SYMBOL_SEARCH_LIST_WAIT_MS = self.symbol_search_wait_ms


_SCRAPER_PERF = ScraperPerformance()


def load_scraper_performance_config() -> Dict[str, Any]:
    if not os.path.exists(CONFIG_FILE):
        return {}
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            cfg = json.load(f) or {}
        perf = cfg.get("scraper_performance")
        return perf if isinstance(perf, dict) else {}
    except Exception:
        return {}


def init_scraper_performance(cfg: Optional[Dict[str, Any]] = None) -> ScraperPerformance:
    global _SCRAPER_PERF
    _SCRAPER_PERF = ScraperPerformance(cfg or load_scraper_performance_config())
    _SCRAPER_PERF.apply_to_module_globals()
    return _SCRAPER_PERF


def scraper_perf() -> ScraperPerformance:
    return _SCRAPER_PERF


def _adaptive_wait(
    predicate: Callable[[], bool],
    *,
    min_wait_s: float,
    max_wait_s: float,
    poll_s: Optional[float] = None,
) -> bool:
    """Polling do max_wait_s; min_wait_s tylko gdy pierwszy check nie gotowy."""
    poll = poll_s if poll_s is not None else scraper_perf().poll_interval_s
    try:
        if predicate():
            return True
    except Exception:
        pass
    time.sleep(max(0.0, min_wait_s))
    deadline = time.time() + max(0.0, max_wait_s - min_wait_s)
    while time.time() < deadline:
        try:
            if predicate():
                return True
        except Exception:
            pass
        time.sleep(poll)
    try:
        return predicate()
    except Exception:
        return False


def _wait_for_ticker_loaded(target_page, ticker: str) -> bool:
    """Czeka aż wykres załaduje ticker (symbol search zniknie / tytuł się zmieni)."""
    perf = scraper_perf()
    bare = (ticker or "").split(":")[-1].strip().upper()
    full = (ticker or "").strip().upper()

    def _loaded() -> bool:
        try:
            search = target_page.locator('input[type="search"]')
            if search.count() > 0 and search.first.is_visible(timeout=150):
                return False
        except Exception:
            pass
        try:
            title = (target_page.title() or "").upper()
            if "BŁĘDNY SYMBOL" in title or "INVALID SYMBOL" in title:
                return True
            if bare and bare in title:
                return True
            if full and full in title:
                return True
        except Exception:
            pass
        return False

    return _adaptive_wait(
        _loaded,
        min_wait_s=perf.min_compute_wait_s,
        max_wait_s=perf.ticker_enter_s,
    )


def _interval_button_is_active(target_page, interval: str) -> bool:
    """True gdy interwał jest zaznaczony w toolbarze (nie tylko obecny w DOM)."""
    iv = (interval or "").strip()
    if not iv:
        return False
    active_selectors = [
        f'button[data-value="{iv}"][aria-checked="true"]',
        f'button[data-value="{iv}"][aria-pressed="true"]',
        f'button[data-value="{iv}"][data-active="true"]',
        f'button[data-value="{iv}"].isActive',
        f'button[data-value="{iv}"].active',
        f'button[aria-label*="{iv}"][aria-checked="true"]',
        f'button[aria-label*="{iv}"][aria-pressed="true"]',
    ]
    for selector in active_selectors:
        try:
            btn = target_page.locator(selector)
            if btn.count() > 0:
                return True
        except Exception:
            pass
    try:
        btn = target_page.locator(f'button[data-value="{iv}"]')
        if btn.count() > 0:
            cls = (btn.first.get_attribute("class") or "").lower()
            if any(token in cls for token in ("active", "selected", "isactive")):
                return True
            aria = (
                (btn.first.get_attribute("aria-checked") or "")
                + (btn.first.get_attribute("aria-pressed") or "")
            ).lower()
            if aria in ("true", "1"):
                return True
    except Exception:
        pass
    return False


def _wait_for_interval_loaded(target_page, interval: str) -> bool:
    """Czeka aż toolbar potwierdzi aktywny interwał, potem krótki settle."""
    perf = scraper_perf()

    def _loaded() -> bool:
        return _interval_button_is_active(target_page, interval)

    loaded = _adaptive_wait(
        _loaded,
        min_wait_s=perf.min_compute_wait_s,
        max_wait_s=perf.interval_change_s,
    )
    if loaded:
        time.sleep(perf.interval_settle_s)
    else:
        logger.warning(
            "Interwał %s: nie potwierdzono aktywnego przycisku — kontynuuję po settle.",
            interval,
        )
        time.sleep(perf.interval_settle_s)
    return loaded


def _legend_item_value_texts(item_locator) -> List[str]:
    """Teksty valueValue z bloku legendy (locator, bez page.content())."""
    texts: List[str] = []
    try:
        values_root = item_locator.locator('[data-qa-id="legend-source-values"]')
        if values_root.count() == 0:
            return texts
        value_nodes = values_root.locator(".valueValue")
        n = value_nodes.count()
        for i in range(min(n, 20)):
            try:
                t = (value_nodes.nth(i).inner_text(timeout=500) or "").strip()
                if t:
                    texts.append(t)
            except Exception:
                pass
    except Exception:
        pass
    return texts


def _legend_value_text_nonempty(text: str) -> bool:
    if not cell_nonempty(text):
        return False
    low = text.lower()
    return (
        "brak danych na wykresie" not in low
        and "brak poprawnych danych" not in low
    )


def _legend_has_nonempty_values_locator(target_page, ind_name: str) -> bool:
    """Czy legenda wskaźnika ma niepuste wartości — poll przez locatory (bez HTML dump)."""
    try:
        items = target_page.locator('[data-qa-id="legend-source-item"]')
        n = items.count()
        for i in range(min(n, 40)):
            item = items.nth(i)
            tw = item.locator(
                '[data-qa-id="title-wrapper legend-source-title"]'
            )
            if tw.count() == 0:
                continue
            title = tw.inner_text(timeout=800)
            if not indicator_title_matches(title, ind_name):
                continue
            texts = _legend_item_value_texts(item)
            return any(_legend_value_text_nonempty(t) for t in texts)
    except Exception:
        return False
    return False


def _legend_has_nonempty_values(target_page, ind_name: str) -> bool:
    """Czy legenda wskaźnika ma niepuste wartości (locator, fallback HTML parse)."""
    if _legend_has_nonempty_values_locator(target_page, ind_name):
        return True
    try:
        html = target_page.content()
        data = parse_indicators(html, [ind_name])
        if ind_name == "PCA":
            return cell_nonempty(data.get("PCA_Value"))
        if (ind_name or "").strip().lower() == "macd":
            return cell_nonempty(data.get("MacD_Line"))
        vals = data.get(f"{ind_name}_Values") or ""
        if not cell_nonempty(vals):
            return False
        low = str(vals).lower()
        return "brak danych na wykresie" not in low and "brak poprawnych danych" not in low
    except Exception:
        return False


def _wait_for_legend_values(target_page, ind_name: str) -> bool:
    """Czeka na widoczną legendę z niepustymi wartościami wskaźnika."""
    perf = scraper_perf()

    def _ready() -> bool:
        return _legend_has_nonempty_values_locator(target_page, ind_name)

    return _adaptive_wait(
        _ready,
        min_wait_s=perf.min_compute_wait_s,
        max_wait_s=perf.max_compute_wait_s,
    )


def _wait_compute_for_indicator(target_page, ind_name: str) -> bool:
    """Adaptive wait zamiast stałego sleep po zmianie tickera/interwału."""
    return _wait_for_legend_values(target_page, ind_name)


def _wait_compute_for_indicators(
    target_page, indicators: List[str]
) -> bool:
    """Czeka aż wszystkie wskaźniki w partii mają niepuste wartości w legendzie."""
    if not indicators:
        return True
    perf = scraper_perf()

    def _all_ready() -> bool:
        return all(
            _legend_has_nonempty_values_locator(target_page, ind_name)
            for ind_name in indicators
        )

    return _adaptive_wait(
        _all_ready,
        min_wait_s=perf.min_compute_wait_s,
        max_wait_s=perf.max_compute_wait_s,
    )


def _switch_chart_interval(target_page, interval: str) -> float:
    """Ustawia interwał wykresu; pomija wpisywanie gdy już aktywny. Zwraca czas w s."""
    perf = scraper_perf()
    t0 = time.perf_counter()
    if _interval_button_is_active(target_page, interval):
        logger.debug("Interwał %s już aktywny — pomijam wpisywanie.", interval)
        time.sleep(perf.interval_settle_active_s)
    else:
        logger.info("Ustawiam interwał: %s", interval)
        target_page.keyboard.type(interval, delay=perf.keyboard_delay_ms)
        time.sleep(perf.small_action_s)
        target_page.keyboard.press("Enter")
        _wait_for_interval_loaded(target_page, interval)
    return time.perf_counter() - t0

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
        items = page.locator('div[data-role="list-item"]:visible')
        if items.count() == 0:
            page.wait_for_selector(
                'div[data-role="list-item"]:visible',
                timeout=scraper_perf().symbol_search_wait_ms,
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


def scraper_overall_progress(
    ticker_idx: int,
    n_tickers: int,
    ind_idx: int,
    n_inds: int,
) -> Tuple[int, int]:
    """Monotoniczny licznik kroków scrapera (ticker × faza wskaźnika)."""
    n_tickers = max(int(n_tickers), 1)
    n_inds = max(int(n_inds), 1)
    ticker_idx = max(int(ticker_idx), 0)
    ind_idx = max(int(ind_idx), 0)
    overall_done = ind_idx * n_tickers + ticker_idx + 1
    overall_total = n_tickers * n_inds
    return overall_done, overall_total


def format_scraper_eta_label(eta_seconds: float) -> str:
    """Czytelna etykieta ETA (np. ``< 1 min``, ``~12 min``, ``~1h 05m``)."""
    if eta_seconds < 60:
        return "< 1 min"
    total_min = int(round(eta_seconds / 60))
    if total_min < 60:
        return f"~{total_min} min"
    h, m = divmod(total_min, 60)
    return f"~{h}h {m:02d}m"


def compute_scraper_eta(
    overall_done: int,
    overall_total: int,
    elapsed_seconds: float,
    *,
    min_steps: int = 10,
) -> Tuple[Optional[float], str, str]:
    """Szacuje pozostały czas i całość na podstawie średniego czasu na krok."""
    overall_done = max(int(overall_done), 0)
    overall_total = max(int(overall_total), 1)
    elapsed_seconds = max(float(elapsed_seconds or 0.0), 0.0)
    if overall_done < min_steps:
        return None, "szacowanie…", ""
    if overall_done >= overall_total:
        return 0.0, "", format_scraper_eta_label(elapsed_seconds)
    remaining = overall_total - overall_done
    avg_per_step = elapsed_seconds / overall_done
    eta_seconds = avg_per_step * remaining
    total_seconds = elapsed_seconds + eta_seconds
    return (
        eta_seconds,
        format_scraper_eta_label(eta_seconds),
        format_scraper_eta_label(total_seconds),
    )


def compute_scraper_eta_segment(
    overall_done: int,
    overall_total: int,
    elapsed_seconds: float,
    *,
    baseline_done: int = 0,
    baseline_elapsed_s: float = 0.0,
    min_steps: int = 10,
) -> Tuple[Optional[float], str, str]:
    """ETA z tempa bieżącego segmentu (po Stop/wznowieniu), całość z pełnego elapsed."""
    segment_done = max(int(overall_done) - int(baseline_done), 0)
    segment_total = max(int(overall_total) - int(baseline_done), 1)
    segment_elapsed = max(
        float(elapsed_seconds) - float(baseline_elapsed_s or 0.0), 0.0
    )
    eta_seconds, eta_label, _ = compute_scraper_eta(
        segment_done, segment_total, segment_elapsed, min_steps=min_steps
    )
    if eta_seconds is None:
        return None, eta_label, ""
    total_seconds = max(float(elapsed_seconds), 0.0) + eta_seconds
    return eta_seconds, eta_label, format_scraper_eta_label(total_seconds)


def _format_scraper_progress(
    ticker_idx: int,
    n_tickers: int,
    ind_idx: int,
    n_inds: int,
    ind_name: str = "",
    eta_label: str = "",
    resumed: bool = False,
) -> str:
    """Postęp łączny (monotoniczny) + szczegóły fazy wskaźnika i tickera."""
    n_tickers = max(int(n_tickers), 1)
    n_inds = max(int(n_inds), 1)
    ticker_idx = max(int(ticker_idx), 0)
    ind_idx = max(int(ind_idx), 0)
    overall_done, overall_total = scraper_overall_progress(
        ticker_idx, n_tickers, ind_idx, n_inds
    )
    parts = [
        f"{overall_done}/{overall_total}",
        f"ticker {ticker_idx + 1}/{n_tickers}",
        f"wsk. {ind_idx + 1}/{n_inds}",
    ]
    if resumed:
        parts.append("(wznowiono)")
    if ind_name:
        parts.append(str(ind_name))
    if eta_label:
        parts.append(eta_label)
    return " · ".join(parts)


def _write_run_state_file(
    state_file: str,
    *,
    current_run_file: str,
    processed_combos,
    session_started_at: Optional[float],
    active_elapsed_s: Optional[float] = None,
    ticker_idx: int,
    ind_idx: int,
    tickers: List[str],
    indicators: List[str],
    no_data_only: bool,
    resumed: bool = False,
    interval_idx: Optional[int] = None,
    eta_baseline_done: Optional[int] = None,
    eta_baseline_elapsed_s: Optional[float] = None,
) -> None:
    payload: Dict[str, Any] = {
        "current_file": current_run_file,
        "processed": [list(x) for x in processed_combos],
        "session_started_at": session_started_at,
        "active_elapsed_s": round(float(active_elapsed_s or 0.0), 3),
        "ticker_idx": max(int(ticker_idx), 0),
        "ind_idx": max(int(ind_idx), 0),
        "tickers": list(tickers or []),
        "indicators": list(indicators or []),
        "no_data_only": bool(no_data_only),
    }
    if resumed:
        payload["resumed"] = True
    if interval_idx is not None:
        payload["interval_idx"] = max(int(interval_idx), 0)
    if eta_baseline_done is not None:
        payload["eta_baseline_done"] = max(int(eta_baseline_done), 0)
    if eta_baseline_elapsed_s is not None:
        payload["eta_baseline_elapsed_s"] = round(float(eta_baseline_elapsed_s), 3)
    with open(state_file, "w", encoding="utf-8") as f:
        json.dump(payload, f)


def _load_run_state_file(
    state_file: str,
) -> Optional[Dict[str, Any]]:
    if not os.path.exists(state_file):
        return None
    try:
        with open(state_file, "r", encoding="utf-8") as f:
            state = json.load(f) or {}
    except Exception:
        return None
    if not str(state.get("current_file") or "").strip():
        return None
    return state


def _parse_progress_checkpoint(
    progress: str,
) -> Tuple[Optional[int], Optional[int], Optional[int]]:
    """Parsuje ticker_idx, ind_idx (0-based) i opcjonalnie interval_idx z postępu statusu."""
    if not progress or not isinstance(progress, str):
        return None, None, None

    interval_idx: Optional[int] = None
    m_iv = re.search(r" · (1D|1W|1M)(?: · |$)", progress, flags=re.IGNORECASE)
    if m_iv:
        interval_idx = {"1D": 0, "1W": 1, "1M": 2}.get(m_iv.group(1).upper())

    m_partia = re.search(
        r"ticker\s+(\d+)\s*/\s*\d+.*?partia\s+(\d+)\s*/\s*\d+",
        progress,
        flags=re.IGNORECASE,
    )
    if m_partia:
        try:
            return (
                max(int(m_partia.group(1)) - 1, 0),
                max(int(m_partia.group(2)) - 1, 0),
                interval_idx,
            )
        except (TypeError, ValueError):
            return None, None, interval_idx

    m_wsk = re.search(
        r"ticker\s+(\d+)\s*/\s*\d+.*?wsk\.\s*(\d+)\s*/\s*\d+",
        progress,
        flags=re.IGNORECASE,
    )
    if m_wsk:
        try:
            return (
                max(int(m_wsk.group(1)) - 1, 0),
                max(int(m_wsk.group(2)) - 1, 0),
                interval_idx,
            )
        except (TypeError, ValueError):
            return None, None, interval_idx

    return None, None, interval_idx


def _scraper_elapsed_seconds(
    active_elapsed_s: Optional[float] = None,
    run_t0: Optional[float] = None,
) -> float:
    """Czas aktywnego scrapowania (bez przerw między Stop a wznowieniem)."""
    base = max(float(active_elapsed_s or 0.0), 0.0)
    if run_t0 is not None:
        return base + max(0.0, time.perf_counter() - run_t0)
    return base


def format_scraper_eta_display(eta_label: str, eta_total_label: str = "") -> str:
    """Czytelna etykieta ETA dla UI (pozostało + opcjonalnie całość)."""
    if not eta_label or eta_label == "szacowanie…":
        return eta_label or "szacowanie…"
    if eta_total_label:
        return f"pozostało {eta_label} (całość {eta_total_label})"
    return f"pozostało {eta_label}"


def scraper_overall_progress_ticker_first(
    ticker_idx: int,
    n_tickers: int,
    batch_idx: int = 0,
    n_batches: int = 1,
    interval_idx: int = 0,
    n_intervals: int = 1,
) -> Tuple[int, int]:
    n_tickers = max(int(n_tickers), 1)
    n_batches = max(int(n_batches), 1)
    n_intervals = max(int(n_intervals), 1)
    ticker_idx = max(int(ticker_idx), 0)
    batch_idx = max(int(batch_idx), 0)
    interval_idx = max(int(interval_idx), 0)
    steps_per_batch = n_tickers * n_intervals
    overall_done = (
        batch_idx * steps_per_batch + ticker_idx * n_intervals + interval_idx + 1
    )
    overall_total = n_batches * steps_per_batch
    return overall_done, overall_total


def chunk_indicators(
    indicators: List[str], max_on_chart: int
) -> List[List[str]]:
    """Dzieli listę wskaźników na partie mieszczące się na wykresie (np. TV Free = 2)."""
    size = max(1, int(max_on_chart))
    items = [str(i).strip() for i in indicators if str(i).strip()]
    if not items:
        return []
    return [items[i : i + size] for i in range(0, len(items), size)]


def _format_scraper_progress_ticker_first(
    ticker_idx: int,
    n_tickers: int,
    interval_name: str = "",
    eta_label: str = "",
    eta_total_label: str = "",
    resumed: bool = False,
    batch_idx: int = 0,
    n_batches: int = 1,
    interval_idx: int = 0,
    n_intervals: int = 1,
    phase: str = "",
) -> str:
    done, total = scraper_overall_progress_ticker_first(
        ticker_idx,
        n_tickers,
        batch_idx,
        n_batches,
        interval_idx,
        n_intervals,
    )
    base = f"{done}/{total} · ticker {ticker_idx + 1}/{n_tickers}"
    if n_batches > 1:
        base += f" · partia {batch_idx + 1}/{n_batches}"
    if interval_name:
        base += f" · {interval_name}"
    if phase:
        base += f" · {phase}"
    if eta_label:
        base += f" · {format_scraper_eta_display(eta_label, eta_total_label)}"
    if resumed:
        base += " · wznowiono"
    return base


def _build_running_scraper_progress_ticker_first(
    ticker_idx: int,
    n_tickers: int,
    interval_name: str,
    *,
    active_elapsed_s: Optional[float] = None,
    run_t0: Optional[float] = None,
    resumed: bool = False,
    batch_idx: int = 0,
    n_batches: int = 1,
    interval_idx: int = 0,
    n_intervals: int = 1,
    phase: str = "",
    eta_baseline_done: int = 0,
    eta_baseline_elapsed_s: float = 0.0,
) -> Tuple[str, Optional[float], str, str]:
    overall_done, overall_total = scraper_overall_progress_ticker_first(
        ticker_idx,
        n_tickers,
        batch_idx,
        n_batches,
        interval_idx,
        n_intervals,
    )
    elapsed = _scraper_elapsed_seconds(active_elapsed_s, run_t0)
    if eta_baseline_done > 0 or eta_baseline_elapsed_s > 0:
        eta_seconds, eta_label, eta_total_label = compute_scraper_eta_segment(
            overall_done,
            overall_total,
            elapsed,
            baseline_done=eta_baseline_done,
            baseline_elapsed_s=eta_baseline_elapsed_s,
        )
    else:
        eta_seconds, eta_label, eta_total_label = compute_scraper_eta(
            overall_done, overall_total, elapsed
        )
    progress = _format_scraper_progress_ticker_first(
        ticker_idx,
        n_tickers,
        interval_name,
        eta_label=eta_label,
        eta_total_label=eta_total_label,
        resumed=resumed,
        batch_idx=batch_idx,
        n_batches=n_batches,
        interval_idx=interval_idx,
        n_intervals=n_intervals,
        phase=phase,
    )
    return progress, eta_seconds, eta_label, eta_total_label


def _build_running_scraper_progress(
    ticker_idx: int,
    n_tickers: int,
    ind_idx: int,
    n_inds: int,
    ind_name: str,
    *,
    active_elapsed_s: Optional[float] = None,
    run_t0: Optional[float] = None,
    resumed: bool = False,
    eta_baseline_done: int = 0,
    eta_baseline_elapsed_s: float = 0.0,
) -> Tuple[str, Optional[float], str, str]:
    overall_done, overall_total = scraper_overall_progress(
        ticker_idx, n_tickers, ind_idx, n_inds
    )
    elapsed = _scraper_elapsed_seconds(active_elapsed_s, run_t0)
    if eta_baseline_done > 0 or eta_baseline_elapsed_s > 0:
        eta_seconds, eta_label, eta_total_label = compute_scraper_eta_segment(
            overall_done,
            overall_total,
            elapsed,
            baseline_done=eta_baseline_done,
            baseline_elapsed_s=eta_baseline_elapsed_s,
        )
    else:
        eta_seconds, eta_label, eta_total_label = compute_scraper_eta(
            overall_done, overall_total, elapsed
        )
    progress = _format_scraper_progress(
        ticker_idx,
        n_tickers,
        ind_idx,
        n_inds,
        ind_name,
        eta_label=format_scraper_eta_display(eta_label, eta_total_label),
        resumed=resumed,
    )
    return progress, eta_seconds, eta_label, eta_total_label


def resolve_run_indicators(
    config_indicators: List[str],
    cli_indicators: Optional[str] = None,
    cli_indicator: Optional[str] = None,
) -> tuple[List[str], List[str], bool]:
    """Zwraca (indicators_to_run, all_config_indicators, is_indicator_subset)."""
    all_inds = [str(i).strip() for i in config_indicators or [] if str(i).strip()]
    if not all_inds:
        all_inds = ["PCA", "HTS Panel", "MacD"]

    raw = (cli_indicators or os.environ.get("TV_SCRAPER_INDICATORS") or "").strip()
    if not raw and cli_indicator:
        raw = str(cli_indicator).strip()

    if not raw:
        return all_inds, all_inds, False

    cfg_map = {i.casefold(): i for i in all_inds}
    selected: List[str] = []
    for part in raw.split(","):
        token = part.strip()
        if not token:
            continue
        canon = cfg_map.get(token.casefold())
        if not canon:
            raise ValueError(
                f"Nieznany wskaźnik: {token}. Dozwolone: {', '.join(all_inds)}."
            )
        if canon not in selected:
            selected.append(canon)
    if not selected:
        raise ValueError("Lista wskaźników nie może być pusta.")
    return selected, all_inds, len(selected) < len(all_inds) or selected != all_inds


def write_scraper_status(
    status,
    progress="",
    current_ticker="",
    error="",
    duration_seconds=None,
    duration_human=None,
    current_indicator="",
    eta_seconds=None,
    eta_label=None,
    eta_total_label=None,
):
    """Write scraper status to JSON file for web UI polling."""
    data = {
        "status": status,
        "progress": progress,
        "current_ticker": current_ticker,
        "current_indicator": current_indicator,
        "error": error,
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    if duration_seconds is not None:
        data["duration_seconds"] = round(float(duration_seconds), 2)
    if duration_human:
        data["duration_human"] = duration_human
    if eta_seconds is not None:
        data["eta_seconds"] = round(float(eta_seconds), 1)
    if eta_label:
        data["eta_label"] = eta_label
    if eta_total_label:
        data["eta_total_label"] = eta_total_label
    if eta_label or eta_total_label:
        data["eta_display"] = format_scraper_eta_display(
            str(eta_label or ""), str(eta_total_label or "")
        )
    try:
        with open(STATUS_FILE, "w") as f:
            json.dump(data, f)
    except Exception:
        pass


def get_color_name(rgb_str):
    """Pomocnicza funkcja do nazywania podstawowych kolorów TradingView"""
    if not rgb_str:
        return "Brak"
    low = rgb_str.lower()
    if (
        "242, 54, 69" in rgb_str
        or "255, 0, 0" in rgb_str
        or "128, 0, 0" in rgb_str
        or "red" in low
    ):
        return "Czerwony"
    if (
        "0, 188, 212" in rgb_str
        or "0, 255, 255" in rgb_str
        or "0, 0, 255" in rgb_str
        or "blue" in low
        or "cyan" in low
    ):
        return "Niebieski"
    if (
        "8, 153, 129" in rgb_str
        or "0, 255, 0" in rgb_str
        or "green" in low
    ):
        return "Zielony"
    if (
        "255, 170, 0" in rgb_str
        or "255, 235, 59" in rgb_str
        or "255, 255, 0" in rgb_str
        or "orange" in low
        or "yellow" in low
    ):
        return "Żółty" if "255, 255, 0" in rgb_str or "255, 235, 59" in rgb_str else "Pomarańczowy"
    return rgb_str


def _to_float(text) -> Optional[float]:
    """Parsuje liczby z TradingView: PL/US separators, NBSP, unicode minus."""
    if text is None:
        return None
    s = str(text)
    s = re.sub(r"\s+", "", s)
    s = s.replace("\u2212", "-").replace("'", "")
    if not s:
        return None

    if "," in s and "." in s:
        # Last separator is the decimal separator; the other one is thousands.
        if s.rfind(".") > s.rfind(","):
            s = s.replace(",", "")
        else:
            s = s.replace(".", "").replace(",", ".")
    elif "," in s:
        parts = s.split(",")
        if len(parts) > 1 and all(len(p) == 3 for p in parts[1:]):
            s = "".join(parts)
        else:
            s = s.replace(",", ".")
    elif "." in s:
        parts = s.split(".")
        if len(parts) > 1 and all(len(p) == 3 for p in parts[1:]):
            s = "".join(parts)

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
) -> bool:
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
            return True
        time.sleep(delay_s)
    logger.warning(
        "Legenda: nie udało się potwierdzić obecności %s w DOM po %d próbach — kontynuuję odczyt.",
        ind_name,
        max_attempts,
    )
    return False


def _note_indicator_error(row_data: dict, ind_name: str, message: str) -> None:
    errors = row_data.setdefault("_indicator_errors", {})
    if isinstance(errors, dict):
        errors[str(ind_name).strip()] = str(message).strip()


def _verify_indicator_present(
    target_page, ind_name: str, attempts: int = 3, delay_s: float = 1.0
) -> bool:
    """Czy ``_page_legend_has_indicator`` zwraca ``True`` w ramach ``attempts`` prób.

    Używane po dodaniu wskaźnika, by potwierdzić że TradingView faktycznie
    dorzucił blok do legendy (nie tylko otworzył modal i zamknął).
    """
    for attempt in range(attempts):
        if _page_legend_has_indicator(target_page, ind_name):
            return True
        if attempt < attempts - 1:
            time.sleep(delay_s)
    return False


def _macd_title_score(title_text: str) -> int:
    """Wyższy wynik = lepsze dopasowanie do CM_Ult_MacD_MTF."""
    tl = (title_text or "").lower()
    if "ult" in tl and "mtf" in tl:
        return 3
    if "ult" in tl or "mtf" in tl:
        return 2
    if "macd" in tl:
        return 1
    return 0


def _class_has_token(classes, token: str) -> bool:
    return any(token in c for c in (classes or []))


def _legend_style(val_el, title_el=None, item_el=None) -> str:
    for el in (val_el, title_el, item_el):
        if el is not None and el.get("style"):
            return el.get("style", "")
    if item_el is not None:
        for child in item_el.find_all("div"):
            if child.get("style"):
                return child.get("style", "")
    return ""


def _legend_value_from_div(div, *, style: str = "", keep_empty: bool = False) -> Optional[dict]:
    text = div.get_text(strip=True)
    missing = not text or text == "\u2205"
    if missing and not keep_empty:
        return None
    style = style or div.get("style", "")
    return {
        "text": "" if missing else text,
        "color": get_color_name(style),
        "missing": missing,
    }


def _normalize_legend_label(label: str) -> str:
    return re.sub(r"\s+", " ", (label or "").strip()).lower()


def _collect_legend_entries(
    item, *, skip_zero: bool = False, keep_empty: bool = False
) -> list:
    """Zbiera wpisy legendy: etykieta (valueTitle) + wartość (valueValue) + kolor."""
    entries: list = []
    values_root = item.find(attrs={"data-qa-id": "legend-source-values"})
    search_in = values_root if values_root is not None else item

    value_items = [
        d
        for d in search_in.find_all("div")
        if _class_has_token(d.get("class"), "valueItem")
    ]
    for val_item in value_items:
        title_el = None
        val_el = None
        for child in val_item.find_all("div"):
            if title_el is None and _class_has_token(child.get("class"), "valueTitle"):
                title_el = child
            if _class_has_token(child.get("class"), "valueValue"):
                val_el = child
                break
        if val_el is None:
            continue
        raw = _legend_value_from_div(
            val_el,
            style=_legend_style(val_el, title_el, val_item),
            keep_empty=keep_empty,
        )
        if raw is None:
            continue
        if skip_zero and not raw.get("missing") and raw["text"] in ("0", "0.00", "0,00"):
            continue
        label = title_el.get_text(strip=True) if title_el else ""
        entries.append({"label": label, "text": raw["text"], "color": raw["color"]})
        if raw.get("missing"):
            entries[-1]["missing"] = True

    if not entries:
        for div in search_in.find_all("div"):
            if not _class_has_token(div.get("class"), "valueValue"):
                continue
            raw = _legend_value_from_div(div, keep_empty=keep_empty)
            if raw is None:
                continue
            if skip_zero and not raw.get("missing") and raw["text"] in ("0", "0.00", "0,00"):
                continue
            entries.append({"label": "", "text": raw["text"], "color": raw["color"]})
            if raw.get("missing"):
                entries[-1]["missing"] = True

    if keep_empty:
        return entries

    deduped: list = []
    for e in entries:
        key = (_normalize_legend_label(e["label"]), e["text"])
        replaced = False
        for i, existing in enumerate(deduped):
            if (_normalize_legend_label(existing["label"]), existing["text"]) != key:
                continue
            replaced = True
            if existing["color"] == "Brak" and e["color"] != "Brak":
                deduped[i] = e
            break
        if not replaced:
            deduped.append(e)
    return deduped


def _entry_by_label_pattern(entries: list, *patterns: str) -> Optional[dict]:
    for pat in patterns:
        pl = re.sub(r"\s+", "", pat.lower())
        for e in entries:
            lab = re.sub(r"\s+", "", _normalize_legend_label(e.get("label", "")))
            if not lab:
                continue
            if pl in lab or lab in pl:
                return e
    return None


def _entry_has_value(entry: Optional[dict]) -> bool:
    return bool(entry) and not entry.get("missing") and bool(str(entry.get("text", "")).strip())


def _hts_band_slots(entries: list) -> list:
    """HTS ribbon slots from legend, excluding trend-change rows."""
    return [
        e
        for e in entries
        if "trend" not in _normalize_legend_label(e.get("label", ""))
    ]


def _hts_slow_is_placeholder(
    entry: Optional[dict], fh: Optional[float], fl: Optional[float]
) -> bool:
    """Slow slot ``0`` / ``0.0000`` with price-scale Fast bands is an empty placeholder."""
    if not entry or entry.get("missing"):
        return True
    val = _to_float(entry.get("text"))
    if val is None:
        return True
    if val != 0:
        return False
    fast = [x for x in (fh, fl) if x is not None]
    return bool(fast) and all(abs(x) > 0.05 for x in fast)


def _hts_slow_has_value(
    entry: Optional[dict], fh: Optional[float], fl: Optional[float]
) -> bool:
    return _entry_has_value(entry) and not _hts_slow_is_placeholder(entry, fh, fl)


def _entry_at(entries: list, idx: int) -> Optional[dict]:
    if idx >= len(entries):
        return None
    entry = entries[idx]
    return entry if _entry_has_value(entry) else None


def _entry_macd_line(entries: list) -> Optional[dict]:
    for e in entries:
        lab = _normalize_legend_label(e.get("label", ""))
        if not lab:
            continue
        if "macd" not in lab:
            continue
        if any(x in lab for x in ("signal", "hist", "cross")):
            continue
        if not _entry_has_value(e):
            continue
        return e
    return None


def _trend_from_macd_color(color: str) -> str:
    if color == "Zielony":
        return "Wzrostowy"
    if color == "Czerwony":
        return "Spadkowy"
    return "Brak trendu"


def _trend_from_macd_signal(macd_f: float, signal_f: float) -> Optional[str]:
    """Trend z pozycji linii MACD względem Signal (MACD powyżej Signal → byk)."""
    if macd_f > signal_f:
        return "Wzrostowy"
    if macd_f < signal_f:
        return "Spadkowy"
    return None


def _cross_from_color(color: str) -> str:
    if color == "Zielony":
        return "BULL CROSS"
    if color == "Czerwony":
        return "BEAR CROSS"
    return "Brak Crossa"


def _collect_legend_values(item, *, skip_zero: bool = False) -> list:
    """Kompatybilność wsteczna: same wartości bez etykiet."""
    return [
        {"text": e["text"], "color": e["color"]}
        for e in _collect_legend_entries(item, skip_zero=skip_zero)
    ]


def _indicator_search_query(
    ind_name: str, indicator_search: Optional[Dict[str, str]] = None
) -> str:
    if indicator_search:
        q = indicator_search.get(ind_name)
        if q:
            return str(q).strip()
    return (ind_name or "").strip()


def _extract_legend_html(target_page) -> str:
    """Fragment HTML legendy (tylko bloki wskaźników) — szybszy niż page.content()."""
    try:
        parts = target_page.evaluate(
            """() => Array.from(
                document.querySelectorAll('[data-qa-id="legend-source-item"]')
            ).map(el => el.outerHTML)"""
        )
        if not parts:
            return ""
        return "<html><body>" + "".join(parts) + "</body></html>"
    except Exception:
        return ""


def parse_indicators(html_content, indicators_to_find):
    """Pobiera i parsuje wartości wskaźników z html dla podanej listy nazw."""
    soup = BeautifulSoup(html_content, "lxml")
    legend_items = soup.find_all("div", attrs={"data-qa-id": "legend-source-item"})
    macd_best_score = -1
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
                elif (ind_name or "").strip().lower() == "macd":
                    score = _macd_title_score(title_text)
                    if score < macd_best_score:
                        continue
                    macd_best_score = score
                    _parse_macd_block(item, results, ind_name)
                else:
                    _parse_hts_block(item, results, ind_name)
            except Exception as exc:
                logger.warning("Błąd parsowania bloku %s: %s", ind_name, exc)

    return results


def _parse_pca_block(item, results, ind_name: str) -> None:
    values = [
        e
        for e in _collect_legend_entries(item, skip_zero=False)
        if _to_float(e["text"]) is not None
    ]
    if not values:
        return

    colored_values = [v for v in values if v["color"] != "Brak"]
    last_val = (colored_values or values)[-1]
    results["PCA_Value"] = last_val["text"]
    results["PCA_Color"] = last_val["color"]
    results[f"{ind_name}_Values"] = (
        f"{last_val['text']} ({results['PCA_Color']})"
    )


def _hts_trend_and_cross(fh: float, fl: float, sh: float, sl: float) -> tuple:
    """Trend i cross z geometrii wstęg Fast vs Slow."""
    if fl > sh:
        return (
            "Wzrostowy",
            "BULL CROSS (Wstęgi się przecięły w górę)",
        )
    if fh < sl:
        return (
            "Spadkowy",
            "BEAR CROSS (Wstęgi się przecięły w dół)",
        )
    if fl < sh:
        return ("Spadkowy", "Brak Crossa")
    if fh > sl and fh > sh:
        return ("Wzrostowy", "Brak Crossa")
    return ("Brak trendu", "Brak Crossa")


def _parse_hts_block(item, results, ind_name: str) -> None:
    entries = _collect_legend_entries(item, skip_zero=False, keep_empty=True)

    trend_change = _entry_by_label_pattern(entries, "trend change", "trendchange")
    if _entry_has_value(trend_change):
        results[f"{ind_name}_Trend_Change"] = _format_legend_value(trend_change)

    slot_entries = _hts_band_slots(entries)

    fh_e = _entry_by_label_pattern(entries, "fast high", "fasthigh")
    fl_e = _entry_by_label_pattern(entries, "fast low", "fastlow")
    sh_e = _entry_by_label_pattern(entries, "slow high", "slowhigh")
    sl_e = _entry_by_label_pattern(entries, "slow low", "slowlow")

    if not all([fh_e, fl_e, sh_e, sl_e]):
        if len(slot_entries) < 2:
            str_vals = [f"{e['text']} ({e['color']})" for e in slot_entries]
            results[f"{ind_name}_Values"] = (
                " | ".join(str_vals) if str_vals else "Brak poprawnych danych"
            )
            return
        positional = (slot_entries + [None, None, None, None])[:4]
        fh_e, fl_e, sh_e, sl_e = positional

    fh = _to_float(fh_e["text"]) if _entry_has_value(fh_e) else None
    fl = _to_float(fl_e["text"]) if _entry_has_value(fl_e) else None

    if len(slot_entries) >= 4:
        pos_sh, pos_sl = slot_entries[2], slot_entries[3]
        if _hts_slow_is_placeholder(sh_e, fh, fl) and _hts_slow_has_value(
            pos_sh, fh, fl
        ):
            sh_e = pos_sh
        if _hts_slow_is_placeholder(sl_e, fh, fl) and _hts_slow_has_value(
            pos_sl, fh, fl
        ):
            sl_e = pos_sl

    if _entry_has_value(fh_e):
        results[f"{ind_name}_Fast_High"] = _format_legend_value(fh_e)
    if _entry_has_value(fl_e):
        results[f"{ind_name}_Fast_Low"] = _format_legend_value(fl_e)
    if _hts_slow_has_value(sh_e, fh, fl):
        results[f"{ind_name}_Slow_High"] = _format_legend_value(sh_e)
    if _hts_slow_has_value(sl_e, fh, fl):
        results[f"{ind_name}_Slow_Low"] = _format_legend_value(sl_e)

    if not all(
        [
            _entry_has_value(fh_e),
            _entry_has_value(fl_e),
            _hts_slow_has_value(sh_e, fh, fl),
            _hts_slow_has_value(sl_e, fh, fl),
        ]
    ):
        results[f"{ind_name}_Trend"] = "Brak trendu"
        results[f"{ind_name}_Cross"] = "Brak Crossa"
        return

    sh = _to_float(sh_e["text"])
    sl = _to_float(sl_e["text"])
    if None in (fh, fl, sh, sl):
        logger.debug(
            "Nie udało się sparsować liczb dla %s: %s",
            ind_name,
            [fh_e["text"], fl_e["text"], sh_e["text"], sl_e["text"]],
        )
        results[f"{ind_name}_Trend"] = "Brak trendu"
        results[f"{ind_name}_Cross"] = "Brak Crossa"
        return

    trend, cross_info = _hts_trend_and_cross(fh, fl, sh, sl)
    results[f"{ind_name}_Trend"] = trend
    results[f"{ind_name}_Cross"] = cross_info


def _format_legend_value(raw: dict) -> str:
    return f"{raw['text']} ({raw['color']})"


def _parse_macd_block(item, results, ind_name: str) -> None:
    """Parser CM_Ult_MacD_MTF: trend z koloru MACD, fallback na MACD vs Signal."""
    entries = _collect_legend_entries(item, skip_zero=False, keep_empty=True)

    signal_labeled = _entry_by_label_pattern(entries, "signal")
    hist_labeled = _entry_by_label_pattern(entries, "hist", "histogram")
    cross_labeled = _entry_by_label_pattern(entries, "cross")

    line_raw = _entry_macd_line(entries) or _entry_at(entries, 0)
    signal_raw = signal_labeled if _entry_has_value(signal_labeled) else _entry_at(entries, 1)
    hist_raw = hist_labeled if _entry_has_value(hist_labeled) else _entry_at(entries, 2)
    cross_raw = cross_labeled if _entry_has_value(cross_labeled) else _entry_at(entries, 3)

    if line_raw is None:
        str_vals = [f"{e['text']} ({e['color']})" for e in entries]
        results[f"{ind_name}_Values"] = (
            " | ".join(str_vals) if str_vals else "Brak poprawnych danych"
        )
        return

    results[f"{ind_name}_Line"] = _format_legend_value(line_raw)
    if _entry_has_value(signal_raw):
        results[f"{ind_name}_Signal"] = _format_legend_value(signal_raw)
    if _entry_has_value(hist_raw):
        results[f"{ind_name}_Histogram"] = _format_legend_value(hist_raw)
    if _entry_has_value(cross_raw):
        results[f"{ind_name}_Cross_Value"] = _format_legend_value(cross_raw)

    results[f"{ind_name}_Fast_High"] = _format_legend_value(line_raw)
    results[f"{ind_name}_Fast_Low"] = (
        _format_legend_value(hist_raw) if _entry_has_value(hist_raw) else _format_legend_value(line_raw)
    )
    results[f"{ind_name}_Slow_High"] = (
        _format_legend_value(cross_raw) if _entry_has_value(cross_raw) else _format_legend_value(signal_raw)
        if _entry_has_value(signal_raw)
        else _format_legend_value(line_raw)
    )
    results[f"{ind_name}_Slow_Low"] = (
        _format_legend_value(signal_raw) if _entry_has_value(signal_raw) else _format_legend_value(line_raw)
    )

    macd_f = _to_float(line_raw["text"])
    signal_f = _to_float(signal_raw["text"]) if signal_raw else None
    trend = _trend_from_macd_color(line_raw["color"])
    if trend == "Brak trendu" and macd_f is not None and signal_f is not None:
        trend = _trend_from_macd_signal(macd_f, signal_f)
    if trend is None:
        trend = "Brak trendu"

    cross_info = _cross_from_color(cross_raw["color"]) if _entry_has_value(cross_raw) else "Brak Crossa"

    results[f"{ind_name}_Trend"] = trend
    results[f"{ind_name}_Cross"] = cross_info


def _select_indicator_from_search_list(target_page, ind_name: str) -> None:
    """Wybiera element z listy modal wyszukiwania wskaźników (po wciśnięciu „/").

    Dla wskaźnika ``MacD`` skanuje wszystkie widoczne ``[data-role="list-item"]``
    i klika ten o najwyższym wyniku ``_macd_title_score`` (preferuje
    ``CM_Ult_MacD_MTF`` nad wbudowanym ``MACD``). Dla pozostałych wskaźników
    pozostawia historyczne zachowanie (kliknięcie pierwszego elementu).
    """
    items = target_page.locator('div[data-role="list-item"]')
    try:
        n = int(items.count())
    except Exception:
        n = 0
    if n <= 0:
        raise RuntimeError("Brak widocznych elementów listy wskaźników do wyboru.")

    if ind_name != "MacD":
        items.first.click(force=True)
        return

    best_idx = -1
    best_score = 0
    best_title = ""
    for i in range(min(n, 40)):
        try:
            title = items.nth(i).inner_text(timeout=800).strip()
        except Exception:
            title = ""
        score = _macd_title_score(title)
        if score > best_score:
            best_idx = i
            best_score = score
            best_title = title

    if best_idx >= 0:
        logger.info(
            "Indicator search match for %s: %s (score=%d)",
            ind_name,
            best_title,
            best_score,
        )
        items.nth(best_idx).click(force=True)
        return

    try:
        fallback_title = items.first.inner_text(timeout=800).strip()
    except Exception:
        fallback_title = ""
    logger.warning(
        "Indicator search match for %s: brak elementu ze score>0; klikam pierwszy (%r).",
        ind_name,
        fallback_title,
    )
    items.first.click(force=True)


def add_indicator_to_chart(
    target_page,
    ind_name: str,
    ticker: str,
    indicator_search: Optional[Dict[str, str]] = None,
) -> None:
    """Otwiera modal wskaźników, wybiera najlepszy wynik i weryfikuje legendę.

    Po dodaniu wskaźnika sprawdza ``_verify_indicator_present``; jeśli legenda
    nie zawiera wskaźnika, usuwa go i ponawia całe dodawanie (search + select)
    — do 3 prób łącznie. Po wyczerpaniu prób podnosi ``RuntimeError`` z
    sufiksem ``(legenda nie zawiera wskaźnika po dodaniu)``.
    """
    search_query = _indicator_search_query(ind_name, indicator_search)
    max_add_attempts = 3

    for add_attempt in range(max_add_attempts):
        target_page.keyboard.press("/")
        time.sleep(SLEEP_AFTER_INDICATOR_MODAL_S)
        target_page.keyboard.type(search_query, delay=scraper_perf().keyboard_delay_ms)
        time.sleep(SLEEP_AFTER_INDICATOR_QUERY_S)
        try:
            target_page.wait_for_selector(
                'div[data-role="list-item"]', state="visible", timeout=3000
            )
            _select_indicator_from_search_list(target_page, ind_name)
        except Exception as e:
            raise RuntimeError(
                f"Zbyt długi czas oczekiwania na listę wskaźników ({ind_name}) dla {ticker}. Błąd: {e}"
            )
        time.sleep(SLEEP_AFTER_SMALL_ACTION_S)
        target_page.keyboard.press("Escape")
        logger.info("Czekam na przeliczenie wskaźnika %s (adaptive)...", ind_name)
        _wait_compute_for_indicator(target_page, ind_name)

        verify_delay = max(0.15, scraper_perf().poll_interval_s)
        if _verify_indicator_present(
            target_page, ind_name, attempts=3, delay_s=verify_delay
        ):
            return

        logger.warning(
            "Wskaźnik %s nie pojawił się w legendzie po dodaniu "
            "(próba %d/%d) — czyszczę i ponawiam.",
            ind_name,
            add_attempt + 1,
            max_add_attempts,
        )
        try:
            remove_active_indicator(target_page, ind_name, ticker)
        except Exception as remove_err:
            logger.warning(
                "Próba usunięcia %s przed ponownym dodaniem nie powiodła się: %s",
                ind_name,
                remove_err,
            )

    raise RuntimeError(
        f"Nie udało się dodać wskaźnika ({ind_name}) dla {ticker} "
        "(legenda nie zawiera wskaźnika po dodaniu)"
    )


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


def _move_crosshair_off_chart(target_page) -> None:
    """TradingView legend can show values under the last crosshair position.

    Move the pointer away from the plot before reading DOM so legend values
    fall back to the latest bar / right-side labels instead of a historical bar.
    """
    try:
        target_page.keyboard.press("Escape")
    except Exception:
        pass
    try:
        viewport = target_page.viewport_size or {"width": 1280, "height": 720}
        target_page.mouse.move(
            max(10, min(40, int(viewport.get("width", 1280)) - 10)),
            max(10, min(40, int(viewport.get("height", 720)) - 10)),
        )
        time.sleep(SLEEP_AFTER_MICRO_ACTION_S)
    except Exception as exc:
        logger.debug("Nie udało się odsunąć crosshair przed odczytem legendy: %s", exc)


def _merge_parsed_indicators_into_row(
    row_data: dict, indicator_data: dict, ind_name: str
) -> None:
    for key, val in indicator_data.items():
        if key == "PCA_Value" or key == "PCA_Color" or key.startswith(ind_name):
            if key != f"{ind_name}_Values" or ind_name != "PCA":
                logger.debug("[%s]: %s", key, val)
            row_data[key] = val


def _note_missing_indicator_parse(row_data: dict, ind_name: str) -> None:
    _ser = pd.Series(row_data)
    if not row_has_indicator_data(_ser, ind_name):
        errors = row_data.get("_indicator_errors") or {}
        if not (isinstance(errors, dict) and ind_name in errors):
            _note_indicator_error(row_data, ind_name, "błąd parsowania legendy")


def _retry_macd_parse(
    target_page,
    ticker: str,
    interval: str,
    row_data: dict,
    indicator_data: dict,
    indicator_search: Optional[Dict[str, str]],
) -> dict:
    """Ponawia dodanie MacD gdy MacD_Line puste."""
    line_val = row_data.get("MacD_Line") or indicator_data.get("MacD_Line")
    if cell_nonempty(line_val):
        return indicator_data
    logger.warning("MacD legend empty for %s %s — retrying once", ticker, interval)
    try:
        remove_active_indicator(target_page, "MacD", ticker)
    except Exception as remove_err:
        logger.warning(
            "Re-try MacD: usuwanie wskaźnika nie powiodło się: %s", remove_err
        )
    time.sleep(scraper_perf().micro_action_s)
    retry_ok = False
    try:
        add_indicator_to_chart(target_page, "MacD", ticker, indicator_search)
        retry_ok = True
    except Exception as add_err:
        logger.warning(
            "Re-try MacD: ponowne dodanie wskaźnika nie powiodło się: %s",
            add_err,
        )
        _note_indicator_error(
            row_data, "MacD", "ponowne dodanie wskaźnika nie powiodło się"
        )
        return indicator_data
    if retry_ok:
        _wait_compute_for_indicator(target_page, "MacD")
        _ensure_legend_expanded(target_page)
        _move_crosshair_off_chart(target_page)
        legend_html = _extract_legend_html(target_page)
        html_retry = legend_html or target_page.content()
        indicator_data_retry = parse_indicators(html_retry, ["MacD"])
        line_val_retry = indicator_data_retry.get("MacD_Line")
        if cell_nonempty(line_val_retry):
            _merge_parsed_indicators_into_row(row_data, indicator_data_retry, "MacD")
            return indicator_data_retry
        logger.warning(
            "Drugi parse MacD też pusty (%s/%s) — zostawiam wynik pierwszego.",
            ticker,
            interval,
        )
        _note_indicator_error(row_data, "MacD", "pusty odczyt po ponowieniu")
    return indicator_data


def _parse_indicators_from_page(
    target_page,
    indicators_to_parse: List[str],
    row_data: dict,
    *,
    ticker: str = "",
    interval: str = "",
    indicator_search: Optional[Dict[str, str]] = None,
) -> Tuple[float, float]:
    """Odczyt wskaźników z legendy. Zwraca (indicator_wait_s, parse_s)."""
    if not indicators_to_parse:
        return 0.0, 0.0
    wait_t0 = time.perf_counter()
    wait_ok = _wait_compute_for_indicators(target_page, indicators_to_parse)
    indicator_wait_s = time.perf_counter() - wait_t0
    if not wait_ok:
        for ind_name in indicators_to_parse:
            if not _legend_has_nonempty_values_locator(target_page, ind_name):
                _note_indicator_error(row_data, ind_name, "timeout legendy")
    _ensure_legend_expanded(target_page)
    _move_crosshair_off_chart(target_page)
    parse_t0 = time.perf_counter()
    legend_html = _extract_legend_html(target_page)
    if legend_html:
        indicator_data = parse_indicators(legend_html, indicators_to_parse)
    else:
        logger.debug("Legenda evaluate pusta — fallback page.content()")
        html_content = target_page.content()
        indicator_data = parse_indicators(html_content, indicators_to_parse)
    for ind_name in indicators_to_parse:
        ind_slice = {
            k: v
            for k, v in indicator_data.items()
            if k == "PCA_Value"
            or k == "PCA_Color"
            or k.startswith(ind_name)
        }
        _merge_parsed_indicators_into_row(row_data, ind_slice, ind_name)
        if ind_name == "MacD":
            _retry_macd_parse(
                target_page,
                ticker,
                interval,
                row_data,
                ind_slice,
                indicator_search,
            )
        _note_missing_indicator_parse(row_data, ind_name)
    parse_s = time.perf_counter() - parse_t0
    return indicator_wait_s, parse_s


def _resolve_ticker_metadata(
    target_page,
    ticker: str,
    symbol_search_info: Dict[str, str],
) -> Tuple[str, str, str, Optional[str]]:
    """Zwraca (company_name, exchange, current_price, skip_reason)."""
    symbol_search_name = symbol_search_info.get("name", "")
    symbol_search_exchange = symbol_search_info.get("exchange", "")
    company_name = "Nieznana"
    exchange = ""
    current_price = ""
    title_text = target_page.title()
    if (
        "Błędny symbol" in title_text
        or "Invalid symbol" in title_text
        or "Nie znaleziono" in title_text
    ):
        return company_name, exchange, current_price, "Błędny symbol / nie znaleziono na TradingView"

    legend_desc = ""
    try:
        legend_desc = target_page.locator(
            'div[data-name="legend-source-description"]'
        ).first.inner_text(timeout=2000)
    except Exception:
        pass

    header_blob = read_chart_symbol_header_blob(target_page, ticker)
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
        header_blob=" ".join(filter(None, [header_blob, title_text, legend_desc])),
    )
    title_clean = (
        title_text.split(" Wskaźnik")[0]
        .split(" Wykres")[0]
        .split(" —")[0]
        .split(" -")[0]
        .strip()
    )
    match_price = re.search(r"\s+(\d+[\.,]\d+|\d+)", title_clean)
    if match_price:
        current_price = match_price.group(1)
    return company_name, exchange, current_price, None


def _metadata_from_existing_rows(df, ticker: str) -> Optional[Tuple[str, str, str]]:
    """Zwraca (company, exchange, price) z CSV gdy już mamy sensowne metadane."""
    if df is None or getattr(df, "empty", True):
        return None
    if "Ticker" not in df.columns:
        return None
    sub = df[df["Ticker"].astype(str) == str(ticker)]
    if sub.empty:
        return None
    row = sub.iloc[0]
    company = str(row.get("Company_Name", "") or "").strip()
    exchange = str(row.get("Exchange", "") or "").strip()
    price = str(row.get("Current_Price", "") or "").strip()
    bad_names = {"", "Nieznana", "—", "\u2014", "?"}
    if company in bad_names and not exchange and not price:
        return None
    if company in bad_names:
        company = "Nieznana"
    return company, exchange, price


def _fundamentals_during_scrape() -> bool:
    if not os.path.exists(CONFIG_FILE):
        return False
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            cfg = json.load(f) or {}
        fund_cfg = cfg.get("fundamentals") or {}
        if not fund_cfg.get("enabled", True):
            return False
        return bool(fund_cfg.get("during_scrape", False))
    except Exception:
        return False


def _switch_ticker_on_chart(
    target_page,
    ticker: str,
) -> Tuple[Dict[str, str], bool]:
    """Przełącza ticker. Zwraca (symbol_search_info, search_still_open)."""
    perf = scraper_perf()
    logger.info("Przełączam na ticker: %s", ticker)
    target_page.locator("body").click(force=True)
    time.sleep(perf.micro_action_s)
    target_page.keyboard.type(ticker, delay=perf.keyboard_delay_ms)
    time.sleep(perf.small_action_s)
    symbol_search_info = read_symbol_search_modal_company_info(target_page, ticker)
    target_page.keyboard.press("Enter")
    _wait_for_ticker_loaded(target_page, ticker)
    search_still_open = False
    try:
        search_box = target_page.locator('input[type="search"]')
        if search_box.count() > 0 and search_box.first.is_visible():
            search_still_open = True
    except Exception:
        pass
    return symbol_search_info, search_still_open


def _execute_ticker_first_loop(
    target_page,
    *,
    tickers: List[str],
    intervals: List[str],
    indicators: List[str],
    status_indicators: List[str],
    indicator_search: Dict[str, str],
    results_buf: ResultsBuffer,
    current_run_file: str,
    processed_combos: set,
    respect_csv_data: bool,
    is_subset_run: bool,
    is_indicator_subset: bool,
    persist_state: bool,
    persist_checkpoint,
    update_state,
    start_ticker_idx: int,
    start_ind_idx: int = 0,
    start_interval_idx: int = 0,
    show_resumed_label: bool,
    active_elapsed_s: Optional[float],
    run_t0: float,
    eta_baseline_done: int = 0,
    eta_baseline_elapsed_s: float = 0.0,
) -> bool:
    """Tryb ticker-first: partie wskaźników na wykresie (TV Free = max 2), jedno przełączenie tickera na partię."""
    perf = scraper_perf()
    max_on_chart = perf.max_indicators_on_chart
    batches = chunk_indicators(indicators, max_on_chart)
    n_batches = len(batches)
    n_intervals = max(len(intervals), 1)
    logger.info(
        "=== Tryb ticker_first: %d wskaźników w %d partiach (max %d na wykresie) ===",
        len(indicators),
        n_batches,
        max_on_chart,
    )

    def _report_progress(
        ticker_idx: int,
        *,
        interval_name: str = "",
        interval_idx: int = 0,
        current_ticker: str = "",
        current_indicator: str = "",
        progress_resumed: bool = False,
        batch_idx_val: int = 0,
        phase: str = "",
    ) -> None:
        progress_str, eta_seconds, eta_label, eta_total_label = (
            _build_running_scraper_progress_ticker_first(
                ticker_idx,
                len(tickers),
                interval_name,
                active_elapsed_s=active_elapsed_s,
                run_t0=run_t0,
                resumed=progress_resumed,
                batch_idx=batch_idx_val,
                n_batches=n_batches,
                interval_idx=interval_idx,
                n_intervals=n_intervals,
                phase=phase,
                eta_baseline_done=eta_baseline_done,
                eta_baseline_elapsed_s=eta_baseline_elapsed_s,
            )
        )
        write_scraper_status(
            "running",
            progress_str,
            current_ticker,
            current_indicator=current_indicator,
            eta_seconds=eta_seconds,
            eta_label=eta_label or None,
            eta_total_label=eta_total_label or None,
        )

    ticker_begin = max(int(start_ticker_idx), 0)
    batch_begin = max(int(start_ind_idx), 0)
    interval_begin = max(int(start_interval_idx), 0)

    for batch_idx, batch_inds in enumerate(batches):
        if batch_idx < batch_begin:
            continue
        is_last_batch = batch_idx == n_batches - 1
        added: List[str] = []
        for ind_name in batch_inds:
            try:
                add_indicator_to_chart(
                    target_page, ind_name, "init", indicator_search
                )
                added.append(ind_name)
            except RuntimeError as exc:
                logger.warning(
                    "ticker_first: nie udało się dodać %s (%s) — fallback do indicator_first",
                    ind_name,
                    exc,
                )
                for prev in added:
                    remove_active_indicator(target_page, prev, "rollback")
                return False

        batch_label = ", ".join(batch_inds)
        ticker_start = ticker_begin if batch_idx == batch_begin else 0

        for ticker_idx in range(ticker_start, len(tickers)):
            ticker = tickers[ticker_idx]
            update_state._last_ticker_idx = ticker_idx
            update_state._last_ind_idx = batch_idx
            persist_checkpoint(
                ticker_idx,
                batch_idx,
                mark_resumed=show_resumed_label
                and ticker_idx == ticker_start
                and batch_idx == batch_begin,
            )
            progress_resumed = (
                show_resumed_label
                and ticker_idx == ticker_start
                and batch_idx == batch_begin
            )
            _report_progress(
                ticker_idx,
                current_ticker=ticker,
                current_indicator=batch_label,
                progress_resumed=progress_resumed,
                batch_idx_val=batch_idx,
            )

            existing_df = results_buf.dataframe
            if respect_csv_data and ticker_fully_done_in_csv(
                existing_df, ticker, intervals, indicators
            ):
                logger.info(
                    "Pomijam %s — na dziś w CSV są już wszystkie wymagane dane.",
                    ticker,
                )
                if is_last_batch:
                    for interval in intervals:
                        update_state(ticker, interval)
                continue

            symbol_search_info, search_open = _switch_ticker_on_chart(
                target_page, ticker
            )
            if search_open:
                logger.warning(
                    "Ticker %s nie znaleziony (okno wyszukiwania wciąż otwarte). Pomijam.",
                    ticker,
                )
                target_page.keyboard.press("Escape")
                time.sleep(scraper_perf().small_action_s)
                if is_last_batch:
                    results_buf.record_skipped(
                        ticker,
                        "Nie znaleziono w wyszukiwarce (brak dopasowania lub zły format)",
                    )
                    for interval in intervals:
                        update_state(ticker, interval)
                    results_buf.flush()
                continue

            try:
                cached_meta = _metadata_from_existing_rows(
                    results_buf.dataframe, ticker
                )
                if cached_meta:
                    company_name, exchange, current_price = cached_meta
                    skip_reason = None
                    logger.debug(
                        "Metadane %s z CSV (pomijam odczyt nagłówka TV).", ticker
                    )
                else:
                    company_name, exchange, current_price, skip_reason = (
                        _resolve_ticker_metadata(
                            target_page, ticker, symbol_search_info
                        )
                    )
            except Exception as e:
                raise RuntimeError(
                    f"Błąd podczas pobierania danych dla {ticker}: {e}"
                ) from e

            if skip_reason:
                logger.warning("Ticker %s nie istnieje. Pomijam...", ticker)
                if is_last_batch:
                    results_buf.record_skipped(ticker, skip_reason)
                    for interval in intervals:
                        update_state(ticker, interval)
                    results_buf.flush()
                continue

            logger.info(
                "(Spółka: %s | Giełda: %s | Cena: %s | partia: %s)",
                company_name,
                exchange or "?",
                current_price,
                batch_label,
            )

            for interval_idx, interval in enumerate(intervals):
                update_state._last_interval_idx = interval_idx
                if (
                    batch_idx == batch_begin
                    and ticker_idx == ticker_start
                    and interval_idx < interval_begin
                ):
                    continue

                existing_df = results_buf.dataframe
                erow = get_row_for_ticker_interval(existing_df, ticker, interval)

                if respect_csv_data and erow is not None and row_interval_complete(
                    erow, indicators
                ):
                    logger.info(
                        "Pomijam %s - %s — w CSV jest już komplet wskaźników.",
                        ticker,
                        interval,
                    )
                    if is_last_batch:
                        update_state(ticker, interval)
                    _report_progress(
                        ticker_idx,
                        interval_name=interval,
                        interval_idx=interval_idx,
                        current_ticker=ticker,
                        current_indicator=f"{batch_label} · {interval}",
                        progress_resumed=progress_resumed,
                        batch_idx_val=batch_idx,
                    )
                    continue

                if (ticker, interval) in processed_combos:
                    if erow is not None and not row_interval_complete(erow, indicators):
                        logger.info(
                            "Sesja wskazywała na %s/%s, CSV niepełny — ponawiam.",
                            ticker,
                            interval,
                        )
                    elif erow is None:
                        logger.info(
                            "Sesja wskazywała na %s/%s, brak wiersza — ponawiam.",
                            ticker,
                            interval,
                        )
                    else:
                        _report_progress(
                            ticker_idx,
                            interval_name=interval,
                            interval_idx=interval_idx,
                            current_ticker=ticker,
                            current_indicator=f"{batch_label} · {interval}",
                            progress_resumed=progress_resumed,
                            batch_idx_val=batch_idx,
                        )
                        continue

                _report_progress(
                    ticker_idx,
                    interval_name=interval,
                    interval_idx=interval_idx,
                    current_ticker=ticker,
                    current_indicator=batch_label,
                    progress_resumed=progress_resumed,
                    batch_idx_val=batch_idx,
                    phase="zmiana interwału",
                )

                interval_ms = int(
                    _switch_chart_interval(target_page, interval) * 1000
                )

                row_data = {
                    "Ticker": ticker,
                    "Company_Name": company_name,
                    "Exchange": exchange,
                    "Current_Price": current_price,
                    "Interval": interval,
                    "Scrape_Status": "",
                    "Scrape_Error": "",
                }
                merge_existing_row_into_row_data(
                    row_data,
                    erow,
                    skip_indicator_merge=(is_subset_run and not is_indicator_subset),
                )

                indicators_to_parse = [
                    ind
                    for ind in batch_inds
                    if erow is None
                    or not row_has_indicator_data(erow, ind)
                    or is_subset_run
                ]
                if indicators_to_parse:
                    parse_label = ", ".join(indicators_to_parse)
                    _report_progress(
                        ticker_idx,
                        interval_name=interval,
                        interval_idx=interval_idx,
                        current_ticker=ticker,
                        current_indicator=parse_label,
                        progress_resumed=progress_resumed,
                        batch_idx_val=batch_idx,
                        phase=f"odczyt {parse_label}",
                    )
                    logger.info(
                        "Odczyt HTML (partia %d/%d): %s",
                        batch_idx + 1,
                        n_batches,
                        parse_label,
                    )
                    wait_ms, parse_ms = _parse_indicators_from_page(
                        target_page,
                        indicators_to_parse,
                        row_data,
                        ticker=ticker,
                        interval=interval,
                        indicator_search=indicator_search,
                    )
                    logger.info(
                        "Krok %s/%s: interwał=%dms wait=%dms parse=%dms",
                        ticker,
                        interval,
                        interval_ms,
                        int(wait_ms * 1000),
                        int(parse_ms * 1000),
                    )

                if is_indicator_subset and erow is not None:
                    for other_ind in status_indicators:
                        if other_ind not in indicators:
                            merge_indicator_into_row(row_data, erow, other_ind)

                if is_last_batch:
                    apply_final_scrape_status(row_data, status_indicators)
                    row_data.pop("_indicator_errors", None)
                    update_state(ticker, interval)
                else:
                    row_data["Scrape_Status"] = ""
                    row_data["Scrape_Error"] = ""
                    row_data.pop("_indicator_errors", None)

                results_buf.upsert(row_data)

            if is_last_batch:
                results_buf.flush()
                if _fundamentals_during_scrape():
                    try:
                        scrape_tv_fundamentals(target_page, ticker)
                    except Exception as exc:
                        logger.warning(
                            "Fundamentale dla %s — błąd po fazie technicznej: %s",
                            ticker,
                            exc,
                        )
                else:
                    logger.debug(
                        "Fundamentale %s pominięte w trakcie scrapingu "
                        "(fundamentals.during_scrape=false).",
                        ticker,
                    )

        for ind_name in reversed(added):
            remove_active_indicator(target_page, ind_name, "cleanup")

    return True


def run_scraper(
    tickers,
    intervals,
    indicators,
    port=9222,
    is_partial=False,
    is_indicator_subset=False,
    all_config_indicators=None,
    indicator_search: Optional[Dict[str, str]] = None,
    no_data_only: bool = False,
):
    _configure_logging()
    perf = init_scraper_performance()
    if indicator_search is None and os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                indicator_search = json.load(f).get("indicator_search") or {}
        except Exception:
            indicator_search = {}
    indicator_search = indicator_search or {}
    status_indicators = [
        str(i).strip()
        for i in (all_config_indicators or indicators or [])
        if str(i).strip()
    ]
    if not status_indicators:
        status_indicators = list(indicators or [])
    is_subset_run = bool(is_partial and not no_data_only)
    persist_state = (not is_partial) or no_data_only
    respect_csv_data = (not is_partial) or no_data_only
    # Use 127.0.0.1 (not "localhost"): on many systems localhost resolves to ::1 first,
    # while Chromium's CDP often listens only on IPv4 — then connect_over_cdp fails with ECONNREFUSED ::1:9222.
    cdp_url = f"http://127.0.0.1:{port}"
    logger.info("Łączenie z przeglądarką przez CDP: %s", cdp_url)

    state_file = "scraper_state.json"
    processed_combos = set()
    current_run_file = None
    session_started_at: Optional[float] = None
    active_elapsed_s: float = 0.0
    start_ind_idx = 0
    start_ticker_idx = 0
    start_interval_idx = 0
    resumed = False
    show_resumed_label = False
    eta_baseline_done = 0
    eta_baseline_elapsed_s = 0.0

    if persist_state and os.path.exists(state_file):
        try:
            state = _load_run_state_file(state_file) or {}
            if state.get("current_file") and os.path.exists(state["current_file"]):
                current_run_file = state["current_file"]
                processed_combos = set(tuple(x) for x in state.get("processed", []))
                raw_started = state.get("session_started_at")
                if raw_started is not None:
                    session_started_at = float(raw_started)
                else:
                    try:
                        session_started_at = os.path.getmtime(state_file)
                    except OSError:
                        pass
                try:
                    active_elapsed_s = float(state.get("active_elapsed_s") or 0.0)
                except (TypeError, ValueError):
                    active_elapsed_s = 0.0
                if no_data_only and state.get("no_data_only") and state.get("tickers"):
                    saved_inds = [
                        str(i).strip()
                        for i in (state.get("indicators") or [])
                        if str(i).strip()
                    ]
                    if not saved_inds or saved_inds == list(indicators or []):
                        tickers = list(state.get("tickers") or tickers)
                        start_ind_idx = max(int(state.get("ind_idx") or 0), 0)
                        start_ticker_idx = max(int(state.get("ticker_idx") or 0), 0)
                        start_interval_idx = max(int(state.get("interval_idx") or 0), 0)
                        resumed = True
                        show_resumed_label = bool(state.get("resumed")) or (
                            start_ind_idx > 0 or start_ticker_idx > 0
                        )
                        n_batches = len(
                            chunk_indicators(indicators, perf.max_indicators_on_chart)
                        )
                        n_intervals = max(len(intervals), 1)
                        eta_baseline_done, _ = scraper_overall_progress_ticker_first(
                            start_ticker_idx,
                            len(tickers),
                            start_ind_idx,
                            n_batches,
                            start_interval_idx,
                            n_intervals,
                        )
                        eta_baseline_elapsed_s = active_elapsed_s
                        logger.info(
                            "Wznawiam no_data run: ticker %d/%d, wsk. %d/%d, plik: %s",
                            start_ticker_idx + 1,
                            len(tickers),
                            start_ind_idx + 1,
                            len(indicators),
                            current_run_file,
                        )
                    else:
                        logger.info(
                            "Zapisany stan no_data ma inne wskaźniki (%s ≠ %s) — start od początku.",
                            ", ".join(saved_inds),
                            ", ".join(indicators or []),
                        )
                elif not no_data_only and not state.get("no_data_only"):
                    start_ticker_idx = max(int(state.get("ticker_idx") or 0), 0)
                    start_ind_idx = max(int(state.get("ind_idx") or 0), 0)
                    start_interval_idx = max(int(state.get("interval_idx") or 0), 0)
                    resumed = True
                    show_resumed_label = bool(state.get("resumed")) or (
                        start_ind_idx > 0
                        or start_ticker_idx > 0
                        or start_interval_idx > 0
                    )
                    n_batches = len(
                        chunk_indicators(indicators, perf.max_indicators_on_chart)
                    )
                    n_intervals = max(len(intervals), 1)
                    eta_baseline_done, _ = scraper_overall_progress_ticker_first(
                        start_ticker_idx,
                        len(tickers),
                        start_ind_idx,
                        n_batches,
                        start_interval_idx,
                        n_intervals,
                    )
                    eta_baseline_elapsed_s = active_elapsed_s
                    logger.info(
                        "Wznawiam pełny run: ticker %d/%d, partia %d/%d, interwał idx %d, plik: %s (pominięto %d kombinacji)",
                        start_ticker_idx + 1,
                        len(tickers),
                        start_ind_idx + 1,
                        n_batches,
                        start_interval_idx,
                        current_run_file,
                        len(processed_combos),
                    )
                elif no_data_only and not state.get("no_data_only"):
                    logger.info(
                        "Istniejący stan pełnego runu — rozpoczynam nową sesję no_data."
                    )
                    current_run_file = None
                    processed_combos = set()
                    session_started_at = None
                    active_elapsed_s = 0.0
                elif not no_data_only and state.get("no_data_only"):
                    logger.info(
                        "Istniejący stan no_data — rozpoczynam pełny run od nowa."
                    )
                    current_run_file = None
                    processed_combos = set()
                    session_started_at = None
                    active_elapsed_s = 0.0
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
        start_ind_idx = 0
        start_ticker_idx = 0
        start_interval_idx = 0
        resumed = False
        show_resumed_label = False
        active_elapsed_s = 0.0
        eta_baseline_done = 0
        eta_baseline_elapsed_s = 0.0

    if persist_state and session_started_at is None:
        session_started_at = time.time()

    results_buf = ResultsBuffer(current_run_file)
    logger.info(
        "Wydajność scrapera: mode=%s, loop=%s, max_on_chart=%d, keyboard_delay=%dms",
        perf.mode,
        perf.loop_mode,
        perf.max_indicators_on_chart,
        perf.keyboard_delay_ms,
    )

    def persist_checkpoint(
        ticker_val_idx: int,
        ind_val_idx: int,
        *,
        mark_resumed: bool = False,
        interval_val_idx: Optional[int] = None,
    ):
        if not persist_state:
            return
        nonlocal active_elapsed_s, run_t0
        if run_t0 is not None:
            active_elapsed_s = _scraper_elapsed_seconds(active_elapsed_s, run_t0)
            run_t0 = time.perf_counter()
        iv = (
            interval_val_idx
            if interval_val_idx is not None
            else getattr(update_state, "_last_interval_idx", 0)
        )
        try:
            _write_run_state_file(
                state_file,
                current_run_file=current_run_file,
                processed_combos=processed_combos,
                session_started_at=session_started_at,
                active_elapsed_s=active_elapsed_s,
                ticker_idx=ticker_val_idx,
                ind_idx=ind_val_idx,
                tickers=tickers,
                indicators=indicators,
                no_data_only=no_data_only,
                resumed=mark_resumed or show_resumed_label,
                interval_idx=iv,
                eta_baseline_done=eta_baseline_done,
                eta_baseline_elapsed_s=eta_baseline_elapsed_s,
            )
        except Exception as exc:
            logger.warning("Nie udało się zapisać stanu sesji: %s", exc)

    run_t0 = None

    def update_state(ticker_val, interval_val):
        if not persist_state:
            return
        processed_combos.add((ticker_val, interval_val))
        persist_checkpoint(
            getattr(update_state, "_last_ticker_idx", 0),
            getattr(update_state, "_last_ind_idx", 0),
            interval_val_idx=getattr(update_state, "_last_interval_idx", 0),
        )

    update_state._last_ticker_idx = 0
    update_state._last_ind_idx = 0
    update_state._last_interval_idx = 0

    if persist_state and no_data_only and not resumed:
        persist_checkpoint(0, 0)

    with sync_playwright() as p:
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
                    return pick_tradingview_chart_page(default_context.pages)
                except Exception:
                    return None

            target_page = _find_tv_page()
            if target_page is not None:
                logger.info(
                    "Używam istniejącej karty TradingView: %s",
                    _target_url(target_page) or "(brak URL)",
                )
            if target_page is None:
                logger.info(
                    "Karta TradingView jeszcze niewidoczna — czekam aż Brave/Chrome wstanie…"
                )
                deadline = time.time() + 12.0
                while time.time() < deadline:
                    target_page = _find_tv_page()
                    if target_page is not None:
                        logger.info(
                            "Używam istniejącej karty TradingView: %s",
                            _target_url(target_page) or "(brak URL)",
                        )
                        break
                    time.sleep(0.5)

            if target_page is None:
                logger.info(
                    "Brak otwartej karty TradingView — otwieram wykres "
                    "(https://www.tradingview.com/chart/) w bieżącej sesji CDP…"
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

            use_ticker_first = perf.loop_mode == "ticker_first"
            if use_ticker_first:
                completed = _execute_ticker_first_loop(
                    target_page,
                    tickers=tickers,
                    intervals=intervals,
                    indicators=indicators,
                    status_indicators=status_indicators,
                    indicator_search=indicator_search,
                    results_buf=results_buf,
                    current_run_file=current_run_file,
                    processed_combos=processed_combos,
                    respect_csv_data=respect_csv_data,
                    is_subset_run=is_subset_run,
                    is_indicator_subset=is_indicator_subset,
                    persist_state=persist_state,
                    persist_checkpoint=persist_checkpoint,
                    update_state=update_state,
                    start_ticker_idx=start_ticker_idx,
                    start_ind_idx=start_ind_idx,
                    start_interval_idx=start_interval_idx,
                    show_resumed_label=show_resumed_label,
                    active_elapsed_s=active_elapsed_s,
                    run_t0=run_t0,
                    eta_baseline_done=eta_baseline_done,
                    eta_baseline_elapsed_s=eta_baseline_elapsed_s,
                )
                if completed:
                    elapsed = _scraper_elapsed_seconds(active_elapsed_s, run_t0)
                    dh = _format_duration(elapsed)
                    logger.info(
                        "Zakończono ticker_first w %s (%.2fs). Dane: %s",
                        dh,
                        elapsed,
                        current_run_file,
                    )
                    n_tf_batches = len(
                        chunk_indicators(indicators, perf.max_indicators_on_chart)
                    )
                    n_tf_intervals = max(len(intervals), 1)
                    write_scraper_status(
                        "done",
                        _format_scraper_progress_ticker_first(
                            len(tickers) - 1,
                            len(tickers),
                            batch_idx=n_tf_batches - 1,
                            n_batches=n_tf_batches,
                            interval_idx=n_tf_intervals - 1,
                            n_intervals=n_tf_intervals,
                        ),
                        "",
                        "",
                        duration_seconds=elapsed,
                        duration_human=dh,
                    )
                    if persist_state and os.path.exists(state_file):
                        os.remove(state_file)
                    return

            for ind_idx, ind_name in enumerate(indicators):
                if ind_idx < start_ind_idx:
                    continue
                logger.info(
                    "=== Faza wskaźnika: %s (%d/%d) ===",
                    ind_name,
                    ind_idx + 1,
                    n_inds,
                )
                logger.info("Dodaję wskaźnik na wykres (raz na fazę): %s", ind_name)
                add_indicator_to_chart(
                    target_page, ind_name, ind_name, indicator_search
                )

                ticker_begin = start_ticker_idx if ind_idx == start_ind_idx else 0
                for ticker_idx in range(ticker_begin, len(tickers)):
                    ticker = tickers[ticker_idx]
                    update_state._last_ticker_idx = ticker_idx
                    update_state._last_ind_idx = ind_idx
                    persist_checkpoint(
                        ticker_idx,
                        ind_idx,
                        mark_resumed=show_resumed_label and ticker_idx == ticker_begin and ind_idx == start_ind_idx,
                    )
                    progress_resumed = show_resumed_label and (
                        ticker_idx == ticker_begin and ind_idx == start_ind_idx
                    )
                    progress_str, eta_seconds, eta_label, eta_total_label = _build_running_scraper_progress(
                        ticker_idx,
                        len(tickers),
                        ind_idx,
                        n_inds,
                        ind_name,
                        active_elapsed_s=active_elapsed_s,
                        run_t0=run_t0,
                        resumed=progress_resumed,
                        eta_baseline_done=eta_baseline_done,
                        eta_baseline_elapsed_s=eta_baseline_elapsed_s,
                    )
                    write_scraper_status(
                        "running",
                        progress_str,
                        ticker,
                        current_indicator=ind_name,
                        eta_seconds=eta_seconds,
                        eta_label=eta_label or None,
                        eta_total_label=eta_total_label or None,
                    )

                    existing_df = results_buf.dataframe

                    if respect_csv_data and ticker_fully_done_in_csv(
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
                    if all_done_for_ticker and ticker_fully_done_in_csv(
                        existing_df, ticker, intervals, indicators
                    ):
                        logger.info(
                            "Pomijam cały ticker %s — stan sesji i CSV są kompletne.",
                            ticker,
                        )
                        continue

                    symbol_search_info, search_open = _switch_ticker_on_chart(
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

                    if search_open:
                        logger.warning(
                            "Ticker %s nie znaleziony (okno wyszukiwania wciąż otwarte). Pomijam.",
                            ticker,
                        )
                        target_page.keyboard.press("Escape")
                        time.sleep(scraper_perf().small_action_s)
                        results_buf.record_skipped(
                            ticker,
                            "Nie znaleziono w wyszukiwarce (brak dopasowania lub zły format)",
                        )
                        for interval in intervals:
                            update_state(ticker, interval)
                        results_buf.flush()
                        continue

                    try:
                        cached_meta = _metadata_from_existing_rows(
                            results_buf.dataframe, ticker
                        )
                        if cached_meta:
                            company_name, exchange, current_price = cached_meta
                            skip_reason = None
                        else:
                            company_name, exchange, current_price, skip_reason = (
                                _resolve_ticker_metadata(
                                    target_page, ticker, symbol_search_info
                                )
                            )
                        if skip_reason:
                            logger.warning("Ticker %s nie istnieje. Pomijam...", ticker)
                            results_buf.record_skipped(ticker, skip_reason)
                            for interval in intervals:
                                update_state(ticker, interval)
                            results_buf.flush()
                            continue
                    except Exception as e:
                        raise RuntimeError(
                            f"Błąd podczas pobierania danych dla {ticker}: {e}"
                        ) from e

                    logger.info(
                        "(Spółka: %s | Giełda: %s | Cena: %s)",
                        company_name,
                        exchange or "?",
                        current_price,
                    )

                    is_last_indicator = ind_idx == n_inds - 1

                    for interval in intervals:
                        existing_df = results_buf.dataframe
                        erow = get_row_for_ticker_interval(
                            existing_df, ticker, interval
                        )

                        if (
                            respect_csv_data
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

                        interval_ms = int(
                            _switch_chart_interval(target_page, interval) * 1000
                        )

                        row_data = {
                            "Ticker": ticker,
                            "Company_Name": company_name,
                            "Exchange": exchange,
                            "Current_Price": current_price,
                            "Interval": interval,
                            "Scrape_Status": "",
                            "Scrape_Error": "",
                        }
                        merge_existing_row_into_row_data(
                            row_data,
                            erow,
                            skip_indicator_merge=(
                                is_subset_run and not is_indicator_subset
                            ),
                        )

                        should_parse = (
                            erow is None
                            or not row_has_indicator_data(erow, ind_name)
                            or is_subset_run
                        )
                        if not should_parse:
                            logger.info(
                                "Pomijam wskaźnik %s — już zapisany w CSV dla %s/%s",
                                ind_name,
                                ticker,
                                interval,
                            )
                        else:
                            logger.info(
                                "Odczyt HTML dla wskaźnika: %s (adaptive wait)",
                                ind_name,
                            )
                            wait_ms, parse_ms = _parse_indicators_from_page(
                                target_page,
                                [ind_name],
                                row_data,
                                ticker=ticker,
                                interval=interval,
                                indicator_search=indicator_search,
                            )
                            logger.info(
                                "Krok %s/%s: interwał=%dms wait=%dms parse=%dms",
                                ticker,
                                interval,
                                interval_ms,
                                int(wait_ms * 1000),
                                int(parse_ms * 1000),
                            )

                        if is_last_indicator:
                            if is_indicator_subset and erow is not None:
                                for other_ind in status_indicators:
                                    if other_ind not in indicators:
                                        merge_indicator_into_row(
                                            row_data, erow, other_ind
                                        )
                            apply_final_scrape_status(row_data, status_indicators)
                            row_data.pop("_indicator_errors", None)
                            results_buf.upsert(row_data)
                            update_state(ticker, interval)
                        else:
                            row_data["Scrape_Status"] = ""
                            row_data["Scrape_Error"] = ""
                            row_data.pop("_indicator_errors", None)
                            results_buf.upsert(row_data)

                    results_buf.flush()
                    if is_last_indicator and _fundamentals_during_scrape():
                        try:
                            scrape_tv_fundamentals(target_page, ticker)
                        except Exception as exc:
                            logger.warning(
                                "Fundamentale dla %s — błąd po fazie technicznej: %s",
                                ticker,
                                exc,
                            )

                remove_active_indicator(target_page, ind_name, "faza")

            results_buf.flush()
            elapsed = _scraper_elapsed_seconds(active_elapsed_s, run_t0)
            dh = _format_duration(elapsed)
            logger.info(
                "Zakończono pełny przebieg w %s (%.2fs). Pobrane dane są w: %s",
                dh,
                elapsed,
                current_run_file,
            )
            write_scraper_status(
                "done",
                _format_scraper_progress(
                    len(tickers) - 1,
                    len(tickers),
                    len(indicators) - 1,
                    len(indicators),
                    indicators[-1] if indicators else "",
                ),
                "",
                "",
                duration_seconds=elapsed,
                duration_human=dh,
            )
            if persist_state and os.path.exists(state_file):
                os.remove(state_file)

        except Exception as e:
            elapsed = None
            if run_t0 is not None:
                elapsed = _scraper_elapsed_seconds(active_elapsed_s, run_t0)
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


def scrape_tv_fundamentals(page, ticker: str) -> Dict[str, Any]:
    """Pobiera fundamentale (yfinance + opcjonalny TV HTTP fallback) i zapisuje do CSV."""
    from fundamentals import fetch_fundamentals

    cfg: Dict[str, Any] = {}
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            cfg = json.load(f) or {}
    fund_cfg = cfg.get("fundamentals") or {}
    if not fund_cfg.get("enabled", True):
        return {}

    cache_path = os.path.join("data", ".fundamentals_cache.json")
    ttl = float(fund_cfg.get("cache_ttl_hours", 24))
    use_tv = fund_cfg.get("tv_fallback", True)
    data = fetch_fundamentals(
        ticker,
        tv_fallback_page=None,
        tv_http_fallback=use_tv,
        cache_path=cache_path,
        ttl_hours=ttl,
        force_refresh=False,
    )
    normalized = {
        k: ("N/A" if v is None else str(v))
        for k, v in data.items()
        if k.startswith("Fund_")
    }
    save_fundamentals_row({"Ticker": ticker, **normalized})
    logger.info(
        "Fundamentale %s: source=%s PE=%s",
        ticker,
        data.get("Fund_Source"),
        data.get("Fund_PE"),
    )
    return data


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
        help="Specify a single indicator to run (e.g., PCA); alias for --indicators",
    )
    parser.add_argument(
        "--indicators",
        type=str,
        help="Comma-separated indicators to run (e.g., MacD,HTS Panel)",
    )
    parser.add_argument(
        "--cdp-port",
        type=int,
        default=None,
        metavar="PORT",
        help="Port zdalnego debugowania przeglądarki (domyślnie: TV_CDP_PORT, potem cdp_port z JSON, 9222)",
    )
    parser.add_argument(
        "--no-data-only",
        action="store_true",
        help="Run resumable no-data refresh (persists scraper_state.json checkpoints)",
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

    ALL_CONFIG_INDICATORS = list(INDICATORS)
    try:
        INDICATORS, ALL_CONFIG_INDICATORS, is_indicator_subset = resolve_run_indicators(
            ALL_CONFIG_INDICATORS,
            cli_indicators=args.indicators,
            cli_indicator=args.indicator,
        )
    except ValueError as exc:
        logger.error("%s", exc)
        raise SystemExit(2) from exc

    cdp_port = resolve_cdp_port(config, cli_port=args.cdp_port)

    no_data_only = bool(args.no_data_only)
    is_partial = False
    if args.ticker:
        TICKERS = [t.strip() for t in args.ticker.split(",")]
        is_partial = True
    if args.interval:
        INTERVALS = [args.interval]
        is_partial = True
    if args.indicator or args.indicators or os.environ.get("TV_SCRAPER_INDICATORS"):
        is_partial = True

    n_tickers = max(len(TICKERS), 1)
    n_inds = max(len(INDICATORS), 1)
    ind0 = INDICATORS[0] if INDICATORS else ""
    progress_str, eta_seconds, eta_label, eta_total_label = _build_running_scraper_progress(
        0, n_tickers, 0, n_inds, ind0
    )
    write_scraper_status(
        "running",
        progress_str,
        current_indicator=ind0,
        eta_seconds=eta_seconds,
        eta_label=eta_label or None,
        eta_total_label=eta_total_label or None,
    )
    try:
        run_scraper(
            TICKERS,
            INTERVALS,
            INDICATORS,
            port=cdp_port,
            is_partial=is_partial,
            is_indicator_subset=is_indicator_subset,
            all_config_indicators=ALL_CONFIG_INDICATORS,
            no_data_only=no_data_only,
        )
    except Exception:
        # Błąd i ewentualny czas do `scraper_status.json` zapisuje już `run_scraper`.
        raise
