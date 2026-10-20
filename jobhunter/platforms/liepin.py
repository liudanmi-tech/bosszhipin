from ..models import Job
from .base import Platform


class Liepin(Platform):
    """猎聘 (liepin.com)。待实现：先在真实页面上确定选择器。"""

    name = "liepin"
    label = "猎聘"
    ready = False
    home_url = "https://www.liepin.com/"

    def is_logged_in(self) -> bool:
        raise NotImplementedError("TODO: 校准猎聘登录态选择器")

    def search(self, keyword: str, city: str) -> list[Job]:
        # 搜索页：https://www.liepin.com/zhaopin/?key=<keyword>
        raise NotImplementedError

    def fetch_detail(self, job: Job) -> Job:
        raise NotImplementedError

    def apply(self, job: Job, greeting: str) -> bool:
        raise NotImplementedError
