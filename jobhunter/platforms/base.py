from abc import ABC, abstractmethod

from playwright.sync_api import BrowserContext, Page

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
            if self.is_logged_in():
                return True
            self.page.wait_for_timeout(2000)
        return False

    @abstractmethod
    def is_logged_in(self) -> bool: ...

    @abstractmethod
    def search(self, keyword: str, city: str) -> list[Job]:
        """按关键词+城市搜索，返回职位列表（至少含 job_id/title/company/url）。"""

    @abstractmethod
    def fetch_detail(self, job: Job) -> Job:
        """补全职位描述等详情。"""

    @abstractmethod
    def apply(self, job: Job, greeting: str) -> bool:
        """投递 / 发起沟通，并发送打招呼话术。"""

    def unread_conversations(self) -> list[dict]:
        """返回有未读消息的会话：[{"job": Job, "history": [{"role", "text"}], "handle": ...}]"""
        return []

    def send_message(self, handle, text: str) -> None:
        raise NotImplementedError
