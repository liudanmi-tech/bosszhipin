import time
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator
from pathlib import Path

from playwright.sync_api import BrowserContext, Page

from ..config import ROOT
from ..models import Job


class Platform(ABC):
    """平台适配器接口。每个招聘网站实现一个子类。

    注意：页面选择器会随网站改版失效，需要定期在真实页面上校准。
    """

    name: str = ""
    home_url: str = ""
    label: str = ""
    ready: bool = True  # 适配器是否已实现

    def __init__(self, ctx: BrowserContext, safety: dict):
        self.ctx = ctx
        self.safety = safety
        self.page: Page = self._pick_page(ctx)
        self.log: Callable[[str], None] = print
        self.should_stop: Callable[[], bool] = lambda: False
        self.on_snap: Callable[[bytes], None] | None = None  # 网页版「实时画面」

    def _pick_page(self, ctx: BrowserContext) -> Page:
        """复用已打开的本站标签页，否则新开一个。

        不用 ctx.pages[0]：通过调试端口连接新版 Chrome 时，列表里还有隐藏的
        chrome://newtab-footer/ 部件（不是真正的标签页，在它上面跳转不可见，关闭它会卡死）。
        """
        domain = self.home_url.split("/")[2]
        page = next((p for p in ctx.pages if domain in p.url), None) or ctx.new_page()
        for other in ctx.pages:  # 顺手关掉启动时的空白新标签页
            if other is not page and other.url.startswith(("chrome://new-tab-page", "chrome://newtab/", "about:blank")):
                try:
                    other.close()
                except Exception:  # noqa: BLE001
                    pass
        try:
            page.bring_to_front()
        except Exception:  # noqa: BLE001
            pass
        return page

    def ensure_login(self) -> None:
        """打开首页；未登录时等待用户在浏览器里手动扫码。"""
        self.page.goto(self.home_url)
        if not self.is_logged_in():
            input(f"[{self.name}] 请在打开的浏览器窗口中扫码登录，完成后按回车继续...")
        if not self.is_logged_in():
            raise RuntimeError(f"[{self.name}] 仍未检测到登录状态")

    def wait_for_login(self, timeout_sec: int = 180) -> bool:
        """轮询登录状态（网页版用，无需终端回车）。已在本站且已登录时不再刷新页面。"""
        domain = self.home_url.split("/")[2]
        if domain not in self.page.url or not self.is_logged_in():
            self.page.goto(self.home_url, wait_until="domcontentloaded")
        self.page.bring_to_front()
        self.log(f"  当前页面：{self.page.url}")
        for i in range(timeout_sec // 2):
            self.snap()
            if self.should_stop():
                return False
            if self.is_logged_in():
                return True
            if i and i % 15 == 0:
                self.log(f"  仍在等待登录… 当前页面：{self.page.url}")
            self.page.wait_for_timeout(2000)
        return False

    @abstractmethod
    def is_logged_in(self) -> bool: ...

    @abstractmethod
    def browse(self, keyword: str, city: str, limit: int = 15,
               skip: Callable[[str], bool] = lambda _id: False) -> Iterator[Job]:
        """像真人一样搜索并逐个点开岗位，每看完一个（含职位描述）就 yield 一个。

        skip(job_id) 为 True 的岗位（已看过）不点开。
        """

    @abstractmethod
    def apply(self, job: Job, greeting: str) -> bool:
        """投递 / 发起沟通，并发送打招呼话术。"""

    def unread_conversations(self) -> list[dict]:
        """返回有未读消息的会话：[{"job": Job, "history": [{"role", "text"}], "handle": ...}]"""
        return []

    def send_message(self, handle, text: str) -> None:
        raise NotImplementedError

    def snap(self) -> None:
        """截一张当前浏览器画面，推送给网页版显示。"""
        if self.on_snap is None:
            return
        try:
            self.on_snap(self.page.screenshot(type="jpeg", quality=55, timeout=5000))
        except Exception:  # noqa: BLE001
            pass

    def dump_debug(self, tag: str) -> Path:
        """保存当前页面截图和 HTML，方便排查选择器失效/风控拦截。"""
        base = ROOT / "data" / "debug" / f"{self.name}-{time.strftime('%m%d-%H%M%S')}-{tag}"
        base.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.page.screenshot(path=f"{base}.png", full_page=True)
            Path(f"{base}.html").write_text(self.page.content(), encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass
        return base.with_suffix(".png")
