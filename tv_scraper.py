import time
import re
import os
import json
import argparse
from datetime import datetime
from typing import Optional
import pandas as pd
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup

STATUS_FILE = "scraper_status.json"
CONFIG_FILE = "scraper_config.json"

def write_scraper_status(status, progress="", current_ticker="", error=""):
    """Write scraper status to JSON file for web UI polling."""
    data = {
        "status": status,
        "progress": progress,
        "current_ticker": current_ticker,
        "error": error,
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    try:
        with open(STATUS_FILE, 'w') as f:
            json.dump(data, f)
    except Exception:
        pass

def get_color_name(rgb_str):
    """Pomocnicza funkcja do nazywania podstawowych kolorów TradingView"""
    if "242, 54, 69" in rgb_str or "red" in rgb_str.lower():
        return "Czerwony"
    if "0, 188, 212" in rgb_str or "blue" in rgb_str.lower():
        return "Niebieski"
    if "8, 153, 129" in rgb_str or "green" in rgb_str.lower():
        return "Zielony"
    if "255, 170, 0" in rgb_str or "orange" in rgb_str.lower():
        return "Pomarańczowy"
    if not rgb_str:
        return "Brak"
    return rgb_str

def parse_indicators(html_content, indicators_to_find):
    """Pobiera i parsuje wartości wskaźników z html dla podanej listy nazw"""
    soup = BeautifulSoup(html_content, 'lxml')
    legend_items = soup.find_all('div', attrs={'data-qa-id': 'legend-source-item'})
    
    results = {}
    for ind in indicators_to_find:
        results[f"{ind}_Values"] = "Brak danych na wykresie"
    
    # Specjalne pola ze względu na specyfikę PCA (gdzie chcemy wyodrębnić główny sygnał)
    results["PCA_Value"] = None
    results["PCA_Color"] = None
    
    for item in legend_items:
        title_el = item.find('div', attrs={'data-qa-id': 'title-wrapper legend-source-title'})
        if not title_el:
            continue
            
        title_text = title_el.get_text(strip=True)
        
        # Iterujemy przez naszą listę wskaźników, żeby sprawdzić czy ten bloczek go dotyczy
        for ind_name in indicators_to_find:
            # Używamy fragmentów nazw (np. PCA-RI albo pełnej nazwy)
            if ind_name.lower() in title_text.lower() or (ind_name == 'PCA' and ('PCA-RI' in title_text or 'PCA Risk' in title_text)):
                
                # --- SPECJALNE PARSOWANIE DLA PCA ---
                if ind_name == 'PCA':
                    values = []
                    for div in item.find_all('div'):
                        classes = div.get('class', [])
                        if any('valueValue' in c for c in classes) or any('valueItem' in c for c in classes):
                            text = div.get_text(strip=True)
                            style = div.get('style', '')
                            if text and text != '∅':
                                values.append({'text': text, 'style': style})
                    
                    if values:
                        last_val = values[-1]
                        results["PCA_Value"] = last_val['text']
                        results["PCA_Color"] = get_color_name(last_val['style'])
                        results[f"{ind_name}_Values"] = f"{last_val['text']} ({results['PCA_Color']})"
                        
                # --- STANDARDOWE PARSOWANIE DLA POZOSTAŁYCH (HTS, itp) ---
                else:
                    values = []
                    # Szukamy pierwszych 4 głównych wartości (które nie są '∅' ani '0')
                    for div in item.find_all('div'):
                        classes = div.get('class', [])
                        if any('valueValue' in c for c in classes) or any('valueItem' in c for c in classes):
                            text = div.get_text(strip=True)
                            style = div.get('style', '')
                            # Filtrujemy smieci z TradingView (często ładuje puste znaczki)
                            if text and text != '∅' and text != '0' and text != '0.00' and text != '0,00':
                                values.append({'text': text, 'color': get_color_name(style)})
                    
                    # W TradingView wartości HTS w DOM są posortowane zazwyczaj tak jak w panelu:
                    # 1. Fast High
                    # 2. Fast Low
                    # 3. Slow High
                    # 4. Slow Low
                    dedup_values = []
                    for v in values:
                        if v not in dedup_values:
                            dedup_values.append(v)
                            
                    if len(dedup_values) >= 4:
                        results[f"{ind_name}_Fast_High"] = f"{dedup_values[0]['text']} ({dedup_values[0]['color']})"
                        results[f"{ind_name}_Fast_Low"] = f"{dedup_values[1]['text']} ({dedup_values[1]['color']})"
                        results[f"{ind_name}_Slow_High"] = f"{dedup_values[2]['text']} ({dedup_values[2]['color']})"
                        results[f"{ind_name}_Slow_Low"] = f"{dedup_values[3]['text']} ({dedup_values[3]['color']})"
                        
                        # Definiujemy prosty Cross: 
                        # Np. jeżeli Fast Low jest niebieskie, to znaczy że jest aktywne/rosnące.
                        # Do bardziej rygorystycznego Crossa musielibyśmy zapamietywać poprzeni stan.
                        results[f"{ind_name}_Trend"] = "Wzrostowy" if dedup_values[1]['color'] == "Niebieski" else "Spadkowy"
                        
                        # Sprawdzenie czy Fast Low przecina Slow High w górę lub Fast High przecina Slow Low w dół 
                        fh = float(dedup_values[0]['text'].replace(' ', '').replace(' ', '').replace(',', '.').replace('−', '-'))
                        fl = float(dedup_values[1]['text'].replace(' ', '').replace(' ', '').replace(',', '.').replace('−', '-'))
                        sh = float(dedup_values[2]['text'].replace(' ', '').replace(' ', '').replace(',', '.').replace('−', '-'))
                        sl = float(dedup_values[3]['text'].replace(' ', '').replace(' ', '').replace(',', '.').replace('−', '-'))
                        
                        cross_info = "Brak Crossa"
                        if fl > sh:
                            cross_info = "BULL CROSS (Wstęgi się przecięły w górę)"
                        elif fh < sl:
                            cross_info = "BEAR CROSS (Wstęgi się przecięły w dół)"
                        
                        results[f"{ind_name}_Cross"] = cross_info
                    else:
                        str_vals = [f"{v['text']} ({v['color']})" for v in dedup_values]
                        results[f"{ind_name}_Values"] = " | ".join(str_vals) if str_vals else "Brak poprawnych danych"

    return results


def record_skipped_ticker(current_run_file: str, ticker: str, reason: str) -> None:
    """Zapisuje jeden wiersz w CSV: ticker pominięty (nie znaleziony / błędny symbol)."""
    row_base = {
        "Ticker": ticker,
        "Company_Name": "—",
        "Current_Price": "",
        "Interval": "-",
        "Scrape_Status": "SKIPPED",
        "Scrape_Error": reason,
    }
    if os.path.exists(current_run_file):
        df = pd.read_csv(current_run_file, encoding="utf-8")
        if "Scrape_Status" in df.columns:
            mask_skip = (df["Ticker"].astype(str) == ticker) & (
                df["Scrape_Status"].astype(str) == "SKIPPED"
            )
            df = df[~mask_skip]
        for col in df.columns:
            if col not in row_base:
                row_base[col] = ""
        ordered = {c: row_base.get(c, "") for c in df.columns}
        out = pd.concat([df, pd.DataFrame([ordered])], ignore_index=True)
    else:
        out = pd.DataFrame([row_base])
    out.to_csv(current_run_file, index=False, encoding="utf-8")
    print(f"    [CSV] Zapisano pominięty ticker {ticker}: {reason}")


def _cell_nonempty(val) -> bool:
    if val is None:
        return False
    try:
        if pd.isna(val):
            return False
    except Exception:
        pass
    s = str(val).strip()
    return s != "" and s.lower() not in ("nan", "none")


def _value_counts_as_indicator_data(val, col: str) -> bool:
    """Puste placeholdery parsowania nie liczą się jako 'są dane wskaźnika'."""
    if not _cell_nonempty(val):
        return False
    s = str(val).strip().lower()
    if "brak danych na wykresie" in s:
        return False
    if "brak poprawnych danych" in s:
        return False
    # Samo słowo „Brak” w Trend/Cross itd. — brak faktycznego pomiaru, nie mylić z dłuższym opisem
    if s == "brak":
        return False
    if col == "PCA_Values" and s in ("ok", "—", "-"):
        return False
    return True


def row_has_indicator_data(row, ind_name: str) -> bool:
    """Czy w wierszu CSV są zapisane dane dla wskaźnika (dowolna powiązana kolumna)."""
    if row is None:
        return False
    ind_name = (ind_name or "").strip()
    for col in row.index:
        c = str(col)
        if c in ("Scrape_Status", "Scrape_Error"):
            continue
        if ind_name == "PCA":
            if c in ("PCA_Values", "PCA_Value", "PCA_Color") and _value_counts_as_indicator_data(
                row[col], c
            ):
                return True
        elif c.startswith(ind_name):
            if _value_counts_as_indicator_data(row[col], c):
                return True
        elif ind_name.lower() in c.lower() and _value_counts_as_indicator_data(row[col], c):
            return True
    return False


def row_interval_complete(row, indicators) -> bool:
    """Wiersz OK dla (ticker, interwał): SKIPPED = nie dotykamy; OK = wszystkie wskaźniki z konfiguracji."""
    if row is None:
        return False
    raw = row["Scrape_Status"] if "Scrape_Status" in row.index else None
    if raw is not None and pd.notna(raw):
        st = str(raw).strip().upper()
    else:
        st = ""
    if st == "SKIPPED":
        return True
    if st and st not in ("OK",):
        return False
    for ind in indicators:
        if not row_has_indicator_data(row, ind):
            return False
    return True


def load_results_dataframe(path: Optional[str]):
    if not path or not os.path.exists(path):
        return None
    try:
        return pd.read_csv(path, encoding="utf-8")
    except Exception as e:
        print(f"[-] Nie można odczytać CSV wyników ({path}): {e}")
        return None


def get_row_for_ticker_interval(df, ticker: str, interval: str):
    if df is None or "Ticker" not in df.columns or "Interval" not in df.columns:
        return None
    m = (df["Ticker"].astype(str) == str(ticker)) & (df["Interval"].astype(str) == str(interval))
    sub = df.loc[m]
    if sub.empty:
        return None
    return sub.iloc[0]


def ticker_marked_skipped_for_day(df, ticker: str) -> bool:
    if df is None or "Ticker" not in df.columns or "Scrape_Status" not in df.columns:
        return False
    sub = df[df["Ticker"].astype(str) == str(ticker)]
    if sub.empty:
        return False
    return (sub["Scrape_Status"].astype(str).str.upper() == "SKIPPED").any()


def ticker_fully_done_in_csv(df, ticker: str, intervals, indicators) -> bool:
    """Ticker nie wymaga pomiaru: wszystkie interwały kompletne albo jeden wiersz SKIPPED na dziś."""
    if df is None:
        return False
    if ticker_marked_skipped_for_day(df, ticker):
        return True
    for interval in intervals:
        row = get_row_for_ticker_interval(df, ticker, interval)
        if not row_interval_complete(row, indicators):
            return False
    return True


def merge_existing_row_into_row_data(row_data: dict, erow) -> None:
    if erow is None:
        return
    for col in erow.index:
        if col in ("Ticker", "Interval"):
            continue
        try:
            v = erow[col]
            if pd.notna(v):
                row_data[str(col)] = v
        except Exception:
            pass


def apply_final_scrape_status(row_data: dict, indicators) -> None:
    _ser = pd.Series(row_data)
    if all(row_has_indicator_data(_ser, ind) for ind in indicators):
        row_data["Scrape_Status"] = "OK"
        row_data["Scrape_Error"] = ""
    else:
        row_data["Scrape_Status"] = "NO_DATA"
        row_data["Scrape_Error"] = (
            "Brak danych wskaźników na wykresie "
            "(np. niewidoczna legenda, zły symbol lub błąd parsowania)."
        )


def add_indicator_to_chart(target_page, ind_name: str, ticker: str) -> None:
    """Otwiera modal wskaźników, wybiera pierwszy wynik, zamyka modal."""
    target_page.keyboard.press("/")
    time.sleep(2)
    target_page.keyboard.type(ind_name, delay=100)
    time.sleep(3)
    try:
        target_page.wait_for_selector(
            'div[data-role="list-item"]', state="visible", timeout=3000
        )
        target_page.locator('div[data-role="list-item"]').first.click(force=True)
    except Exception as e:
        raise RuntimeError(
            f"Zbyt długi czas oczekiwania na listę wskaźników ({ind_name}) dla {ticker}. Błąd: {e}"
        )
    time.sleep(1)
    target_page.keyboard.press("Escape")
    print("        Czekam na przeliczenie wskaźnika (4s)...")
    time.sleep(4)


def remove_active_indicator(target_page, ind_name: str, ticker: str) -> None:
    """Usuwa aktywny wskaźnik z wykresu (menu Opcje)."""
    print("        Usuwam wskaźnik, by oczyścić widok...")
    try:
        options_btn = target_page.locator(
            'button[aria-label="Usuń opcje"], button[aria-label="Remove options"]'
        )
        if options_btn.count() > 0:
            options_btn.first.click(force=True)
            time.sleep(0.5)
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
        raise RuntimeError(
            f"Błąd usuwania wskaźnika '{ind_name}' dla {ticker}: {e}"
        )


CSV_META_COLUMNS = [
    "Ticker",
    "Company_Name",
    "Current_Price",
    "Interval",
    "Scrape_Status",
    "Scrape_Error",
]


def _order_result_columns(columns) -> list:
    """Stała kolejność: meta + pozostałe alfabetycznie (kolumny wskaźników)."""
    cols = list(columns)
    meta_set = set(CSV_META_COLUMNS)
    rest = sorted([c for c in cols if c not in meta_set])
    ordered = [c for c in CSV_META_COLUMNS if c in cols]
    for c in rest:
        if c not in ordered:
            ordered.append(c)
    for c in cols:
        if c not in ordered:
            ordered.append(c)
    return ordered


def _ensure_meta_columns(df: pd.DataFrame) -> pd.DataFrame:
    for c in CSV_META_COLUMNS:
        if c not in df.columns:
            df[c] = ""
    return df[_order_result_columns(df.columns)]


def save_results_row(current_run_file: str, row_data: dict) -> None:
    """Upsert jednego wiersza po (Ticker, Interval). Zawsze z kolumnami Scrape_* w nagłówku."""
    row_data = dict(row_data)
    for c in CSV_META_COLUMNS:
        row_data.setdefault(c, "")

    if not os.path.exists(current_run_file):
        df_row = pd.DataFrame([row_data])
        df_row = df_row[_order_result_columns(df_row.columns)]
        df_row.to_csv(current_run_file, index=False, mode="w", encoding="utf-8")
        return
    try:
        df_existing = pd.read_csv(current_run_file, encoding="utf-8", on_bad_lines="skip")
        df_existing = _ensure_meta_columns(df_existing)
        mask = (df_existing["Ticker"] == row_data["Ticker"]) & (
            df_existing["Interval"] == row_data["Interval"]
        )
        if mask.any():
            for col, val in row_data.items():
                if col not in df_existing.columns:
                    df_existing[col] = ""
                df_existing.loc[mask, col] = val
        else:
            df_new_row = pd.DataFrame([row_data])
            for c in df_existing.columns:
                if c not in df_new_row.columns:
                    df_new_row[c] = ""
            for c in df_new_row.columns:
                if c not in df_existing.columns:
                    df_existing[c] = ""
            df_existing = pd.concat([df_existing, df_new_row], ignore_index=True)
        df_existing = df_existing[_order_result_columns(df_existing.columns)]
        df_existing.to_csv(current_run_file, index=False, encoding="utf-8")
    except Exception as e:
        print(f"[-] Błąd bezpiecznego zapisu (update) pliku CSV: {e}. Fallback do append...")
        df_row = pd.DataFrame([row_data])
        df_row = df_row[_order_result_columns(df_row.columns)]
        df_row.to_csv(current_run_file, index=False, mode="a", header=False, encoding="utf-8")


def run_scraper(tickers, intervals, indicators, port=9222, is_partial=False):
    print(f"[*] Łączenie z przeglądarką na porcie {port}...")
    
    # --- INCEREMNTAL STATE SETUP ---
    state_file = "scraper_state.json"
    processed_combos = set()
    current_run_file = None

    if not is_partial and os.path.exists(state_file):
        try:
            with open(state_file, 'r') as f:
                state = json.load(f)
                # Load previously processed combinations if resuming the same file
                if 'current_file' in state and os.path.exists(state['current_file']):
                    current_run_file = state['current_file']
                    processed_combos = set(tuple(x) for x in state.get('processed', []))
                    print(f"[*] Wznawiam pracę z poprzedniej sesji. Plik: {current_run_file}")
                    print(f"    (Pominięto {len(processed_combos)} już zbadanych kombinacji ticker/interwał)")
        except Exception as e:
            print(f"[-] Błąd odczytu pliku stanu: {e}")

    # Initialize a new output file if starting fresh
    if not current_run_file:
        os.makedirs("results", exist_ok=True)
        date_str = datetime.now().strftime("%Y-%m-%d")
        current_run_file = os.path.join("results", f"tradingview_results_{date_str}.csv")
        # Create empty CSV with headers later based on the first row's keys
        print(f"[*] Rozpoczynam nową sesję pobierania. Plik docelowy: {current_run_file}")

    def update_state(ticker_val, interval_val):
        """Helper to save the current progress to file"""
        if is_partial:
            return
        processed_combos.add((ticker_val, interval_val))
        with open(state_file, 'w') as f:
            json.dump({
                "current_file": current_run_file,
                "processed": list(processed_combos)
            }, f)

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
                raise RuntimeError("Nie znaleziono otwartej karty TradingView w przeglądarce podpiętej pod port 9222.")

            # Dodajemy nasłuchiwanie na dialogi by zapobiec wywalaniu skryptu
            target_page.on("dialog", lambda dialog: dialog.accept())

            print(f"[*] Podłączono do karty: {target_page.title()}")
            target_page.bring_to_front()
            
            # Wstępne czyszczenie wykresu ze starych wskaźników przed rozpoczęciem pętli
            print("[*] Czyszczę wykres ze starych wskaźników przed pomiarem...")
            try:
                options_btn = target_page.locator('button[aria-label="Usuń opcje"], button[aria-label="Remove options"]')
                if options_btn.count() > 0:
                    options_btn.first.click(force=True)
                    time.sleep(1)
                    menu_items = target_page.locator('[data-role="menuitem"]').all()
                    for el in menu_items:
                        text = el.inner_text().strip()
                        if re.search(r"Usuń.*wskaźnik|Remove.*indicator", text, re.IGNORECASE) and "rysun" not in text.lower() and "drawing" not in text.lower():
                            el.click(force=True)
                            time.sleep(1)
                            break
            except Exception as e:
                print("Nie powiodło się pełne czyszczenie ekranu:", e)

            if not indicators:
                print("[!] Lista wskaźników jest pusta — przerwano.")
                return

            n_inds = len(indicators)

            for ind_idx, ind_name in enumerate(indicators):
                print(
                    f"\n[*] === Faza wskaźnika: {ind_name} ({ind_idx + 1}/{n_inds}) ==="
                )
                print(f"     => Dodaję wskaźnik na wykres (raz na fazę): {ind_name}")
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
                        print(
                            f"[!] Pomijam {ticker} — na dziś w CSV są już wszystkie wymagane dane "
                            f"(lub ticker oznaczony jako SKIPPED)."
                        )
                        for interval in intervals:
                            update_state(ticker, interval)
                        continue

                    all_done_for_ticker = all(
                        (ticker, interval) in processed_combos for interval in intervals
                    )
                    if all_done_for_ticker:
                        print(
                            f"[!] Pomijam cały ticker {ticker} — wszystkie interwały oznaczone w stanie sesji."
                        )
                        continue

                    print(f"\n[+] Przełączam na ticker: {ticker}")
                    target_page.locator("body").click(force=True)
                    time.sleep(0.5)
                    target_page.keyboard.type(ticker, delay=100)
                    time.sleep(1)
                    target_page.keyboard.press("Enter")
                    time.sleep(3)

                    try:
                        search_box = target_page.locator('input[type="search"]')
                        if search_box.count() > 0 and search_box.first.is_visible():
                            print(
                                f"[-] BŁĄD: Ticker {ticker} nie został odnaleziony (okno wyszukiwania wciąż otwarte). Daję na pauzę i pomijam..."
                            )
                            target_page.keyboard.press("Escape")
                            time.sleep(1)
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
                            print(f"[-] BŁĄD: Ticker {ticker} nie istnieje. Pomijam...")
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
                            match = re.search(r"^(.+?)\s+(\d+[\.,]\d+|\d+)", title_core)
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
                        match_price = re.search(r"\s+(\d+[\.,]\d+|\d+)", title_clean)
                        if match_price:
                            current_price = match_price.group(1)
                    except Exception as e:
                        raise RuntimeError(
                            f"Błąd podczas pobierania danych dla {ticker}: {e}"
                        )

                    print(f"    (Spółka: {company_name} | Cena: {current_price})")

                    is_last_indicator = ind_idx == n_inds - 1

                    for interval in intervals:
                        existing_df = load_results_dataframe(current_run_file)
                        erow = get_row_for_ticker_interval(existing_df, ticker, interval)

                        if not is_partial and erow is not None and row_interval_complete(
                            erow, indicators
                        ):
                            print(
                                f"[!] Pomijam {ticker} - {interval} — w CSV jest już komplet wskaźników."
                            )
                            update_state(ticker, interval)
                            continue

                        if (ticker, interval) in processed_combos:
                            if erow is not None and not row_interval_complete(
                                erow, indicators
                            ):
                                print(
                                    f"[!] Sesja wskazywała na {ticker}/{interval}, "
                                    f"ale CSV niepełny — ponawiam pomiar."
                                )
                            elif erow is None:
                                print(
                                    f"[!] Sesja wskazywała na {ticker}/{interval}, brak wiersza w CSV — ponawiam."
                                )
                            else:
                                print(
                                    f"[!] Pomijam {ticker} - {interval} (wznów + CSV OK)."
                                )
                                continue

                        print(f"  -> Ustawiam interwał: {interval}")
                        target_page.keyboard.type(interval, delay=100)
                        time.sleep(1)
                        target_page.keyboard.press("Enter")
                        time.sleep(2)

                        row_data = {
                            "Ticker": ticker,
                            "Company_Name": company_name,
                            "Current_Price": current_price,
                            "Interval": interval,
                            "Scrape_Status": "",
                            "Scrape_Error": "",
                        }
                        merge_existing_row_into_row_data(row_data, erow)

                        if erow is not None and row_has_indicator_data(erow, ind_name):
                            print(
                                f"     => Pomijam wskaźnik {ind_name} — już zapisany w CSV dla tego interwału"
                            )
                        else:
                            print(f"     => Odczyt HTML dla wskaźnika: {ind_name}")
                            print("        Czekam na przeliczenie wskaźnika (4s)...")
                            time.sleep(4)
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
                                    if key != f"{ind_name}_Values" or ind_name != "PCA":
                                        print(f"        [{key}]: {val}")
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

            print(f"\n[+] Zakończono pełny przebieg! Pobrane dane są w: {current_run_file}")
            # Czyszczenie pliku ze stanem przy udanym pełnym przebiegu
            if not is_partial and os.path.exists(state_file):
                os.remove(state_file)

        except Exception as e:
            print(f"[-] Błąd podczas scrapowania: {e}")
            raise

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="TradingView Web Scraper")
    parser.add_argument("--ticker", type=str, help="Comma-separated tickers to run (e.g., PLTR,FCX)")
    parser.add_argument("--interval", type=str, help="Specify a single interval to run (e.g., 1D)")
    parser.add_argument("--indicator", type=str, help="Specify a single indicator to run (e.g., PCA)")
    args = parser.parse_args()

    # Load config from JSON file
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r') as f:
            config = json.load(f)
        TICKERS = config.get("tickers", [])
        INTERVALS = config.get("intervals", ["1D", "1W", "1M"])
        INDICATORS = config.get("indicators", ["PCA", "HTS Panel", "MacD"])
    else:
        print(f"[!] Config file {CONFIG_FILE} not found, using defaults.")
        TICKERS = ["FCX", "PLTR"]
        INTERVALS = ["1D", "1W", "1M"]
        INDICATORS = ["PCA", "HTS Panel", "MacD"]

    # Override from CLI arguments
    is_partial = False
    if args.ticker:
        TICKERS = [t.strip() for t in args.ticker.split(',')]
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

