import os
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup

def get_dom_structure(port=9222):
    print(f"[*] Łączenie z przeglądarką na porcie {port}...")
    
    with sync_playwright() as p:
        try:
            # Podłączamy się do odpalonej przeglądarki z flagą --remote-debugging-port
            browser = p.chromium.connect_over_cdp(f"http://localhost:{port}")
            
            # Pobieramy pierwszą otwartą stronę / kartę
            default_context = browser.contexts[0]
            
            target_page = None
            for page in default_context.pages:
                if "tradingview.com" in page.url or "TradingView" in page.title():
                    target_page = page
                    break
                    
            if not target_page:
                print("[-] Nie znaleziono otwartej karty TradingView. Otwarte karty:")
                for page in default_context.pages:
                    print(f"  - {page.title()} ({page.url})")
                return

            print(f"[*] Podłączono do karty: {target_page.title()}")
            
            # Pobieramy kod HTML strony
            target_page.wait_for_load_state('networkidle', timeout=10000)
            html_content = target_page.content()
            
            # Formujemy HTML przez BeautifulSoup, aby był czytelniejszy
            soup = BeautifulSoup(html_content, 'lxml')
            
            # Zapisujemy do pliku .html, aby móc to przeanalizować
            output_file = "tv_dom_dump.html"
            with open(output_file, "w", encoding="utf-8") as f:
                f.write(soup.prettify())
                
            print(f"[+] Zapisano zrzut strony do pliku: {os.path.abspath(output_file)}")
            print("[*] Teraz możesz otworzyć ten plik w edytorze kodu i wyszukać słowa 'PCA Risk' lub 'HTS Panel'.")

        except Exception as e:
            print(f"[-] Błąd połączenia: {e}")
            print("[!] Upewnij się, że Brave zostało poprawnie uruchomione z komendą:")
            print("[!] /Applications/Brave\\ Browser.app/Contents/MacOS/Brave\\ Browser --remote-debugging-port=9222")

if __name__ == "__main__":
    get_dom_structure()
