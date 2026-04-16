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

Kolumny zależą od wskaźników; dodatkowo:

- **`Scrape_Status`**: `OK` dla normalnego wiersza, **`SKIPPED`** gdy ticker został pominięty (nie znaleziono symbolu / błędny symbol).
- **`Scrape_Error`**: przy `SKIPPED` — krótki opis powodu; przy `OK` zwykle puste.

W panelu web tickery z `SKIPPED` są wizualnie oznaczone (czerwona ramka + komunikat).

## Automatyczny dzienny odczyt (harmonogram)

W **`scraper_config.json`** (lub w panelu **Konfiguracja**) możesz ustawić:

```json
"auto_schedule": {
  "enabled": true,
  "hour": 7,
  "minute": 30
}
```

O ustalonej **godzinie lokalnej** (zegar systemowy) aplikacja uruchomi `tv_scraper.py` z **pełną listą tickerów** z konfiguracji — tak jak przycisk „Uruchom wszystkie”.  
**Musisz mieć stale uruchomiony proces `uvicorn`** oraz realną możliwość działania scrapera (Brave z CDP + karta TradingView). Harmonogram nie uruchamia przeglądarki za Ciebie.

## Dane pomocnicze

- **`data/`** — opcjonalnie eksport watchlisty (np. `Portfel_Watchlist_*.csv`); pliki CSV z tego katalogu są domyślnie ignorowane przez Git — nie commituj prywatnych list bez potrzeby.
- Zrzuty DOM do debugowania zapisuj pod **`data/tv_dom_dump.html`** (ścieżka ignorowana w repozytorium); skrypt `scripts/get_dom.py` tworzy ten plik w `data/`.

## Testy

```bash
pytest tests/ -q
```

Testy jednostkowe nie wymagają uruchomionej przeglądarki. Skrypty integracyjne Playwright w `tests/` są wyłączone z domyślnego zbierania testów (patrz `tests/conftest.py`).

## Jak to działa w skrócie

1. Playwright łączy się z przeglądarką przez **CDP** (`localhost:9222`).
2. Dla każdego tickera skrypt symuluje wpisywanie symbolu, zmianę interwału, dodaje kolejne wskaźniki z konfiguracji, czyta HTML legendy, parsuje wartości, usuwa wskaźnik i zapisuje wiersz do CSV.
3. Panel web łączy wyniki z watchlistą (jeśli jest plik w `data/`) i wyświetla wykres PCA oraz status scrapera.
