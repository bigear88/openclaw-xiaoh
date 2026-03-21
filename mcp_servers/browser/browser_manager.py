#!/usr/bin/env python3
"""
小虹瀏覽器管理器
所有瀏覽器操作都用有頭 Chrome（headless=False），避免被網站阻擋。

架構：
- VM 上用 Xvfb 虛擬顯示器跑有頭 Chrome
- noVNC 讓你從本機瀏覽器即時看到操作過程
- 同時只允許 1 個瀏覽器實例（記憶體保護）
"""

import asyncio
import base64
import os
import time
from pathlib import Path
from typing import Any, Optional

from playwright.async_api import async_playwright, Browser, Page, BrowserContext

# ============================================================
# 設定
# ============================================================

SCREENSHOTS_DIR = os.environ.get("SCREENSHOTS_DIR", "/tmp/xiaohong-screenshots")
BROWSER_MAX_INSTANCES = 1
BROWSER_TIMEOUT_MS = 60_000  # 單頁最大等待 60s
DEFAULT_VIEWPORT = {"width": 1920, "height": 1080}

# Chrome 啟動參數 — 模擬真人瀏覽器，避免被偵測
CHROME_ARGS = [
    "--disable-blink-features=AutomationControlled",  # 隱藏自動化標記
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-infobars",
    "--disable-extensions",
    "--disable-popup-blocking",
    "--disable-translate",
    "--disable-background-timer-throttling",
    "--disable-backgrounding-occluded-windows",
    "--disable-renderer-backgrounding",
    "--window-size=1920,1080",
    "--start-maximized",
    "--lang=zh-TW",  # 繁體中文
]


