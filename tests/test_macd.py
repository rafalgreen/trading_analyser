import time
from playwright.sync_api import sync_playwright

def test_macd(port=9222):
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
            
            print("[*] Szukam przycisków w okolicach legendy...")
            # Zbieramy wszystkie data-name z przycisków na stronie
            names = target_page.evaluate("""() => {
                return Array.from(document.querySelectorAll('div, button, a')).map(el => el.getAttribute('data-name')).filter(n => n && n.includes('legend'));
            }""")
            
            print("Znalazłem data-name z 'legend':", set(names))
            
            # Spróbujmy znaleźć cokolwiek co ma w nazwie toggle, collapse, expand i kliknąć!
            toggle = target_page.query_selector('[data-name="legend-toggle-action"], [data-name="legend-collapse-action"], [data-name="legend-expand-action"]')
            if toggle:
                print("KLIKAM:", toggle.get_attribute("data-name"))
                toggle.click(force=True)
                time.sleep(1)
            else:
                print("NIE ZNALEZIONO TOGGLE!")
                
            print("[*] Ponowna próba usunięcia...")
            items = target_page.query_selector_all('div[data-qa-id="legend-source-item"]')
            for item in reversed(items):
                try:
                    item.hover()
                    time.sleep(0.5)
                    close_btn = item.query_selector('button[data-name="legend-remove-action"]')
                    if close_btn:
                        close_btn.click(force=True)
                        print("[+] Kliknięto KOSZ!")
                        time.sleep(0.5)
                except Exception as e:
                    print("- Błąd hover/kosz:", e)
                    
        except Exception as e:
            print(f"[-] Błąd: {e}")

if __name__ == "__main__":
    test_macd()
