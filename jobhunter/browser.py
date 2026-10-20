import os
import random
import subprocess
import time

import httpx

from playwright.sync_api import BrowserContext, Locator, Page, Playwright

from .config import ROOT

# 去掉最明显的自动化特征（仅在退回 Playwright 自启动浏览器时使用）
STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en'] });
window.chrome = window.chrome || { runtime: {} };
"""

CHROME_PATHS = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    os.path.expanduser("~/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    "/usr/bin/google-chrome",
]
CDP_PORTS = {"boss": 9333, "liepin": 9334, "maimai": 9335}


def open_context(pw: Playwright, platform: str, headless: bool = False) -> BrowserContext:
    """打开某个平台专用的浏览器，登录态保存在 browser_profiles/ 下，之后复用。

    首选：像普通用户一样启动电脑上的 Chrome，再通过调试端口连上去操作。浏览器不是由
    Playwright 启动的，没有 --enable-automation / navigator.webdriver 等自动化标记，
    不容易触发招聘网站的风控（BOSS 的 _security_check 反复刷新）。
    找不到 Chrome 时退回 Playwright 自带的 Chromium。
    """
    chrome = os.environ.get("JOBHUNTER_CHROME") or next((p for p in CHROME_PATHS if os.path.exists(p)), None)
    if chrome:
        return _connect_real_chrome(pw, chrome, platform)

    profile_dir = ROOT / "browser_profiles" / platform
    profile_dir.mkdir(parents=True, exist_ok=True)
    ctx = pw.chromium.launch_persistent_context(
        str(profile_dir),
        headless=headless,
        no_viewport=True,
        locale="zh-CN",
        ignore_default_args=["--enable-automation"],
        args=["--disable-blink-features=AutomationControlled", "--start-maximized"],
    )
    ctx.add_init_script(STEALTH_JS)
    return ctx


def _connect_real_chrome(pw: Playwright, chrome: str, platform: str) -> BrowserContext:
    port = CDP_PORTS.get(platform, 9339)
    endpoint = f"http://127.0.0.1:{port}"
    if not _cdp_alive(endpoint):  # 上次打开的 Chrome 还开着就直接复用
        profile_dir = ROOT / "browser_profiles" / f"chrome_{platform}"
        profile_dir.mkdir(parents=True, exist_ok=True)
        args = [
            chrome,
            f"--remote-debugging-port={port}",
            f"--user-data-dir={profile_dir}",
            "--no-first-run",
            "--no-default-browser-check",
            "--start-maximized",
            *os.environ.get("JOBHUNTER_CHROME_ARGS", "").split(),
        ]
        subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        for _ in range(40):
            if _cdp_alive(endpoint):
                break
            time.sleep(0.5)
        else:
            raise RuntimeError("Chrome 启动失败：调试端口没有响应。请完全退出该 Chrome 窗口后重试")
    browser = pw.chromium.connect_over_cdp(endpoint)
    return browser.contexts[0] if browser.contexts else browser.new_context()


def _cdp_alive(endpoint: str) -> bool:
    try:
        return httpx.get(f"{endpoint}/json/version", timeout=1, trust_env=False).status_code == 200
    except httpx.HTTPError:
        return False


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
