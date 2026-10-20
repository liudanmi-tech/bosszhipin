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
    "api_key": "",
    "base_url": "https://openrouter.ai/api/v1",
    "model": "deepseek/deepseek-v4.1-flash",
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
        self.worker = BrowserWorker()
        self._storage: Storage | None = None
        # 各招聘网站由 Chrome 插件在用户自己的浏览器里操作（见 extension/），以下按网站记录
        self.ext_requests: dict[str, dict] = {}  # 等插件领取的任务
        self.ext_running: set[str] = set()       # 本轮还没结束的网站
        self.ext_seen: dict[str, float] = {}     # 插件最近一次来领任务的时间
        self.ext_queued: dict[str, int] = {}     # 已排进投递队列、尚未回报的数量（计入每日上限）

    # ---------- 基础 ----------
    def log(self, msg: str) -> None:
        self.logs.append(f"{time.strftime('%H:%M:%S')}  {msg}")

    def touch_jobs(self) -> None:
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
        llm = LLM({"base_url": s["base_url"], "model": s["model"], "api_key": s["api_key"]})
        return Matcher(llm, s["resume_text"], prefs)

    def storage(self) -> Storage:
        with self.lock:
            if self._storage is None:
                self._storage = Storage()
        return self._storage

    def ext_connected(self, site: str) -> bool:
        return time.time() - self.ext_seen.get(site, 0) < 8

    def start_ext(self, name: str, requests: dict[str, dict]) -> None:
        """把任务交给各网站的插件标签页；插件轮询 /api/ext/poll 领取。"""
        with self.lock:
            if self.task:
                raise HTTPException(409, f"正在执行「{self.task}」，请等待完成或点击停止")
            self.task, self.stop, self.ext_queued = name, False, {}
            base = {"id": time.time(), "min_delay": self.settings["min_delay_sec"],
                    "max_delay": self.settings["max_delay_sec"]}
            self.ext_requests = {site: {**base, **req} for site, req in requests.items()}
            self.ext_running = set(requests)
        for site in requests:
            info = EXT_SITES[site]
            if self.ext_connected(site):
                self.log(f"[{info['label']}] 已交给插件开始{name}")
            else:
                self.log(f"⚠ [{info['label']}] 插件未连接：请用装了插件的 Chrome 打开 {info['url']} 并登录，打开后自动开始")

    def finish_ext(self, site: str) -> None:
        self.ext_requests.pop(site, None)
        if site in self.ext_running:
            self.ext_running.discard(site)
            self.log(f"[{EXT_SITES[site]['label']}] 完成")
        if not self.ext_running and self.task:
            self.log(f"— {self.task}结束 —")
            self.task = None

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
        entry["tailored_text"] = text
        markdown_to_docx(text, DATA / "tailored" / f"{_safe(entry['key'])}.docx")
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
    s["has_api_key"] = bool(s.pop("api_key"))
    s["platform_options"] = [{"name": k, "label": v["label"], "url": v["url"], "beta": v.get("beta", False)}
                             for k, v in EXT_SITES.items()]
    return s


@app.post("/api/settings")
def save_settings(body: dict):
    for k, v in body.items():
        if k not in DEFAULT_SETTINGS or (k == "api_key" and not v):
            continue
        state.settings[k] = v
    _save(SETTINGS_FILE, state.settings)
    return {"ok": True}


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
def search():
    s = state.settings
    if not s["resume_text"].strip():
        raise HTTPException(400, "请先上传简历")
    if not s["keywords"]:
        raise HTTPException(400, "请至少填写一个职位关键词")
    if not s["api_key"]:
        raise HTTPException(400, "请先填写 OpenRouter API Key")
    sites = [p for p in s["platforms"] if p in EXT_SITES]
    if not sites:
        raise HTTPException(400, "请至少勾选一个渠道")
    requests = {}
    for site in sites:
        tasks, unknown = build_tasks(site, s["keywords"], s["cities"])
        if unknown:
            state.log(f"⚠ [{EXT_SITES[site]['label']}] 这些城市暂不支持，按全国搜索：{'、'.join(unknown)}")
        known = [j["job_id"] for j in state.jobs.values() if j["platform"] == site]
        requests[site] = {"kind": "search", "tasks": tasks, "limit": s["max_per_search"], "known": known}
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
def stop():
    state.stop = True
    for site in list(state.ext_requests):  # 插件还没领走的任务直接取消
        state.finish_ext(site)
    state.log("收到停止指令，当前岗位处理完后停止")
    return {"ok": True}


