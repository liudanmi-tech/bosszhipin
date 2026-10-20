import random
import time

from playwright.sync_api import BrowserContext, Locator, Page, Playwright

from .config import ROOT

# 去掉最明显的自动化特征（navigator.webdriver 等），让页面看到的是普通浏览器
STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en'] });
window.chrome = window.chrome || { runtime: {} };
"""


def open_context(pw: Playwright, platform: str, headless: bool = False) -> BrowserContext:
    """每个平台一个持久化浏览器目录：首次手动扫码登录，之后复用登录态。

    优先使用电脑上安装的正版 Chrome，没有时退回 Playwright 自带的 Chromium。
    注意：不要改成「带 --remote-debugging-port 启动 Chrome 再 connect_over_cdp」——
    BOSS 的反调试脚本能识别调试端口，页面会被立刻跳转到 about:blank。
    """
    profile_dir = ROOT / "browser_profiles" / platform
    profile_dir.mkdir(parents=True, exist_ok=True)
    options = dict(
        headless=headless,
        no_viewport=True,
        locale="zh-CN",
        ignore_default_args=["--enable-automation"],
        args=["--disable-blink-features=AutomationControlled", "--start-maximized"],
    )
    try:
        ctx = pw.chromium.launch_persistent_context(str(profile_dir), channel="chrome", **options)
    except Exception:  # noqa: BLE001 —— 未安装 Chrome
        ctx = pw.chromium.launch_persistent_context(str(profile_dir), **options)
    ctx.add_init_script(STEALTH_JS)
    return ctx


def human_pause(safety: dict) -> None:
    """两个岗位之间的随机停顿，降低被风控识别的概率。"""
    time.sleep(random.uniform(safety.get("min_delay_sec", 4), safety.get("max_delay_sec", 12)))


def short_pause(lo: float = 0.6, hi: float = 1.8) -> None:
    """页面内小动作之间的停顿（看一眼、移鼠标）。"""
    time.sleep(random.uniform(lo, hi))


def human_click(page: Page, target: Locator) -> None:
    """像人一样点击：滚动到可见 → 鼠标分几步移过去 → 稍停 → 点击（带随机偏移）。"""
    target.scroll_into_view_if_needed()
    short_pause(0.3, 0.9)
    box = target.bounding_box()
    if not box:
        target.click()
        return
    x = box["x"] + box["width"] * random.uniform(0.3, 0.7)
    y = box["y"] + box["height"] * random.uniform(0.3, 0.7)
    page.mouse.move(x, y, steps=random.randint(8, 20))
    short_pause(0.2, 0.6)
    page.mouse.click(x, y, delay=random.randint(40, 140))


def human_type(page: Page, target: Locator, text: str) -> None:
    """点中输入框，清空后逐字输入，每个字之间有随机间隔。"""
    human_click(page, target)
    page.keyboard.press("Meta+A" if _is_mac(page) else "Control+A")
    page.keyboard.press("Backspace")
    for ch in text:
        page.keyboard.type(ch)
        time.sleep(random.uniform(0.08, 0.25))


def human_scroll(page: Page, times: int = 1) -> None:
    for _ in range(times):
        page.mouse.wheel(0, random.randint(300, 700))
        short_pause(0.5, 1.2)


def _is_mac(page: Page) -> bool:
    try:
        return "Mac" in page.evaluate("navigator.platform")
    except Exception:  # noqa: BLE001
        return False
