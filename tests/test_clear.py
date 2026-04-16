import time
import re
from playwright.sync_api import sync_playwright

def test_clear(port=9222):
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
                return

            target_page.bring_to_front()
            
            # Krok 1: Wyczyść globalnie
            print("[*] Klikam z paska po lewej ikonę kosza...")
            options_btn = target_page.locator('button[aria-label="Usuń opcje"], button[aria-label="Remove options"]')
            if options_btn.count() > 0:
                print("Mam przycisk Opcji Kosza!")
                options_btn.first.click(force=True)
                time.sleep(1)
                
                menu_items = target_page.locator('[data-role="menuitem"]').all()
                for el in menu_items:
                    text = el.inner_text().strip()
                    if re.search(r"Usuń.*wskaźnik|Remove.*indicator", text, re.IGNORECASE) and "rysun" not in text.lower() and "drawing" not in text.lower():
                        print(f"[+] Znalazłem w menu i klikam: '{text}'")
                        el.click(force=True)
                        time.sleep(1)
                        break
            else:
                print("Brak obcji kosza na pasku, odpalam fallback")
                
            time.sleep(2)
            print("[+] Dodaję MACD...")
            target_page.keyboard.press("/")
            time.sleep(1)
            target_page.keyboard.type("MACD", delay=100)
            time.sleep(2)
            target_page.keyboard.press("ArrowDown")
            time.sleep(0.5)
            target_page.keyboard.press("Enter")
            time.sleep(1)
            target_page.keyboard.press("Escape")
            time.sleep(3)
            
            print("Pobieram legend-source-item:")
            items = target_page.query_selector_all('div[data-qa-id="legend-source-item"]')
            for item in items:
                title_el = item.query_selector('div[data-qa-id="title-wrapper legend-source-title"]')
                if title_el:
                    print("TITLE EXTRACTED:", title_el.inner_text())
                
        except Exception as e:
            print(f"[-] Błąd: {e}")

if __name__ == "__main__":
    test_clear()