@app.get("/api/status")
def status(since: int = -1):
    return {
        "task": state.task,
        "logs": list(state.logs),
        "logged_in": state.logged_in,
        "version": state.version,
        "screen_at": state.screen_at,
        "ext_connected": {site: state.ext_connected(site) for site in EXT_SITES},
        "jobs": None if since == state.version else sorted(
            ({k: v for k, v in j.items() if k != "tailored_text"} for j in state.jobs.values()),
            key=lambda j: -j["score"],
        ),
    }


# ---------- Chrome 插件接口（extension/background.js 代为请求） ----------
def _ext_site(site: str) -> str:
    if site not in EXT_SITES:
        raise HTTPException(400, f"未知网站：{site}")
    return site


@app.get("/api/ext/poll")
def ext_poll(site: str, claim: int = 0):
    state.ext_seen[_ext_site(site)] = time.time()
    request = state.ext_requests.pop(site, None) if claim else None
    return {"request": request, "stop": state.stop}


@app.post("/api/ext/job")
def ext_job(body: dict):
    """插件看完一个岗位后提交，返回打分、打招呼话术，以及是否放进投递队列。"""
    site = _ext_site(body.get("platform", ""))
    job = Job(**{k: body.get(k) or ([] if k == "tags" else "") for k in JOB_FIELDS})
    key = f"{site}:{job.job_id}"
    s, store = state.settings, state.storage()
    entry = state._screen_one(state.matcher(), store, key, job, tailor_now=False)
    apply_it = False
    if entry["status"] == "matched" and s["auto_apply"]:
        if store.applied_today(site) + state.ext_queued.get(site, 0) < s["daily_apply_limit"]:
            apply_it = True
            state.ext_queued[site] = state.ext_queued.get(site, 0) + 1
        else:
            state.log(f"  今日投递名额已满，不再排队：{job.title}")
    if entry["status"] == "matched" and s["auto_tailor"]:  # 定制简历较慢，放后台生成，不耽误插件
        threading.Thread(target=state._tailor_safe, args=(entry,), daemon=True).start()
    return {"key": key, "score": entry["score"], "greeting": entry["greeting"], "apply": apply_it, "stop": state.stop}


@app.post("/api/ext/applied")
def ext_applied(body: dict):
    entry = state.jobs.get(body.get("key", ""))
    if entry:
        site = entry["platform"]
        state.ext_queued[site] = max(0, state.ext_queued.get(site, 0) - 1)
        ok = bool(body.get("ok"))
        entry["status"], entry["error"] = ("applied" if ok else "failed"), body.get("error", "")
        if ok:
            job = Job(**{k: v for k, v in entry.items() if k in JOB_FIELDS})
            state.storage().record(job, MatchResult(entry["score"], entry["reasons"], entry["greeting"]), "applied")
        state.touch_jobs()
        state.log(f"  {'✅ 已投递' if ok else '⚠ 投递失败'}：{entry['title']} @ {entry['company']}"
                  + (f"（{entry['error']}）" if entry["error"] else ""))
    return {"ok": True, "stop": state.stop}


@app.post("/api/ext/log")
def ext_log(body: dict):
    label = EXT_SITES.get(body.get("site", ""), {}).get("label", "插件")
    state.log(f"[{label}] {str(body.get('msg', ''))[:300]}")
    return {"ok": True}


@app.post("/api/ext/done")
def ext_done(body: dict):
    state.finish_ext(_ext_site(body.get("site", "")))
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
    state.jobs.clear()
    state.touch_jobs()
    return {"ok": True}


def _404():
    raise HTTPException(404, "岗位不存在")
