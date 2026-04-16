"""Manual debug: extract price from TradingView tab title via CDP (run Brave with --remote-debugging-port=9222)."""
from playwright.sync_api import sync_playwright


def main():
    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp("http://localhost:9222")
            context = browser.contexts[0]
            page = None
            for pg in context.pages:
                if "tradingview" in pg.url.lower():
                    page = pg
                    break

            if not page:
                print("No tradingview page found")
                return

            title_text = page.title()
            print("Title text is:", title_text)

            parts = title_text.split(" ")
            if len(parts) >= 2:
                price = parts[1]
                print(f"Extracted Price: {price}")

        except Exception as e:
            print("Error:", e)


if __name__ == "__main__":
    main()
