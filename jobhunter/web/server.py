import json
import re
import subprocess
import threading
import time
from collections import deque
from dataclasses import asdict, fields
from pathlib import Path

from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, Response

from ..browser import human_pause
from ..config import ROOT, read_resume
from ..llm import LLM
from ..matcher import Matcher
from ..models import Job, MatchResult
from ..platforms import PLATFORMS
from ..storage import Storage
from .providers import KEY_FIELDS, PROVIDERS, default_settings, public_info
from .resume_docx import markdown_to_docx
from .sites import EXT_SITES, build_tasks
from .worker import BrowserWorker

DATA = ROOT / "data"
SETTINGS_FILE = DATA / "web_settings.json"
JOBS_FILE = DATA / "web_jobs.json"
STATIC = Path(__file__).parent / "static"

DEFAULT_SETTINGS = {
    "resume_name": "",
    "resume_text": "",
    "platforms": ["boss"],
    "keywords": [],
    "cities": [],
    "salary_min_k": 0,
    "must_have": [],
    "avoid": [],
    "notes": "",
    "threshold": 75,
    "max_per_search": 15,
    "daily_apply_limit": 30,
    "min_delay_sec": 4,
    "max_delay_sec": 12,
    "auto_tailor": True,
    "auto_apply": False,
    "browse_speed": "normal",  # 插件查看岗位的节奏 fast / normal / slow；投递间隔仍用 min/max_delay_sec
    **default_settings(),  # provider + 每家服务商的 Key / 模型
}
JOB_FIELDS = {f.name for f in fields(Job)}


