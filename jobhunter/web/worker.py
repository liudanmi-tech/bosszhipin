import queue
import threading
from concurrent.futures import Future

from playwright.sync_api import sync_playwright

from ..browser import open_context
from ..platforms import PLATFORMS
from ..platforms.base import Platform


class BrowserWorker:
    """Playwright 不是线程安全的：所有浏览器操作都排队到这一个线程里执行。"""

    def __init__(self):
        self._queue: queue.Queue = queue.Queue()
        self._sites: dict[str, Platform] = {}
        self._pw = None
        threading.Thread(target=self._run, daemon=True, name="browser").start()

    def submit(self, fn, *args) -> Future:
        fut: Future = Future()
        self._queue.put((fn, args, fut))
        return fut

    def site(self, name: str, safety: dict) -> Platform:
        """获取平台实例（只能在 worker 线程内调用）。浏览器窗口被手动关掉时自动重开。"""
        site = self._sites.get(name)
        if site is not None:
            try:
                if site.page.is_closed():
                    site.page = site._pick_page(site.ctx)
            except Exception:
                site = None
        if site is None:
            ctx = open_context(self._pw, name)
            site = PLATFORMS[name](ctx, safety)
            self._sites[name] = site
        site.safety = safety
        return site

    def _run(self):
        with sync_playwright() as pw:
            self._pw = pw
            while True:
                fn, args, fut = self._queue.get()
                try:
                    fut.set_result(fn(*args))
                except BaseException as e:  # noqa: BLE001 —— 错误交给调用方
                    fut.set_exception(e)
