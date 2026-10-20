import hashlib
import time
from collections.abc import Callable, Iterator

from ..browser import human_click, human_scroll, human_type, short_pause
from ..models import Job
from .base import Platform

# BOSS直聘城市编码
CITY_CODES = {
    "全国": "100010000", "北京": "101010100", "上海": "101020100", "广州": "101280100",
    "深圳": "101280600", "杭州": "101210100", "成都": "101270100", "南京": "101190100",
    "武汉": "101200100", "西安": "101110100", "苏州": "101190400", "天津": "101030100",
    "重庆": "101040100", "长沙": "101250100", "厦门": "101230200", "合肥": "101220100",
}

# 选择器同时兼容新版（/web/geek/jobs，左列表右详情）和旧版页面，改版后在这里校准
CARD = ".job-card-box, .job-card-wrapper"
SEARCH_INPUT = ".search-input-box input, input.ipt-search, .c-search-input input, input[placeholder*='搜索']"
DETAIL_DESC = ".job-detail-box .desc, .job-detail-body .desc, p.desc, .job-sec-text"
DETAIL_BOX = ".job-detail-box, .job-detail-container, .job-detail"
CHAT_BTN = ".op-btn-chat, .btn-startchat, a:has-text('立即沟通'), a:has-text('继续沟通')"
CHAT_INPUT = "#chat-input, .chat-input[contenteditable], div[contenteditable='true']"


class Boss(Platform):
    """BOSS直聘 (zhipin.com)：模拟真人在网页上搜索、逐个点开岗位、沟通。"""

    name = "boss"
    label = "BOSS直聘"
    home_url = "https://www.zhipin.com/web/geek/jobs"

    def is_logged_in(self) -> bool:
        if self._blocked():
            return False
        return self.page.locator(".nav-figure, .user-nav .nav-figure, a[ka='header-username']").count() > 0

    # ---------- 搜索：输入关键词 → 逐个点开岗位卡片 ----------
    def browse(self, keyword: str, city: str, limit: int = 15,
               skip: Callable[[str], bool] = lambda _id: False) -> Iterator[Job]:
        code = CITY_CODES.get(city, CITY_CODES["全国"])
        if f"city={code}" not in self.page.url or "/web/geek/jobs" not in self.page.url:
            self.page.goto(f"https://www.zhipin.com/web/geek/jobs?city={code}")
            self._wait_for(SEARCH_INPUT, "搜索框")
            short_pause(1.5, 3)

        human_type(self.page, self.page.locator(SEARCH_INPUT).first, keyword)
        short_pause(0.4, 1)
        self.page.keyboard.press("Enter")
        short_pause(2, 4)
        self._wait_for(CARD, "职位列表")

        seen, scrolls = 0, 0
        idx = 0
        while seen < limit:
            cards = self.page.locator(CARD)
            if idx >= cards.count():
                if scrolls >= 5:
                    break
                human_scroll(self.page, 2)  # 滚动加载更多
                scrolls += 1
                continue
            card = cards.nth(idx)
            idx += 1
            job = self._card_to_job(card)
            if skip(job.job_id):
                continue
            seen += 1
            self.log(f"  👀 查看第 {seen} 个：{job.title} @ {job.company}")
            self._read_detail(card, job)
            yield job

    def _card_to_job(self, card) -> Job:
        href = card.locator("a[href*='/job_detail/']").first
        href = href.get_attribute("href") if href.count() else ""
        title = _text(card, ".job-name, .job-title")
        company = _text(card, ".boss-name, .company-name, .company-info h3")
        if href:
            job_id = href.split("/job_detail/")[-1].split(".html")[0]
        else:
            job_id = hashlib.md5(f"{title}|{company}".encode()).hexdigest()[:16]
        return Job(
            platform=self.name, job_id=job_id, title=title, company=company,
            url=(href if href.startswith("http") else f"https://www.zhipin.com{href}") if href else "",
            salary=_text(card, ".job-salary, .salary"),
            city=_text(card, ".company-location, .job-area"),
            tags=[t.inner_text().strip() for t in card.locator(".tag-list li").all()],
        )

    def _read_detail(self, card, job: Job) -> None:
        """点击卡片：新版在右侧显示详情，旧版会打开新标签页。读完像人一样停留几秒。"""
        pages_before = len(self.ctx.pages)
        title = card.locator(".job-name, .job-title").first
        human_click(self.page, title if title.count() else card)
        short_pause(1.5, 3)

        if len(self.ctx.pages) > pages_before:  # 旧版：新标签页
            tab = self.ctx.pages[-1]
            tab.wait_for_load_state()
            job.description = _text(tab, DETAIL_DESC) or _text(tab, DETAIL_BOX)
            job.url = job.url or tab.url
            short_pause(2, 5)
            tab.close()
            return

        for _ in range(8):  # 新版：等右侧详情刷新
            job.description = _text(self.page, DETAIL_DESC) or _text(self.page, DETAIL_BOX)
            if job.description:
                break
            time.sleep(1)
        human_scroll(self.page, 1)  # 假装往下看看
        short_pause(1.5, 4)

    # ---------- 投递：点「立即沟通」并发送打招呼 ----------
    def apply(self, job: Job, greeting: str) -> bool:
        if not job.url:
            raise RuntimeError("缺少岗位链接")
        self.page.goto(job.url)
        self._wait_for(CHAT_BTN, "沟通按钮")
        short_pause(2, 4)  # 先看看岗位
        btn = self.page.locator(CHAT_BTN).first
        if "继续沟通" in btn.inner_text():
            raise RuntimeError("之前已经沟通过这个岗位")
        human_click(self.page, btn)
        short_pause(1.5, 3)

        # 点击后通常弹出「已向BOSS发送消息」，选「继续沟通」进入聊天页发送自定义话术
        go_chat = self.page.locator(".dialog-container a:has-text('继续沟通'), .greet-boss-dialog .sure-btn, "
                                    "a:has-text('继续沟通')").first
        if go_chat.count():
            human_click(self.page, go_chat)
            self.page.wait_for_load_state()
            short_pause(2, 4)
        if greeting:
            box = self.page.locator(CHAT_INPUT).first
            box.wait_for(timeout=15000)
            human_type(self.page, box, greeting)
            short_pause(0.8, 2)
            self.page.keyboard.press("Enter")
            short_pause(1, 2)
        return True

    # ---------- 风控处理 ----------
    def _blocked(self) -> bool:
        url = self.page.url
        if "security" in url or "verify" in url or "captcha" in url:
            return True
        return self.page.locator(".geetest_panel, .geetest_holder, .verify-wrap, #captcha").count() > 0

    def _wait_for(self, selector: str, what: str, timeout: int = 30) -> None:
        """等元素出现；遇到安全验证时延长到 3 分钟，并提示用户在浏览器里手动完成验证。"""
        deadline, warned = time.time() + timeout, False
        while time.time() < deadline:
            if self.page.locator(selector).count():
                return
            if self._blocked() and not warned:
                warned = True
                deadline = time.time() + 180
                self.log("  ⚠ BOSS 正在做安全验证。如果浏览器里出现验证码/滑块，请手动完成（3 分钟内）")
            time.sleep(1)
        path = self.dump_debug(what)
        raise RuntimeError(f"等待「{what}」超时，当前页面：{self.page.url}；截图已保存到 {path}")


def _text(scope, selector: str) -> str:
    loc = scope.locator(selector).first
    try:
        return loc.inner_text(timeout=2000).strip() if loc.count() else ""
    except Exception:  # noqa: BLE001
        return ""
