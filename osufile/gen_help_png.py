import asyncio
from pathlib import Path
from playwright.async_api import async_playwright

HELP_HTML = Path(__file__).parent / "help.html"
OUT_DIR = Path(__file__).parent

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={"width": 1280, "height": 800})

        # overview 页 (/oh)
        await page.goto(HELP_HTML.as_uri())
        await page.wait_for_timeout(500)
        # 获取实际内容高度
        height = await page.evaluate("document.body.scrollHeight")
        await page.set_viewport_size({"width": 1280, "height": height})
        await page.screenshot(path=str(OUT_DIR / "help.png"), full_page=True)
        print(f"✅ help.png generated ({height}px)")

        # detail 页 (/oh detail)
        await page.goto(HELP_HTML.as_uri() + "?page=detail")
        await page.wait_for_timeout(500)
        height = await page.evaluate("document.body.scrollHeight")
        await page.set_viewport_size({"width": 1280, "height": height})
        await page.screenshot(path=str(OUT_DIR / "detail.png"), full_page=True)
        print(f"✅ detail.png generated ({height}px)")

        await browser.close()

asyncio.run(main())