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

    def browse(self, keyword, city, limit=15, skip=lambda _id: False):
        raise NotImplementedError

    def apply(self, job: Job, greeting: str) -> bool:
        raise NotImplementedError
