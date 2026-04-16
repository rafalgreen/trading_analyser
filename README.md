# TradingView Scraper Bot

Bot automatyzujący odczyt wartości ze wskaźników z platformy TradingView (m.in. niestandardowych wskaźników takich jak PCA Risk Indicator, HTS Panel), wykorzystujący narzędzie Playwright.

## Co robi ten skrypt?
1. **Działa bez logowania i omija Captcha:** Używa biblioteki Playwright do łączenia się z aktywną sesją Twojej działającej przeglądarki (wspiera Chrome / Brave).
2. **Pełna automatyzacja:** Samoistnie wpisuje nazwy tickerów korzystając z klawiatury, wyszukuje je, a następnie przełącza się pomiędzy wybranymi interwałami (D, W, M).
3. **Dynamiczne Dodawanie i Usuwanie Wskaźników:** Dla każdego tickera i interwału, skrypt sam otwiera wyszukiwarkę wskaźników (skrót `/`), wpisuje nazwę z konfiguracji, dodaje wskaźnik, odczytuje wartości, a następnie usuwa go z wykresu, gwarantując czysty odczyt bez zakłóceń.
4. **Odczyt danych "Data Window" (Legenda):** Wyciąga odpowiednie wartości CSS dla wskaźników i parsuje z nich konkretne stany (np. wartości liczbowe oraz kolory RGB wskazujące na przecięcie wstęg).
5. **Zapis do pliku:** Odczytane sygnały i kolory zapisywane są ustrukturyzowane bezpośrednio do plików z timestampem w nazwie (np. `tradingview_results_2026-03-14_01-00-00.csv`), gwarantując unikalność urobku każdego skanu.

### Przykładowy format wyniku
```csv
Ticker,Interval,PCA_Value,PCA_Color,HTS Panel_Fast_High,HTS Panel_Fast_Low,HTS Panel_Slow_High,HTS Panel_Slow_Low,HTS Panel_Trend,HTS Panel_Cross
BTCUSDT,1D,70,color: rgb(255, 170, 0);,81774,11 (Brak),81774,11 (Niebieski),78024,10 (Brak),78024,10 (Niebieski),Wzrostowy,BULL CROSS (Wstęgi się przecięły w górę)
```
Skrypt potrafi rozpoznawać podstawowe kolory (`Czerwony`, `Niebieski`, `Zielony`), a w przypadku skomplikowanych barw PCA wypluwa dokładną wartość RGB do prostej obróbki np. w Excelu.

## Jak używać bota na codzień?

W pliku `tv_scraper.py` na samym dole w sekcji `if __name__ == "__main__":` znajduje się konfiguracja, w której możesz podać interesujące Cię opcje:
```python
    TICKERS = ["BTCUSDT", "ETHUSDT", "PKN"] # Twoja lista symboli
    INTERVALS = ["1D", "1W", "1M"]
    INDICATORS = ["PCA", "HTS Panel"] # Skrypt przeszuka Data Window i wyciągnie te konkretne wskaźniki
```

**Uruchomienie:**
1. Upewnij się, że Brave (lub Chrome) jest włączony w trybie debugowania. Przykładowo dla przeglądarki Brave na macOS musisz ją uruchomić wpisując w terminal:
   `/Applications/Brave\ Browser.app/Contents/MacOS/Brave\ Browser --remote-debugging-port=9222`
   
   Upewnij się, że masz otwartą jedną główną kartę w tej nowej sesji przeglądarki ze stroną TradingView na dowolnym, pustym instrumencie (bot i tak sam wyczyści wykres ze starych wskaźników przed pracą).
   
2. Odpal skrypt w terminalu:
   ```bash
   cd /Users/rafciu/CursorProjects/trading_analyser
   source venv/bin/activate
   python3 tv_scraper.py
   ```
