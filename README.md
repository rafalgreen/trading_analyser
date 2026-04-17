# Trading Analyser

Zestaw do automatycznego odczytu wskaźników z wykresów **TradingView** (np. PCA, HTS Panel, MacD) i przeglądania wyników w **przeglądarce**. Skrypt `tv_scraper.py` korzysta z **Playwright** i podłącza się do już uruchomionej przeglądarki (Brave / Chrome) w trybie zdalnego debugowania. Aplikacja **FastAPI** (`app.py`) serwuje API oraz statyczny panel z listą plików wynikowych, wykresami PCA i konfiguracją.

## Wymagania

- Python 3.10+ (w projekcie często używane jest środowisko wirtualne `venv`)
- **Brave** lub **Chrome** uruchomiony z portem debugowania **9222**
- Otwarta karta z **TradingView** (wykres), gdy działa scraper
- Zależności: `pip install -r requirements.txt`

## Szybki start

```bash
cd /ścieżka/do/trading_analyser
python3 -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
```

### Przeglądarka (macOS, przykład Brave)

```bash
/Applications/Brave\ Browser.app/Contents/MacOS/Brave\ Browser --remote-debugging-port=9222
```

Otwórz w tej sesji kartę z wykresem TradingView. Bez tego scraper zgłosi brak połączenia lub brak karty TV.

### Serwer WWW (panel + API)

```bash
source venv/bin/activate
uvicorn app:app --host 0.0.0.0 --port 8000
```

W przeglądarce: `http://127.0.0.1:8000` — historia plików CSV z katalogu `results/`, karty per ticker, wykres PCA, zakładka konfiguracji (tickery, interwały, wskaźniki, harmonogram).

## Panel web — funkcjonalność

### Karty tickerów (widok główny)

Dla każdego tickera z wybranego pliku wynikowego generowana jest karta z ikonami akcji po prawej stronie nagłówka:

- 🔄 **odśwież** — zleca ponowne pobranie tylko tego tickera (`POST /api/scraper/run` z `tickers=[ticker]`). Spinner gaśnie po zakończeniu runu lub po 60 s bezpiecznika.
- 🕘 **historia** — modal z wykresem historycznym PCA dla wybranego interwału (`1D` / `1W` / `1M`), złączony z danych wszystkich dostępnych plików wynikowych.
- ✏️ **zmiana nazwy** — modal do zmiany symbolu tickera w konfiguracji (np. gdy TradingView rozpoznaje go pod innym zapisem). Stara nazwa znika z bieżącego widoku (ukrywana w `localStorage`), nowy symbol jest automatycznie zlecany do pobrania.

Po każdej karcie widać trzy kolumny interwałów (`1D`, `1W`, `1M`) z sekcjami dla każdego wskaźnika z konfiguracji. Jeśli brakuje danych dla konkretnego wskaźnika / interwału, pojawia się żółty banner „Brak danych dla: …", a jeśli cały ticker nie ma danych — dodatkowo baner „⚠ Brak danych" z podpowiedzią żeby sprawdzić zapis symbolu lub zmienić nazwę. Diagnostyka jest liczona niezależnie od kolumny `Scrape_Status` (`/api/results/{date_id}` zwraca pola `Missing_Indicators` oraz `All_Indicators_Missing`).

Na górze widoku:

