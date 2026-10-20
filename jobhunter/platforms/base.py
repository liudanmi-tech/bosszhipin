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
        self.page: Page = ctx.pages[0] if ctx.pages else ctx.new_page()
        self.log: Callable[[str], None] = print
        self.on_snap: Callable[[bytes], None] | None = None  # 网页版「实时画面」

    def ensure_login(self) -> None:
        """打开首页；未登录时等待用户在浏览器里手动扫码。"""
        self.page.goto(self.home_url)
        if not self.is_logged_in():
            input(f"[{self.name}] 请在打开的浏览器窗口中扫码登录，完成后按回车继续...")
        if not self.is_logged_in():
            raise RuntimeError(f"[{self.name}] 仍未检测到登录状态")

    def wait_for_login(self, timeout_sec: int = 180) -> bool:
        """打开首页并轮询登录状态（网页版用，无需终端回车）。"""
        self.page.goto(self.home_url)
        for _ in range(timeout_sec // 2):
            self.snap()
            if self.is_logged_in():
                return True
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
