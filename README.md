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
  "run_on_startup": true
}
```

- **`enabled` + `hour` / `minute`** — codziennie o tej **godzinie lokalnej** uruchamiany jest pełny przebieg (jak „Uruchom wszystkie”).
- **`run_on_startup`** (domyślnie `true`) — przy **każdym starcie** `uvicorn` po ok. **15 sekundach** uruchamiany jest ten sam pełny odczyt, żeby od razu zaktualizować plik wynikowy na **bieżący dzień**. Wyłącz (`false`), jeśli nie chcesz długiego scrapowania przy każdym restarcie serwera podczas developmentu.

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

## Jak to działa w skrócie

1. Playwright łączy się z przeglądarką przez **CDP** (`localhost:9222`).
2. Dla każdego tickera skrypt symuluje wpisywanie symbolu, zmianę interwału, dodaje kolejne wskaźniki z konfiguracji, czyta HTML legendy, parsuje wartości, usuwa wskaźnik i zapisuje wiersz do CSV.
3. Panel web łączy wyniki z watchlistą (jeśli jest plik w `data/`) i wyświetla wykres PCA oraz status scrapera.