class BrowserManager:
    """
    瀏覽器實例管理器。
    確保同時只有 1 個 Chrome 在跑，提供截圖、導航、互動等功能。
    """

    def __init__(self):
        self._playwright = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None
        self._lock = asyncio.Lock()
        self._last_activity = 0.0
        self._screenshot_count = 0

        # 確保截圖目錄存在
        Path(SCREENSHOTS_DIR).mkdir(parents=True, exist_ok=True)

    async def _ensure_browser(self) -> Page:
        """確保瀏覽器已啟動，返回當前 Page"""
        async with self._lock:
            if self._browser is None or not self._browser.is_connected():
                await self._launch_browser()
            self._last_activity = time.time()
            return self._page

    async def _launch_browser(self):
        """
        啟動有頭 Chrome。
        關鍵：headless=False，讓 noVNC 可以看到畫面。
        """
        if self._playwright is None:
            self._playwright = await async_playwright().start()

        # *** 核心：headless=False ***
        # 在 VM 上配合 Xvfb 虛擬螢幕，Windows 直接顯示視窗
        self._browser = await self._playwright.chromium.launch(
            headless=False,  # 🔴 永不使用 headless！
            args=CHROME_ARGS,
            slow_mo=100,  # 慢速操作，方便觀察
        )

        self._context = await self._browser.new_context(
            viewport=DEFAULT_VIEWPORT,
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            locale="zh-TW",
            timezone_id="Asia/Taipei",
            # 模擬真人
            java_script_enabled=True,
            has_touch=False,
            is_mobile=False,
            # 忽略 HTTPS 錯誤
            ignore_https_errors=True,
        )

        # 注入反偵測腳本
        await self._context.add_init_script("""
            // 隱藏 webdriver 標記
            Object.defineProperty(navigator, 'webdriver', { get: () => false });

            // 隱藏 Chrome automation 標記
            window.chrome = { runtime: {} };

            // 偽造 plugins
            Object.defineProperty(navigator, 'plugins', {
                get: () => [1, 2, 3, 4, 5]
            });

            // 偽造 languages
            Object.defineProperty(navigator, 'languages', {
                get: () => ['zh-TW', 'zh', 'en-US', 'en']
            });
        """)

        self._page = await self._context.new_page()
        self._page.set_default_timeout(BROWSER_TIMEOUT_MS)

    async def navigate(self, url: str, wait_until: str = "networkidle") -> dict[str, Any]:
        """導航到指定 URL"""
        page = await self._ensure_browser()
        try:
            response = await page.goto(url, wait_until=wait_until, timeout=BROWSER_TIMEOUT_MS)
            return {
                "success": True,
                "url": page.url,
                "title": await page.title(),
                "status": response.status if response else None,
            }
        except Exception as e:
            return {"success": False, "error": str(e), "url": url}

    async def screenshot(
        self,
        output_name: Optional[str] = None,
        full_page: bool = False,
        selector: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        截圖。支援全頁、元素選擇器截圖。
        回傳檔案路徑和 base64 編碼。
        """
        page = await self._ensure_browser()

        self._screenshot_count += 1
        if output_name is None:
            output_name = f"screenshot_{self._screenshot_count}_{int(time.time())}"

        path = os.path.join(SCREENSHOTS_DIR, f"{output_name}.png")

        try:
            if selector:
                element = await page.query_selector(selector)
                if element:
                    await element.screenshot(path=path)
                else:
                    return {"success": False, "error": f"找不到元素：{selector}"}
            else:
                await page.screenshot(path=path, full_page=full_page)

            # 讀取 base64 以便傳回
            with open(path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode()

            return {
                "success": True,
                "path": path,
                "size_kb": round(os.path.getsize(path) / 1024, 1),
                "base64_preview": b64[:200] + "...",  # 只回傳前 200 字元預覽
                "url": page.url,
                "title": await page.title(),
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def click(self, selector: str, wait_after_ms: int = 1000) -> dict[str, Any]:
        """點擊指定元素"""
        page = await self._ensure_browser()
        try:
            await page.click(selector, timeout=10000)
            await page.wait_for_timeout(wait_after_ms)
            return {
                "success": True,
                "selector": selector,
                "url": page.url,
                "title": await page.title(),
            }
        except Exception as e:
            return {"success": False, "error": str(e), "selector": selector}

    async def fill(self, selector: str, value: str) -> dict[str, Any]:
        """在輸入框填入文字"""
        page = await self._ensure_browser()
        try:
            await page.fill(selector, value, timeout=10000)
            return {"success": True, "selector": selector, "value": value}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def evaluate(self, script: str) -> dict[str, Any]:
        """在頁面中執行 JavaScript"""
        page = await self._ensure_browser()
        try:
            result = await page.evaluate(script)
            return {"success": True, "result": str(result)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def get_page_text(self) -> dict[str, Any]:
        """取得當前頁面的文字內容"""
        page = await self._ensure_browser()
        try:
            text = await page.inner_text("body")
            return {
                "success": True,
                "url": page.url,
                "title": await page.title(),
                "text": text[:5000],  # 限制回傳長度
                "text_length": len(text),
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def wait_for_selector(self, selector: str, timeout_ms: int = 10000) -> dict[str, Any]:
        """等待特定元素出現"""
        page = await self._ensure_browser()
        try:
            await page.wait_for_selector(selector, timeout=timeout_ms)
            return {"success": True, "selector": selector}
        except Exception as e:
            return {"success": False, "error": str(e), "selector": selector}

    async def scroll(self, direction: str = "down", amount: int = 500) -> dict[str, Any]:
        """頁面捲動"""
        page = await self._ensure_browser()
        try:
            delta = amount if direction == "down" else -amount
            await page.mouse.wheel(0, delta)
            await page.wait_for_timeout(500)
            return {"success": True, "direction": direction, "amount": amount}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def get_browser_status(self) -> dict[str, Any]:
        """取得瀏覽器狀態"""
        is_running = self._browser is not None and self._browser.is_connected()
        return {
            "browser_running": is_running,
            "current_url": self._page.url if is_running and self._page else None,
            "current_title": (await self._page.title()) if is_running and self._page else None,
            "screenshot_count": self._screenshot_count,
            "last_activity": self._last_activity,
            "headless": False,  # 🔴 永遠是 False
            "screenshots_dir": SCREENSHOTS_DIR,
        }

    async def close(self):
        """關閉瀏覽器"""
        if self._browser:
            await self._browser.close()
            self._browser = None
            self._context = None
            self._page = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None


# 全域單例
browser_manager = BrowserManager()
