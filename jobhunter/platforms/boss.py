from urllib.parse import quote

from ..browser import human_pause
from ..models import Job
from .base import Platform

# BOSS直聘城市编码
CITY_CODES = {
    "北京": "101010100", "上海": "101020100", "广州": "101280100",
    "深圳": "101280600", "杭州": "101210100", "成都": "101270100",
}


class Boss(Platform):
    """BOSS直聘 (zhipin.com)。选择器基于当前网页版，改版后需重新校准。"""

    name = "boss"
    label = "BOSS直聘"
    home_url = "https://www.zhipin.com/web/geek/job"

    def is_logged_in(self) -> bool:
        return self.page.locator(".user-nav .nav-figure, .nav-figure").count() > 0

    def search(self, keyword: str, city: str) -> list[Job]:
        code = CITY_CODES.get(city, "100010000")  # 100010000 = 全国
        self.page.goto(f"https://www.zhipin.com/web/geek/job?query={quote(keyword)}&city={code}")
        self.page.wait_for_selector(".job-card-wrapper, .job-card-box", timeout=20000)
        human_pause(self.safety)

        jobs = []
        for card in self.page.locator(".job-card-wrapper, .job-card-box").all():
            link = card.locator("a[href*='/job_detail/']").first
            href = link.get_attribute("href") or ""
            if not href:
                continue
            job_id = href.split("/job_detail/")[-1].split(".html")[0]
            jobs.append(Job(
                platform=self.name,
                job_id=job_id,
                title=_text(card, ".job-name"),
                company=_text(card, ".company-name, .boss-name"),
                salary=_text(card, ".salary, .job-salary"),
                city=_text(card, ".job-area, .company-location"),
                url=href if href.startswith("http") else f"https://www.zhipin.com{href}",
                tags=[t.inner_text() for t in card.locator(".tag-list li").all()],
            ))
        return jobs

    def fetch_detail(self, job: Job) -> Job:
        self.page.goto(job.url)
        self.page.wait_for_selector(".job-sec-text", timeout=20000)
        job.description = _text(self.page, ".job-sec-text")
        job.recruiter = _text(self.page, ".job-boss-info .name")
        human_pause(self.safety)
        return job

    def apply(self, job: Job, greeting: str) -> bool:
        """点击「立即沟通」，在聊天框发送打招呼话术。"""
        if self.page.url != job.url:
            self.page.goto(job.url)
        btn = self.page.locator(".btn-startchat, a:has-text('立即沟通')").first
        if btn.count() == 0 or "继续沟通" in (btn.inner_text() or ""):
            return False
        btn.click()
        human_pause(self.safety)

        # 点击后可能弹出「留在此页/继续沟通」对话框，或直接跳转聊天页
        stay = self.page.locator(".greet-boss-dialog .cancel-btn, a:has-text('继续沟通')").first
        if stay.count():
            stay.click()
            self.page.wait_for_load_state()
        box = self.page.locator("#chat-input, .chat-input[contenteditable]").first
        if box.count() and greeting:
            box.click()
            box.type(greeting, delay=60)
            human_pause(self.safety)
            self.page.keyboard.press("Enter")
        return True


def _text(scope, selector: str) -> str:
    loc = scope.locator(selector).first
    return loc.inner_text().strip() if loc.count() else ""
