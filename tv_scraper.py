import time
import re
import os
import json
import argparse
import logging
from datetime import datetime
from typing import Optional

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

SLEEP_AFTER_INDICATOR_MODAL_S = 2
SLEEP_AFTER_INDICATOR_QUERY_S = 3
SLEEP_AFTER_INDICATOR_COMPUTE_S = 4
SLEEP_AFTER_TICKER_ENTER_S = 3
SLEEP_AFTER_INTERVAL_CHANGE_S = 2
SLEEP_AFTER_SMALL_ACTION_S = 1
SLEEP_AFTER_MICRO_ACTION_S = 0.5

logger = logging.getLogger("tv_scraper")


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


def write_scraper_status(status, progress="", current_ticker="", error=""):
    """Write scraper status to JSON file for web UI polling."""
    data = {
        "status": status,
        "progress": progress,
        "current_ticker": current_ticker,
        "error": error,
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
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


def parse_indicators(html_content, indicators_to_find):
    """Pobiera i parsuje wartości wskaźników z html dla podanej listy nazw."""
    soup = BeautifulSoup(html_content, "lxml")
    legend_items = soup.find_all("div", attrs={"data-qa-id": "legend-source-item"})

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
            matches = ind_name.lower() in title_text.lower() or (
                ind_name == "PCA"
                and ("PCA-RI" in title_text or "PCA Risk" in title_text)
            )
            if not matches:
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
    for div in item.find_all("div"):
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
    logger.info("Łączenie z przeglądarką na porcie %s...", port)

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
        try:
            browser = p.chromium.connect_over_cdp(f"http://localhost:{port}")
            default_context = browser.contexts[0]

            target_page = None
            for page in default_context.pages:
                if "tradingview.com" in page.url or "TradingView" in page.title():
                    target_page = page
                    break

            if not target_page:
                raise RuntimeError(
                    "Nie znaleziono otwartej karty TradingView w przeglądarce podpiętej pod port 9222."
                )

            target_page.on("dialog", lambda dialog: dialog.accept())

            logger.info("Podłączono do karty: %s", target_page.title())
            target_page.bring_to_front()

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

                        try:
                            company_name = target_page.locator(
                                'div[data-name="legend-source-description"]'
                            ).first.inner_text(timeout=2000)
                        except Exception:
                            title_core = (
                                title_text.split(" Wskaźnik")[0]
                                .split(" Wykres")[0]
                                .split(" —")[0]
                                .split(" -")[0]
                                .strip()
                            )
                            match = re.search(
                                r"^(.+?)\s+(\d+[\.,]\d+|\d+)", title_core
                            )
                            if match:
                                company_name = match.group(1).strip()
                            else:
                                company_name = title_core.split(" ")[0]

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
                        "(Spółka: %s | Cena: %s)", company_name, current_price
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
                                "Odczyt HTML dla wskaźnika: %s (czekam %ss)",
                                ind_name,
                                SLEEP_AFTER_INDICATOR_COMPUTE_S,
                            )
                            time.sleep(SLEEP_AFTER_INDICATOR_COMPUTE_S)
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

            logger.info(
                "Zakończono pełny przebieg! Pobrane dane są w: %s", current_run_file
            )
            if not is_partial and os.path.exists(state_file):
                os.remove(state_file)

        except Exception as e:
            logger.error("Błąd podczas scrapowania: %s", e)
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
    args = parser.parse_args()

    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r") as f:
            config = json.load(f)
        TICKERS = config.get("tickers", [])
        INTERVALS = config.get("intervals", ["1D", "1W", "1M"])
        INDICATORS = config.get("indicators", ["PCA", "HTS Panel", "MacD"])
    else:
        logger.warning("Config file %s not found, using defaults.", CONFIG_FILE)
        TICKERS = ["FCX", "PLTR"]
        INTERVALS = ["1D", "1W", "1M"]
        INDICATORS = ["PCA", "HTS Panel", "MacD"]

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
        run_scraper(TICKERS, INTERVALS, INDICATORS, is_partial=is_partial)
        write_scraper_status("done", f"{len(TICKERS)}/{len(TICKERS)}", "")
    except Exception as e:
        write_scraper_status("error", "", "", str(e))
        raise