def _load(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _save(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def _safe(key: str) -> str:
    return re.sub(r"[^\w.-]", "_", key)


class State:
    def __init__(self):
        self.settings = {**DEFAULT_SETTINGS, **_load(SETTINGS_FILE, {})}
        self.jobs: dict[str, dict] = _load(JOBS_FILE, {})
        self.logs: deque = deque(maxlen=300)
        self.task: str | None = None
        self.stop = False
        self.logged_in: dict[str, bool] = {}
        self.screen: bytes = b""
        self.screen_at = 0.0
        self.version = 0
        self.lock = threading.Lock()
        self.jobs_lock = threading.RLock()  # 保护 self.jobs 的增删和存盘
        self.worker = BrowserWorker()
        self._storage: Storage | None = None
        # 各招聘网站由 Chrome 插件在用户自己的浏览器里操作（见 extension/），以下按网站记录
        # 每个网站独立运行，互不阻塞
        self.ext_requests: dict[str, dict] = {}  # 等插件领取的任务
        self.ext_running: dict[str, str] = {}    # 正在跑的网站 → 任务名（筛选/投递）
        self.ext_active_id: dict[str, str] = {}  # 网站当前任务的 id，插件里残留的旧任务据此叫停
        self.ext_stop: set[str] = set()          # 被要求停止的网站
        self.ext_run_seen: dict[str, float] = {}  # 领走任务的那个标签页最近一次汇报的时间
        self.ext_seen: dict[str, float] = {}     # 插件最近一次来领任务的时间
        self.ext_queued: dict[str, int] = {}     # 已排进投递队列、尚未回报的数量（计入每日上限）

    # ---------- 基础 ----------
    def log(self, msg: str) -> None:
        self.logs.append(f"{time.strftime('%H:%M:%S')}  {msg}")

    def touch_jobs(self) -> None:
        # 插件会并发提交打分，写文件时序列化，避免边改边存
        with self.jobs_lock:
            self.version += 1
            _save(JOBS_FILE, self.jobs)

    def safety(self) -> dict:
        s = self.settings
        return {"min_delay_sec": s["min_delay_sec"], "max_delay_sec": s["max_delay_sec"]}

    def matcher(self) -> Matcher:
        s = self.settings
        if not s["resume_text"].strip():
            raise HTTPException(400, "请先上传简历")
        prefs = {k: s[k] for k in ("keywords", "cities", "salary_min_k", "must_have", "avoid", "notes")}
        return Matcher(self.llm(), s["resume_text"], prefs)

    def llm(self) -> LLM:
        """按网页上选的 AI 服务商创建客户端；没填 Key 时报 400。"""
        prov = PROVIDERS.get(self.settings["provider"], PROVIDERS["openrouter"])
        key = self.settings[prov["key_field"]]
        if not key:
            raise HTTPException(400, f"请先填写 {prov['label']} 的 API Key")
        return LLM({"base_url": prov["base_url"], "model": self.settings[prov["model_field"]], "api_key": key})

    def storage(self) -> Storage:
        with self.lock:
            if self._storage is None:
                self._storage = Storage()
        return self._storage

    def ext_connected(self, site: str) -> bool:
        return time.time() - self.ext_seen.get(site, 0) < 8

    def ext_task(self) -> str | None:
        """给页面顶部显示：哪些网站在做什么。"""
        if not self.ext_running:
            return None
        return "、".join(f"{EXT_SITES[s]['label']}{n}" for s, n in self.ext_running.items())

    def start_ext(self, name: str, requests: dict[str, dict]) -> None:
        """把任务交给各网站的插件标签页；插件轮询 /api/ext/poll 领取。

        只派给已连接（页面开着）且空闲的网站：没打开的网站会让任务一直等下去；
        各网站互不阻塞，一个网站在跑不影响另一个网站开始。
        """
        self.reap_ext()
        skipped = []
        for site in list(requests):
            label = EXT_SITES[site]["label"]
            if site in self.ext_running:
                skipped.append(f"{label}还在{self.ext_running[site]}（可先点停止）")
                del requests[site]
            elif not self.ext_connected(site):
                skipped.append(f"{label}未打开，请用装了插件的 Chrome 打开 {EXT_SITES[site]['url']} 并登录")
                del requests[site]
        for why in skipped:
            self.log(f"⚠ 跳过：{why}")
        if not requests:
            raise HTTPException(400, "没有可以开始的网站：" + "；".join(skipped))
        base = {"min_delay": self.settings["min_delay_sec"], "max_delay": self.settings["max_delay_sec"]}
        with self.lock:
            for site, req in requests.items():
                rid = str(time.time_ns())
                self.ext_requests[site] = {**base, **req, "id": rid}
                self.ext_active_id[site] = rid
                self.ext_running[site] = name
                self.ext_stop.discard(site)
                self.ext_queued[site] = 0
        for site in requests:
            self.log(f"[{EXT_SITES[site]['label']}] 已交给插件开始{name}")

    def reap_ext(self) -> None:
        """任务没人做时结束它，免得一直挂着挡住新任务：
        还没被领走 → 该网站 30 秒没有任何页面开着；
        已被领走 → 领走它的那个标签页 5 分钟没汇报（被关掉或跳走了）。放宽到 5 分钟是因为
        Chrome 会把后台标签页的定时器限流到约每分钟一次，切到别的标签页时汇报会变慢。
        """
        now = time.time()
        for site in list(self.ext_running):
            if site in self.ext_requests:
                gone = now - self.ext_seen.get(site, 0) > 30
            else:
                gone = now - self.ext_run_seen.get(site, 0) > 300
            if gone:
                self.log(f"⚠ [{EXT_SITES[site]['label']}] 运行任务的页面已关闭或长时间没响应，结束该任务")
                self.finish_ext(site)

    def ext_alive(self, site: str, run: str | None) -> None:
        """插件带着当前任务 id 来请求（轮询、提交岗位、写日志）都算「还活着」。"""
        if run and self.ext_active_id.get(site) == run:
            self.ext_run_seen[site] = time.time()

    def ext_stop_reason(self, site: str, run: str | None) -> str:
        """插件里正在跑的任务是否该停，以及原因（空字符串 = 不用停）。"""
        if site in self.ext_stop:
            return "已按停止键停止"
        if run and self.ext_active_id.get(site) != run:
            return "服务端已不再记录这个任务（页面长时间没响应，或程序重启过），请重新开始"
        return ""

    def finish_ext(self, site: str) -> None:
        self.ext_requests.pop(site, None)
        self.ext_active_id.pop(site, None)
        self.ext_stop.discard(site)
        name = self.ext_running.pop(site, None)
        if name:
            self.log(f"— [{EXT_SITES[site]['label']}] {name}结束 —")

    def start_task(self, name: str, fn, *args) -> None:
        with self.lock:
            if self.task:
                raise HTTPException(409, f"正在执行「{self.task}」，请等待完成或点击停止")
            self.task, self.stop = name, False

        def wrapped():
            try:
                fn(*args)
            except Exception as e:  # noqa: BLE001
                self.log(f"❌ {name}出错：{e}")
            finally:
                self.task = None
                self.log(f"— {name}结束 —")

        self.worker.submit(wrapped)

    # ---------- 浏览器任务（在 worker 线程执行） ----------
    def _site(self, platform: str):
        site = self.worker.site(platform, self.safety())
        site.log = self.log
        site.on_snap = self._set_screen
        site.should_stop = lambda: self.stop
        return site

    def _set_screen(self, jpeg: bytes) -> None:
        self.screen, self.screen_at = jpeg, time.time()

    def _login(self, platform: str) -> bool:
        site = self._site(platform)
        self.log(f"[{site.label}] 打开登录页，未登录请在弹出的浏览器窗口中扫码（3 分钟内）")
        ok = site.wait_for_login(180)
        self.logged_in[platform] = ok
        self.log(f"[{site.label}] {'✅ 已登录' if ok else '⚠ 登录超时'}")
        return ok

    def run_login(self, platform: str) -> None:
        self._login(platform)

    def run_search(self) -> None:
        s = self.settings
        matcher = self.matcher()
        store = self.storage()
        cities = s["cities"] or ["全国"]
        for platform in s["platforms"]:
            cls = PLATFORMS[platform]
            if platform in EXT_SITES:  # 这些网站走 Chrome 插件，不用自动化浏览器
                continue
            if not cls.ready:
                self.log(f"[{cls.label}] 适配器开发中，跳过")
                continue
            if not self._login(platform):
                continue
            site = self._site(platform)
            for keyword in s["keywords"]:
                for city in cities:
                    if self.stop:
                        return
                    self.log(f"[{site.label}] 搜索「{keyword}」@ {city}")
                    to_apply = []
                    try:
                        for job in site.browse(keyword, city, s["max_per_search"],
                                               skip=lambda jid: f"{platform}:{jid}" in self.jobs):
                            if self.stop:
                                return
                            try:
                                entry = self._screen_one(matcher, store, f"{platform}:{job.job_id}", job)
                            except Exception as e:  # noqa: BLE001
                                self.log(f"  跳过 {job.title}：{e}")
                                continue
                            if entry["status"] == "matched" and s["auto_apply"]:
                                to_apply.append(entry)
                    except Exception as e:  # noqa: BLE001
                        self.log(f"[{site.label}] 搜索失败：{e}")
                    # 投递会离开列表页，所以这一页看完后再逐个投递
                    for entry in to_apply:
                        if self.stop:
                            return
                        self._apply_one(site, store, entry)

    def _screen_one(self, matcher, store, key, job: Job, tailor_now: bool = True) -> dict:
        s = self.settings
        match = matcher.score(job)
        matched = match.score >= s["threshold"]
        entry = {
            **asdict(job), "key": key, "score": match.score, "reasons": match.reasons,
            "greeting": match.greeting, "status": "matched" if matched else "low",
            "tailored": False, "error": "",
        }
        with self.jobs_lock:
            self.jobs[key] = entry
        store.record(job, match, "seen" if matched else "skipped")
        self.touch_jobs()
        self.log(f"  {match.score:>3} 分  {job.title} @ {job.company}")

        if matched and s["auto_tailor"] and tailor_now:
            self._tailor(entry, matcher)
        return entry

    def run_apply(self, keys: list[str]) -> None:
        store = self.storage()
        for key in keys:
            if self.stop:
                return
            entry = self.jobs.get(key)
            if not entry or entry["status"] == "applied":
                continue
            site = self._site(entry["platform"])
            self._apply_one(site, store, entry)

    def _apply_one(self, site, store, entry: dict) -> None:
        limit = self.settings["daily_apply_limit"]
        if store.applied_today(site.name) >= limit:
            self.log(f"[{site.label}] 已达今日投递上限 {limit}，跳过 {entry['title']}")
            return
        job = Job(**{k: v for k, v in entry.items() if k in JOB_FIELDS})
        try:
            ok = site.apply(job, entry["greeting"])
        except Exception as e:  # noqa: BLE001
            ok, entry["error"] = False, str(e)
        entry["status"] = "applied" if ok else "failed"
        if ok:
            store.record(job, MatchResult(entry["score"], entry["reasons"], entry["greeting"]), "applied")
        self.touch_jobs()
        self.log(f"  {'✅ 已投递' if ok else '⚠ 投递失败'}：{entry['title']} @ {entry['company']}")
        human_pause(self.safety())

    # ---------- 定制简历（不碰浏览器，可在任意线程） ----------
    def _tailor_safe(self, entry: dict) -> None:
        try:
            self._tailor(entry)
        except Exception as e:  # noqa: BLE001
            self.log(f"  定制简历生成失败：{entry['title']}：{e}")

    def _tailor(self, entry: dict, matcher: Matcher | None = None) -> None:
        matcher = matcher or self.matcher()
        job = Job(**{k: v for k, v in entry.items() if k in JOB_FIELDS})
        text = matcher.tailor_resume(job)
        markdown_to_docx(text, DATA / "tailored" / f"{_safe(entry['key'])}.docx")
        with self.jobs_lock:
            entry["tailored_text"] = text
            entry["tailored"] = True
        self.touch_jobs()
        self.log(f"  📝 已生成定制简历：{entry['title']} @ {entry['company']}")


def _version() -> str:
    try:
        return subprocess.run(["git", "log", "-1", "--format=%h %cd", "--date=format:%m-%d %H:%M"],
                              cwd=ROOT, capture_output=True, text=True, timeout=5).stdout.strip()
    except Exception:  # noqa: BLE001
        return "未知"


state = State()
state.log(f"程序已启动，版本 {_version()}")
app = FastAPI(title="AI 求职助手")


@app.get("/", response_class=HTMLResponse)
def index():
    return (STATIC / "index.html").read_text(encoding="utf-8")


@app.get("/api/settings")
def get_settings():
    s = dict(state.settings)
    s["has_key"] = {f: bool(s.pop(f)) for f in KEY_FIELDS}
    s["providers"] = public_info()
    s["platform_options"] = [{"name": k, "label": v["label"], "url": v["url"], "beta": v.get("beta", False)}
                             for k, v in EXT_SITES.items()]
    return s


@app.post("/api/settings")
def save_settings(body: dict):
    for k, v in body.items():
        if k not in DEFAULT_SETTINGS or (k in KEY_FIELDS and not v):  # Key 留空 = 不修改
            continue
        state.settings[k] = v
    _save(SETTINGS_FILE, state.settings)
    return {"ok": True}


@app.post("/api/test-llm")
def test_llm():
    """用当前保存的 Key 和模型发一句话，把成功或失败原因直接告诉用户。"""
    try:
        llm = state.llm()
    except HTTPException as e:
        return {"ok": False, "message": e.detail}
    started = time.time()
    try:
        reply = llm.chat("你是测试助手。", "回复「连接成功」四个字。")
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": str(e)}
    label = PROVIDERS[state.settings["provider"]]["label"]
    return {"ok": True, "message": f"{label} · {llm.model} 连接成功（{time.time() - started:.1f} 秒）：{reply.strip()[:30]}"}


@app.post("/api/resume")
async def upload_resume(file: UploadFile):
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in (".docx", ".pdf", ".txt", ".md"):
        raise HTTPException(400, "支持 .docx / .pdf / .txt；旧版 .doc 请先另存为 .docx")
    path = DATA / "resume" / f"original{suffix}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(await file.read())
    try:
        text = read_resume(path)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"简历解析失败：{e}") from e
    if not text.strip():
        raise HTTPException(400, "没有从文件中读到文字（可能是扫描版图片），请换一个文件")
    state.settings.update(resume_name=file.filename, resume_text=text)
    _save(SETTINGS_FILE, state.settings)
    return {"name": file.filename, "text": text}


@app.post("/api/login/{platform}")
def login(platform: str):
    if platform not in PLATFORMS or not PLATFORMS[platform].ready:
        raise HTTPException(400, "该渠道暂未支持")
    state.start_task("登录", state.run_login, platform)
    return {"ok": True}


@app.post("/api/search")
def search(body: dict | None = None):
    """开始筛选。插件面板上的「开始」会带 site，只启动那个网站。"""
    s = state.settings
    if not s["resume_text"].strip():
        raise HTTPException(400, "请先上传简历")
    if not s["keywords"]:
        raise HTTPException(400, "请至少填写一个职位关键词")
    state.llm()  # 没填 Key 时直接提示
    only = (body or {}).get("site")
    sites = [only] if only in EXT_SITES else [p for p in s["platforms"] if p in EXT_SITES]
    if not sites:
        raise HTTPException(400, "请至少勾选一个渠道")
    requests = {}
    for site in sites:
        tasks, unknown = build_tasks(site, s["keywords"], s["cities"])
        if unknown:
            state.log(f"⚠ [{EXT_SITES[site]['label']}] 这些城市暂不支持，按全国搜索：{'、'.join(unknown)}")
        with state.jobs_lock:
            known = [j["job_id"] for j in state.jobs.values() if j["platform"] == site]
        requests[site] = {"kind": "search", "tasks": tasks, "limit": s["max_per_search"], "known": known,
                          "speed": s["browse_speed"]}
    state.start_ext("筛选", requests)
    return {"ok": True}


@app.post("/api/apply")
def apply(body: dict):
    entries = [state.jobs[k] for k in body.get("keys", []) if k in state.jobs]
    entries = [e for e in entries if e["status"] != "applied" and e["url"] and e["platform"] in EXT_SITES]
    if not entries:
        raise HTTPException(400, "请先勾选要投递的岗位")
    requests = {}
    for site in dict.fromkeys(e["platform"] for e in entries):
        mine = [e for e in entries if e["platform"] == site]
        room = state.settings["daily_apply_limit"] - state.storage().applied_today(site)
        if room <= 0:
            state.log(f"⚠ [{EXT_SITES[site]['label']}] 已达今日投递上限，跳过")
            continue
        if len(mine) > room:
            state.log(f"⚠ [{EXT_SITES[site]['label']}] 今日只剩 {room} 个投递名额，只投前 {room} 个")
        requests[site] = {"kind": "apply", "apply_queue": [
            {"key": e["key"], "url": e["url"], "greeting": e["greeting"], "title": e["title"]} for e in mine[:room]]}
    if not requests:
        raise HTTPException(400, "已达今日投递上限")
    state.start_ext("投递", requests)
    return {"ok": True}


@app.post("/api/stop")
def stop(body: dict | None = None):
    """停止全部网站；插件面板上的「停止」会带 site，只停那个网站。"""
    only = (body or {}).get("site")
    sites = [only] if only in EXT_SITES else list(state.ext_running)
    for site in sites:
        if site in state.ext_requests:  # 插件还没领走的任务直接取消
            state.finish_ext(site)
        elif site in state.ext_running:
            state.ext_stop.add(site)
    if not only:
        state.stop = True  # 命令行 / 自动化浏览器任务
    state.log("收到停止指令，当前岗位处理完后停止")
    return {"ok": True}


@app.get("/api/status")
def status(since: int = -1):
    state.reap_ext()
    return {
        "task": state.task or state.ext_task(),
        "logs": list(state.logs),
        "logged_in": state.logged_in,
        "version": state.version,
        "screen_at": state.screen_at,
        "ext_connected": {site: state.ext_connected(site) for site in EXT_SITES},
        "jobs": None if since == state.version else _job_list(),
    }


def _job_list() -> list[dict]:
    with state.jobs_lock:
        jobs = [{k: v for k, v in j.items() if k != "tailored_text"} for j in state.jobs.values()]
    return sorted(jobs, key=lambda j: -j["score"])


# ---------- Chrome 插件接口（extension/background.js 代为请求） ----------
def _ext_site(site: str) -> str:
    if site not in EXT_SITES:
        raise HTTPException(400, f"未知网站：{site}")
    return site


@app.get("/api/ext/poll")
def ext_poll(site: str, claim: int = 0, run: str = ""):
    state.ext_seen[_ext_site(site)] = time.time()
    request = state.ext_requests.pop(site, None) if claim else None
    if request:
        state.ext_run_seen[site] = time.time()
    state.ext_alive(site, run)
    reason = state.ext_stop_reason(site, run) if run else ""
    return {"request": request, "stop": bool(reason), "reason": reason}


@app.post("/api/ext/job")
def ext_job(body: dict):
    """插件看完一个岗位后提交，返回打分、打招呼话术，以及是否放进投递队列。"""
    site = _ext_site(body.get("platform", ""))
    job = Job(**{k: body.get(k) or ([] if k == "tags" else "") for k in JOB_FIELDS})
    key = f"{site}:{job.job_id}"
    state.ext_alive(site, body.get("run"))
    s, store = state.settings, state.storage()
    try:
        entry = state._screen_one(state.matcher(), store, key, job, tailor_now=False)
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001 —— 模型调用失败等，把原因告诉插件
        state.log(f"  ⚠ 打分失败：{job.title}：{e}")
        raise HTTPException(502, f"AI 打分失败：{e}") from e
    apply_it = False
    if entry["status"] == "matched" and s["auto_apply"]:
        if store.applied_today(site) + state.ext_queued.get(site, 0) < s["daily_apply_limit"]:
            apply_it = True
            state.ext_queued[site] = state.ext_queued.get(site, 0) + 1
        else:
            state.log(f"  今日投递名额已满，不再排队：{job.title}")
    if entry["status"] == "matched" and s["auto_tailor"]:  # 定制简历较慢，放后台生成，不耽误插件
        threading.Thread(target=state._tailor_safe, args=(entry,), daemon=True).start()
    reason = state.ext_stop_reason(site, body.get("run"))
    return {"key": key, "score": entry["score"], "greeting": entry["greeting"], "apply": apply_it,
            "stop": bool(reason), "reason": reason}


@app.post("/api/ext/applied")
def ext_applied(body: dict):
    entry = state.jobs.get(body.get("key", ""))
    site = entry["platform"] if entry else ""
    state.ext_alive(site, body.get("run"))
    if entry:
        state.ext_queued[site] = max(0, state.ext_queued.get(site, 0) - 1)
        ok = bool(body.get("ok"))
        entry["status"], entry["error"] = ("applied" if ok else "failed"), body.get("error", "")
        if ok:
            job = Job(**{k: v for k, v in entry.items() if k in JOB_FIELDS})
            state.storage().record(job, MatchResult(entry["score"], entry["reasons"], entry["greeting"]), "applied")
        state.touch_jobs()
        state.log(f"  {'✅ 已投递' if ok else '⚠ 投递失败'}：{entry['title']} @ {entry['company']}"
                  + (f"（{entry['error']}）" if entry["error"] else ""))
    reason = state.ext_stop_reason(site, body.get("run")) if site else ""
    return {"ok": True, "stop": bool(reason), "reason": reason}


@app.post("/api/ext/log")
def ext_log(body: dict):
    label = EXT_SITES.get(body.get("site", ""), {}).get("label", "插件")
    if body.get("site") in EXT_SITES:
        state.ext_alive(body["site"], body.get("run"))
    state.log(f"[{label}] {str(body.get('msg', ''))[:300]}")
    return {"ok": True}


@app.post("/api/ext/done")
def ext_done(body: dict):
    site = _ext_site(body.get("site", ""))
    if body.get("run") in (None, "", state.ext_active_id.get(site)):  # 残留旧任务结束时不影响新任务
        state.finish_ext(site)
    return {"ok": True}


@app.get("/api/screen")
def screen():
    if not state.screen:
        raise HTTPException(404, "暂无画面")
    return Response(state.screen, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@app.patch("/api/jobs/{key}")
def edit_job(key: str, body: dict):
    entry = state.jobs.get(key) or _404()
    if "greeting" in body:
        entry["greeting"] = str(body["greeting"])
    state.touch_jobs()
    return {"ok": True}


@app.post("/api/jobs/{key}/tailor")
def tailor(key: str):
    entry = state.jobs.get(key) or _404()
    state._tailor(entry)
    return {"ok": True}


@app.get("/api/jobs/{key}/resume")
def download_resume(key: str):
    entry = state.jobs.get(key) or _404()
    path = DATA / "tailored" / f"{_safe(key)}.docx"
    if not path.exists():
        raise HTTPException(404, "尚未生成定制简历")
    name = re.sub(r'[\\/:*?"<>|]', "_", f"简历-{entry['company']}-{entry['title']}.docx")
    return FileResponse(path, filename=name)


@app.delete("/api/jobs")
def clear_jobs():
    with state.jobs_lock:
        state.jobs.clear()
    state.touch_jobs()
    return {"ok": True}


def _404():
    raise HTTPException(404, "岗位不存在")
