import time
from playwright.sync_api import sync_playwright

def test_dynamic_indicators(port=9222):
    print(f"[*] Łączenie z przeglądarką na porcie {port}...")
    
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
                print("[-] Nie znaleziono otwartej karty TradingView.")
                return

            print(f"[*] Podłączono do karty: {target_page.title()}")
            target_page.bring_to_front()
            
            # Skupiamy się na ciele strony
            target_page.locator('body').click(force=True)
            time.sleep(1)
            
            indicators_to_test = ["PCA", "HTS Panel"]
            
            for ind_name in indicators_to_test:
                print(f"--- Testuję dodawanie wskaźnika: {ind_name} ---")
                
                # 1. Otwieramy okno wskaźników (skrót '/')
                print("  Otwieram okno wskaźników...")
                target_page.keyboard.press("/")
                time.sleep(2)
                
                # 2. Wpisujemy nazwę wskaźnika
                print(f"  Wyszukuję: {ind_name}")
                target_page.keyboard.type(ind_name, delay=100)
                time.sleep(3) # Czekamy na wyniki wyszukiwania
                
                # 3. Wybieramy pierwszy wynik
                # Okno wyszukiwania ma listę. Najlepiej nacisnąć Enter, jeśli TV to obsługuje, 
                # lub kliknąć w pierwszy pasujący element. Sprawdzimy Enter.
                print("  Wybieram pierwszy wynik (Enter)...")
                target_page.keyboard.press("Enter")
                time.sleep(1)
                
                # Zamykamy okno wskaźników
                target_page.keyboard.press("Escape")
                
                print("  Czekam na załadowanie wskaźnika na wykresie...")
                time.sleep(4)
                
                # ... tutaj byłby parse_indicators ...
                print("  [SYMULACJA] Parsowanie wartości...")
                
                # 4. Usuwamy wszystkie wskaźniki z wykresu
                # Najlepiej usunąć wszystko przez skrót klawiszowy lub kliknięcie w ikonę kosza, a potem "Usuń wskaźniki".
                # W TradingView jest opcja w prawym kliku "Remove indicators"
                # Ale można sprawdzić, czy działa np. usunięcie obiektu z Legendy
                print("  Usuwam wskaźnik...")
                
                # Poszukajmy przycisku z klasą close / remove w legendzie dla tego wskaźnika
                # Selektor: div[data-qa-id="legend-source-item"] button[data-name="legend-remove-action"]
                remove_buttons = target_page.query_selector_all('button[data-name="legend-remove-action"]')
                for btn in remove_buttons:
                    try:
                        btn.click()
                        time.sleep(0.5)
                    except Exception as e:
                        pass
                        
                print("  Usunięto wskaźniki. Gotowy do następnego.\\n")
                
        except Exception as e:
            print(f"[-] Błąd: {e}")

if __name__ == "__main__":
    test_dynamic_indicators()
