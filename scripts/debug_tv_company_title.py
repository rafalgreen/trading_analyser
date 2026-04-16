"""Manual debug: parse company/ticker from TradingView chart title via CDP."""
import re

from playwright.sync_api import sync_playwright


def main():
    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp("http://localhost:9222")
            context = browser.contexts[0]
            for page in context.pages:
                if "tradingview.com/chart" in page.url.lower():
                    title_text = page.title()
                    print("Raw Title:", title_text)

                    try:
                        core = title_text.split(" —")[0].split(" -")[0].strip()
                        print("Core:", core)

                        match = re.search(r"^([A-Za-z0-9.]+)\s+(\d+[.,]\d+|\d+)", core)
                        if match:
                            print("Extracted Ticker/Name from Regex:", match.group(1))

                    except Exception as e:
                        print("M2:", e)

            browser.close()
        except Exception as e:
            print(e)


if __name__ == "__main__":
    main()
