# save_player_playwright.py
import asyncio
from pathlib import Path
from playwright.async_api import async_playwright

PLAYER_ID = "492796533"
OUT = Path(__file__).with_name(f"player_{PLAYER_ID}_raw.html")

async def main():
    url = f"https://wos-giftcode.centurygame.com/player/{PLAYER_ID}"
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1200, "height": 900},
            locale="en-US"
        )
        page = await context.new_page()
        try:
            # Go to the page and wait for network to be idle
            resp = await page.goto(url, wait_until="networkidle", timeout=30000)
            status = resp.status if resp else 'no-response'
            print("HTTP status:", status)
            # Optionally wait a bit if the site loads dynamic content
            await page.wait_for_timeout(1000)
            content = await page.content()
            OUT.write_text(content, encoding="utf-8")
            print("Saved rendered HTML to:", OUT)
        except Exception as e:
            print("Error fetching page:", e)
        finally:
            await context.close()
            await browser.close()

if __name__ == "__main__":
    asyncio.run(main())