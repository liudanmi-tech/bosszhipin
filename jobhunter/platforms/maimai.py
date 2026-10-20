from ..models import Job
from .base import Platform


class Maimai(Platform):
    """脉脉 (maimai.cn)。待实现：职位页 + 猎头私信。"""

    name = "maimai"
    label = "脉脉"
    ready = False
    home_url = "https://maimai.cn/"

    def is_logged_in(self) -> bool:
        raise NotImplementedError("TODO: 校准脉脉登录态选择器")

    def browse(self, keyword, city, limit=15, skip=lambda _id: False):
        raise NotImplementedError

    def apply(self, job: Job, greeting: str) -> bool:
        raise NotImplementedError
