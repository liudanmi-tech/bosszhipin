import random
import time

from playwright.sync_api import BrowserContext, Playwright

from .config import ROOT


def open_context(pw: Playwright, platform: str, headless: bool = False) -> BrowserContext:
    """每个平台一个持久化浏览器目录：首次手动扫码登录，之后复用登录态。"""
    profile_dir = ROOT / "browser_profiles" / platform
    profile_dir.mkdir(parents=True, exist_ok=True)
    return pw.chromium.launch_persistent_context(
        str(profile_dir),
        headless=headless,
        viewport={"width": 1366, "height": 860},
        locale="zh-CN",
    )


def human_pause(safety: dict) -> None:
    """操作之间的随机停顿，降低被风控识别的概率。"""
    time.sleep(random.uniform(safety.get("min_delay_sec", 4), safety.get("max_delay_sec", 12)))