- **Pasek wyszukiwania** — filtrowanie po tickerze / nazwie spółki.
- **Sortowanie** — domyślne, po PCA (rosnąco / malejąco), po nazwie tickera.
- **Licznik rekordów** — pokazuje liczbę widocznych / wszystkich wierszy i ewentualnie „N ukrytych po zmianie nazwy · (pokaż)" by przywrócić ukryte karty.
- **Wskaźnik świeżości danych** — kropka kolorowa + wiek pliku („dzisiaj 08:12", „wczoraj", „7 dni temu").

### Zmiana nazwy tickera (`POST /api/tickers/rename`)

- Walidacja symbolu przez regex `^[A-Z0-9._-]{1,20}$`.
- Fuzzy match starej nazwy: najpierw **dokładne trafienie**, potem **bazowy symbol** (prefiks przed pierwszą kropką, np. `LULU.O` ↔ `LULU`). Jeśli w konfigu jest jeden kandydat z tą samą bazą — używany; przy wielu kandydatach endpoint zwraca `409` i wymaga ręcznego rozstrzygnięcia.
- CSV-y historyczne **nie są modyfikowane** (stare wiersze zostają jako audyt).
- W UI stara karta jest ukrywana w `localStorage`, żeby reload jej nie przywrócił; przyciskiem „(pokaż)" przy liczniku rekordów można przywrócić ukryte symbole.

### Zakładka „Konfiguracja"

- **Tickery** — dodawanie / usuwanie symboli (walidacja po regexie jak wyżej).
- **Interwały** i **wskaźniki** — checkboxy.
- **Auto-schedule** — codzienny przebieg o ustawionej godzinie (APScheduler `CronTrigger`).
- **`run_on_startup`** — **domyślnie wyłączone** (patrz niżej sekcję o harmonogramie).
- Przyciski **„Uruchom wszystkie"** i **„Zatrzymaj"** — Uruchom potwierdza akcją w własnym modalu (natywne `confirm()` bywa wyciszane przez przeglądarki po „nie pokazuj kolejnych okien dialogowych"). Stop zabija całą grupę procesów scrapera (SIGTERM → SIGKILL) + fallback przez `pgrep -f tv_scraper.py` dla osieroconych procesów po restarcie serwera.
- **Pasek postępu scrapera** — odświeżany co ~1 s (`GET /api/scraper/status`), pokazuje bieżący ticker i fazę `ticker x/N · wsk. y/M`.

### Watchlista (zakładka „Watchlist")

Jeśli w `data/` jest plik `Portfel_Watchlist_*.csv`, panel dołącza do tickerów pola: `Name`, `Last`, `Market_Cap`, `P/E`, `EPS`, `Beta`, `Revenue`, `Daily_Signal` / `Weekly_Signal` / `Monthly_Signal`, `Chg. %`, `YTD`, `1Y`. W widoku watchlisty można filtrować po sygnale (Strong Buy / Buy / Neutral / Sell / Strong Sell) i interwale — filtry pamiętają się w `localStorage`.

### Powiadomienia i skróty

- **Toasty** informują o starcie / zakończeniu scrapera, `already_running` (z informacją który ticker obecnie leci), sukcesie rename, błędach sieci.
- **Skróty klawiaturowe**: `/` — focus na wyszukiwanie, `Esc` — zamyka aktywny modal / czyści filtr.

### Sam skrypt (CLI)

```bash
python tv_scraper.py
```

Opcje: `--ticker A,B,C` (podzbiór), `--interval 1D`, `--indicator PCA` — patrz `python tv_scraper.py -h`.

Konfiguracja domyślna jest w **`scraper_config.json`**: lista **tickers**, **intervals**, **indicators** oraz opcjonalnie **`auto_schedule`** (patrz niżej).

## Wyniki

Pliki zapisywane są w katalogu **`results/`**, nazwa w stylu:

`tradingview_results_YYYY-MM-DD.csv`

Kolejność kolumn jest stała na początku każdego wiersza (meta), potem kolumny wskaźników z konfiguracji:

1. **`Ticker`**, **`Company_Name`**, **`Current_Price`**, **`Interval`**
2. **`Scrape_Status`**, **`Scrape_Error`**
3. dalej m.in. `PCA_Values`, `HTS Panel_*`, `MacD_*` — zależnie od **`indicators`** w `scraper_config.json`

Znaczenie meta:

- **`Scrape_Status`**: `OK` dla normalnego wiersza, **`SKIPPED`** gdy ticker został pominięty (nie znaleziono symbolu / błędny symbol).
- **`Scrape_Error`**: przy `SKIPPED` — krótki opis powodu; przy `OK` zwykle puste.

W panelu web tickery z `SKIPPED` są wizualnie oznaczone (czerwona ramka + komunikat).

Starsze pliki CSV mogły być zapisane **bez** kolumn `Scrape_Status` / `Scrape_Error` w nagłówku, przez co pole statusu trafiało do złej kolumny (np. `PCA_Values` pokazywało `OK`). Aby wyrównać nagłówek i wiersze do obecnego formatu:

```bash
python3 scripts/repair_results_csv.py results/tradingview_results_YYYY-MM-DD.csv
```

Możesz podać kilka plików naraz. Jeśli nagłówek już zawiera `Scrape_Status`, skrypt nic nie zmienia.

## Automatyczny odczyt (harmonogram + start serwera)

W **`scraper_config.json`** (lub w panelu **Konfiguracja**) pole **`auto_schedule`**:

```json
"auto_schedule": {
  "enabled": true,
  "hour": 8,
  "minute": 0,
  "run_on_startup": false
}
```

- **`enabled` + `hour` / `minute`** — codziennie o tej **godzinie lokalnej** uruchamiany jest pełny przebieg (jak „Uruchom wszystkie").
- **`run_on_startup`** — **domyślnie `false`**. Włącz (`true`), jeśli chcesz żeby `uvicorn` po każdym starcie automatycznie odpalał pełny scrape po ~15 s. W trakcie developmentu (częste restarty) to zaskakuje i preemptuje ręczne „rescrape" pojedynczych tickerów — dlatego domyślnie jest wyłączone.

**Musisz mieć uruchomiony `uvicorn`** oraz możliwość działania scrapera (Brave z CDP + karta TradingView). Harmonogram ani start nie otwierają samej przeglądarki.

## Dane pomocnicze

- **`data/`** — opcjonalnie eksport watchlisty (np. `Portfel_Watchlist_*.csv`); pliki CSV z tego katalogu są domyślnie ignorowane przez Git — nie commituj prywatnych list bez potrzeby.
- Zrzuty DOM do debugowania zapisuj pod **`data/tv_dom_dump.html`** (ścieżka ignorowana w repozytorium); skrypt `scripts/get_dom.py` tworzy ten plik w `data/`.
- **`scripts/repair_results_csv.py`** — naprawa starych plików wynikowych (patrz sekcja [Wyniki](#wyniki)).
- **`scripts/macos-daily-scraper.example.plist`** — przykład **launchd** na macOS: codzienne `python tv_scraper.py` o wybranej godzinie (dostosuj ścieżki i `WorkingDirectory`, potem `launchctl load`).

## Testy

```bash
python3 -m pytest tests/ -q
```

Testy jednostkowe nie wymagają uruchomionej przeglądarki. Skrypty integracyjne Playwright w `tests/` są wyłączone z domyślnego zbierania testów (patrz `tests/conftest.py`).

## Struktura modułów

- **`tv_scraper.py`** — logika scrapowania (Playwright, TradingView, parsowanie legendy).
- **`app.py`** — FastAPI: serwowanie `/api/*`, zarządzanie konfiguracją i procesem scrapera, harmonogram.
- **`results_store.py`** — wspólny moduł I/O dla plików CSV z wynikami: stałe kolumn meta (`CSV_META_COLUMNS`), zapis `upsert` po (Ticker, Interval), odczyt odporny na uszkodzone wiersze, predykaty kompletności wiersza. Używany przez `tv_scraper.py`, `app.py` i `scripts/repair_results_csv.py`.
- **`static/`** — panel web (HTML/CSS/JS).

## Logging

Scraper i API używają standardowej biblioteki `logging`. Poziom kontroluje zmienna środowiskowa:

```bash
TV_LOG_LEVEL=DEBUG uvicorn app:app --host 0.0.0.0 --port 8000
```

Dopuszczalne wartości: `DEBUG`, `INFO` (domyślnie), `WARNING`, `ERROR`.

Gdy scraper jest uruchamiany przez API (`POST /api/scraper/run` lub auto-schedule), jego **stdout/stderr** lądują w pliku **`scraper.log`** w katalogu projektu — każdy run zaczyna się nagłówkiem `===== <timestamp> start tickers=… =====`. Plik jest rotowany po przekroczeniu 2 MB (→ `scraper.log.1`). `scraper.log*` są w `.gitignore`.

## REST API (skrót)

- `GET /api/history` — lista plików wynikowych (data + liczba rekordów).
- `GET /api/results/{date_id}` — wiersze z CSV dla danego dnia + per-row `Missing_Indicators` / `All_Indicators_Missing`.
- `GET /api/ticker/{ticker}/history?interval=1D` — historia PCA dla tickera (dla modala wykresu historycznego).
- `GET /api/config` / `POST /api/config` — pełna konfiguracja (tickery, interwały, wskaźniki, `auto_schedule`).
- `POST /api/scraper/run` — uruchomienie scrapera, `body: {"tickers": ["LULU"]}` albo `[]` dla pełnego przebiegu. Zwraca `started` / `already_running` / `error`.
- `POST /api/scraper/stop` — zatrzymanie (zabija grupę procesów + fallback przez `pgrep`).
- `GET /api/scraper/status` — `status` (`idle` / `running` / `done` / `error`), `progress`, `current_ticker`, `pid`.
- `POST /api/tickers/rename` — `body: {"old": "LULU.O", "new": "LULU"}`, fuzzy-match po bazowym symbolu.
- `GET /api/watchlist` — dane z najnowszego `Portfel_Watchlist_*.csv` (jeśli jest).

## Jak to działa w skrócie

1. Playwright łączy się z przeglądarką przez **CDP** (`localhost:9222`).
2. Dla każdego tickera skrypt symuluje wpisywanie symbolu, zmianę interwału, dodaje kolejne wskaźniki z konfiguracji, czyta HTML legendy, parsuje wartości, usuwa wskaźnik i zapisuje wiersz do CSV.
3. Panel web łączy wyniki z watchlistą (jeśli jest plik w `data/`) i wyświetla wykres PCA oraz status scrapera.
