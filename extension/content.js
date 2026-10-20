// AI 求职助手 · BOSS直聘插件
// 运行在你自己打开的 BOSS 页面里（没有调试连接，不会触发 BOSS 的反调试跳空白页）。
// 页面跳转会让脚本重新加载，所以运行进度按标签页存在 chrome.storage.local 里，加载后接着做。
//
// 流程：搜索阶段  列表页逐个点开岗位 → 发给本地服务打分 → 高分且开启自动投递的进入投递队列
//       投递阶段  打开岗位详情页 → 点「立即沟通」→「继续沟通」进入聊天页 → 发送打招呼 → 下一个

(() => {
  if (window.top !== window) return;

  // 选择器同时兼容新版（/web/geek/jobs，左列表右详情）和旧版页面，改版后在这里校准
  const CARD = ".job-card-box, .job-card-wrapper";
  const DESC = ".job-detail-box .desc, .job-detail-body .desc, p.desc, .job-sec-text";
  const DETAIL_BOX = ".job-detail-box, .job-detail-container, .job-detail";
  const CHAT_INPUT = "#chat-input, .chat-input[contenteditable], div[contenteditable='true'], textarea.input-area";

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const rand = (a, b) => a + Math.random() * (b - a);
  const $ = (s, root = document) => root.querySelector(s);
  const $$ = (s, root = document) => [...root.querySelectorAll(s)];
  const textOf = (root, sel) => { const e = root.querySelector(sel); return e ? e.innerText.trim() : ""; };
  const visible = (e) => !!(e && (e.offsetWidth || e.offsetHeight || e.getClientRects().length));

  const path = location.pathname;
  const page = path.startsWith("/web/geek/job") ? "list"
    : path.startsWith("/job_detail/") ? "detail"
    : path.startsWith("/web/geek/chat") ? "chat"
    : path.includes("security") ? "security" : "other";

  function api(path, body) {
    return new Promise((resolve) => {
      try {
        chrome.runtime.sendMessage({ type: "api", path, body }, (r) =>
          resolve(r || { ok: false, offline: true, data: {} }));
      } catch (e) { resolve({ ok: false, offline: true, data: { detail: String(e) } }); }
    });
  }

  async function waitFor(fn, timeout = 20000, interval = 500) {
    const end = Date.now() + timeout;
    while (Date.now() < end) {
      const v = fn();
      if (v) return v;
      await sleep(interval);
    }
    return null;
  }

  // 找文字完全等于 texts 之一的可见元素（按钮、链接）
  function findByText(texts, exclude) {
    const hits = $$("a, button, span, div").filter((e) =>
      e !== exclude && visible(e) && texts.includes(e.innerText.trim()) && e.innerText.length < 10);
    return hits[hits.length - 1] || null; // 后出现的（弹窗里的）优先
  }

  // ---------- 运行状态（按标签页保存） ----------
  let tabId = null, run = null, busy = false;
  const key = () => `run_${tabId}`;
  const save = () => (run ? chrome.storage.local.set({ [key()]: run }) : chrome.storage.local.remove(key()));

  // ---------- 页面左下角的小面板 ----------
  let ui = {};
  function renderPanel() {
    const host = document.createElement("div");
    host.style.cssText = "position:fixed;left:16px;bottom:16px;z-index:2147483647;";
    const root = host.attachShadow({ mode: "closed" });
    root.innerHTML = `
      <style>
        .box{width:300px;background:#fff;border-radius:10px;box-shadow:0 6px 24px rgba(0,0,0,.18);
             font:13px/1.5 -apple-system,"PingFang SC",sans-serif;color:#1d2129;overflow:hidden}
        .hd{background:#00a6a7;color:#fff;padding:8px 12px;display:flex;align-items:center;gap:6px;cursor:pointer}
        .dot{width:8px;height:8px;border-radius:50%;background:#ccc}.dot.on{background:#7CFC9A}
        .bd{padding:8px 12px}.st{font-weight:600;margin-bottom:4px}
        .lg{height:96px;overflow:auto;color:#4e5969;font-size:12px;white-space:pre-wrap;border-top:1px dashed #e5e6eb;padding-top:4px}
        .bt{display:flex;gap:8px;margin-top:8px} button{flex:1;border:1px solid #e5e6eb;background:#fff;border-radius:6px;padding:5px;cursor:pointer;font:inherit}
        button.p{background:#00a6a7;border-color:#00a6a7;color:#fff} .min .bd{display:none}
      </style>
      <div class="box"><div class="hd"><span class="dot"></span><b>AI 求职助手</b><span style="margin-left:auto" class="tg">－</span></div>
        <div class="bd"><div class="st">空闲</div><div class="lg"></div>
          <div class="bt"><button class="p go">开始</button><button class="stop">停止</button></div></div></div>`;
    document.documentElement.appendChild(host);
    ui = { box: $(".box", root), dot: $(".dot", root), st: $(".st", root), lg: $(".lg", root) };
    $(".hd", root).onclick = () => ui.box.classList.toggle("min");
    $(".go", root).onclick = async () => {
      const r = await api("/api/search", {});
      if (!r.ok) log("⚠ " + (r.offline ? "连不上本地助手，请先双击 start.command" : r.data.detail));
    };
    $(".stop", root).onclick = async () => { await api("/api/stop", {}); await finish("已手动停止"); };
  }
  const setStatus = (s) => ui.st && (ui.st.textContent = s);
  function log(msg, remote = true) {
    if (ui.lg) { ui.lg.textContent += (ui.lg.textContent ? "\n" : "") + msg; ui.lg.scrollTop = 1e9; }
    if (remote) api("/api/ext/log", { msg });
  }

  async function finish(reason) {
    if (!run) return;
    log(`⏹ ${reason}`);
    run = null;
    await save();
    await api("/api/ext/done", {});
    setStatus("空闲");
  }

  // ---------- 搜索阶段 ----------
  const searchUrl = (t) =>
    `https://www.zhipin.com/web/geek/jobs?query=${encodeURIComponent(t.keyword)}&city=${t.city_code}`;

  async function doSearch() {
    const task = run.tasks[run.taskIdx];
    if (!task) {
      run.phase = "apply";
      await save();
      log(`搜索完成，待投递 ${run.applyQueue.length} 个`);
      return doApply();
    }
    const params = new URLSearchParams(location.search);
    const onTarget = page === "list" && params.get("query") === task.keyword && params.get("city") === task.city_code;
    if (!onTarget) {
      run.navTries = (run.navTries || 0) + 1;
      if (run.navTries > 3) { // 反复跳不到目标页，跳过这个搜索
        log(`⚠ 打不开「${task.keyword}」@ ${task.city} 的搜索页，跳过`);
        run.taskIdx++; run.navTries = 0; await save();
        return doSearch();
      }
      await save();
      log(`🔍 搜索「${task.keyword}」@ ${task.city}`);
      await sleep(rand(800, 1800));
      location.href = searchUrl(task);
      return;
    }
    run.navTries = 0;
    await scanList(task);
    if (!run) return;
    run.taskIdx++;
    await save();
    await sleep(rand(1500, 3000));
    return doSearch();
  }

  function cardToJob(card) {
    const a = card.querySelector("a[href*='/job_detail/']");
    const href = a ? a.getAttribute("href") : "";
    return {
      platform: "boss",
      job_id: href ? href.split("/job_detail/")[1].split(".html")[0] : "",
      title: textOf(card, ".job-name, .job-title"),
      company: textOf(card, ".boss-name, .company-name, .company-info h3"),
      salary: textOf(card, ".job-salary, .salary"),
      city: textOf(card, ".company-location, .job-area"),
      url: href ? new URL(href, location.origin).href : "",
      tags: $$(".tag-list li", card).map((e) => e.innerText.trim()),
      description: "",
    };
  }

  async function scanList(task) {
    setStatus(`搜索「${task.keyword}」@ ${task.city}`);
    if (!(await waitFor(() => $$(CARD).length, 25000))) {
      log("⚠ 没找到职位列表（页面结构可能变了，或正在安全验证）");
      return;
    }
    const known = new Set(run.known || []);
    let i = 0, viewed = 0, idleScrolls = 0;
    while (run && viewed < run.limit) {
      const cards = $$(CARD);
      if (i >= cards.length) { // 滚动加载更多
        if (idleScrolls >= 4) break;
        cards[cards.length - 1].scrollIntoView({ block: "end", behavior: "smooth" });
        await sleep(rand(1500, 2500));
        idleScrolls = $$(CARD).length > cards.length ? 0 : idleScrolls + 1;
        continue;
      }
      const card = cards[i++];
      const job = cardToJob(card);
      if (!job.job_id || known.has(job.job_id)) continue;

      card.scrollIntoView({ block: "center", behavior: "smooth" });
      await sleep(rand(600, 1400));
      const before = textOf(document, DESC);
      card.click(); // 点卡片本身（不点标题链接），右侧显示详情
      await waitFor(() => { const d = textOf(document, DESC); return d && d !== before; }, 8000);
      job.description = textOf(document, DESC) || textOf(document, DETAIL_BOX);
      viewed++;
      known.add(job.job_id);
      run.known = [...known];
      setStatus(`查看第 ${viewed} 个：${job.title}`);
      await sleep(rand(1500, 3500)); // 像人一样读一会儿

      const r = await api("/api/ext/job", job);
      if (!r.ok) {
        if (r.offline) return finish("连不上本地助手，请确认 start.command 正在运行");
        log("⚠ " + (r.data.detail || "打分失败"));
        if (r.status >= 400 && r.status < 500) return finish("配置不完整，请在 8765 网页补全后重试");
        continue;
      }
      log(`${r.data.score} 分  ${job.title} @ ${job.company}`, false);
      if (r.data.apply) run.applyQueue.push({ key: r.data.key, url: job.url, greeting: r.data.greeting, title: job.title });
      await save();
      if (r.data.stop) return finish("已停止");
      await sleep(rand(run.min_delay * 1000, run.max_delay * 1000));
    }
  }

  // ---------- 投递阶段 ----------
  async function doApply() {
    const item = run.applyQueue[run.applyIdx];
    if (!item) return finish(`全部完成，本次投递 ${run.applied || 0} 个`);
    setStatus(`投递 ${run.applyIdx + 1}/${run.applyQueue.length}：${item.title}`);
    if (page === "chat" && run.step === "chat") return sendGreeting(item);
    if (page === "detail" && location.href.split("?")[0] === item.url.split("?")[0]) return clickChat(item);
    run.step = null;
    await save();
    await sleep(rand(1000, 2500));
    location.href = item.url;
  }

  async function clickChat(item) {
    const btn = await waitFor(() => findByText(["立即沟通", "继续沟通"]), 15000);
    if (!btn) return report(item, false, "没找到「立即沟通」按钮");
    if (btn.innerText.includes("继续沟通")) return report(item, false, "之前已经沟通过");
    await sleep(rand(1500, 3500)); // 先看看岗位
    run.step = "chat";
    await save();
    btn.click();
    // 点击后通常弹出「已向BOSS发送消息」，选「继续沟通」进入聊天页
    const go = await waitFor(() => findByText(["继续沟通"], btn), 6000);
    if (go) { await sleep(rand(800, 1500)); go.click(); }
    await sleep(8000);
    if (page === "detail" && location.pathname.startsWith("/job_detail/")) { // 还在详情页：可能被限制了
      const tip = $$(".dialog-container, .boss-dialog, .toast, [class*='dialog']").filter(visible).map((e) => e.innerText.trim()).join(" ").slice(0, 80);
      return report(item, false, "点击后没有进入聊天页" + (tip ? "：" + tip : ""));
    }
  }

  async function sendGreeting(item) {
    const box = await waitFor(() => { const b = $(CHAT_INPUT); return visible(b) && b; }, 20000);
    if (!box) return report(item, false, "没找到聊天输入框");
    await sleep(rand(1500, 3000));
    box.focus();
    if (box.isContentEditable) {
      document.execCommand("insertText", false, item.greeting); // 产生和真实输入一样的 input 事件
    } else {
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value").set.call(box, item.greeting);
      box.dispatchEvent(new Event("input", { bubbles: true }));
    }
    await sleep(rand(800, 1600));
    const send = findByText(["发送"]) || $(".btn-send, .submit-content .btn");
    if (send) send.click();
    else box.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", code: "Enter", keyCode: 13, bubbles: true }));
    await sleep(1500);
    const left = (box.innerText || box.value || "").trim();
    return report(item, !left, left ? "消息可能没发出去，请到聊天页确认" : "");
  }

  async function report(item, ok, error) {
    run.step = null;
    run.applyIdx++;
    if (ok) run.applied = (run.applied || 0) + 1;
    await save();
    const r = await api("/api/ext/applied", { key: item.key, ok, error });
    log(`${ok ? "✅ 已投递" : "⚠ 投递失败"}：${item.title}${error ? "（" + error + "）" : ""}`, false);
    if (r.data && r.data.stop) return finish("已停止");
    await sleep(rand(run.min_delay * 1000, run.max_delay * 1000));
    return doApply();
  }

  // ---------- 调度 ----------
  async function resume() {
    if (busy || !run) return;
    busy = true;
    try {
      if (page === "security") return setStatus("等待 BOSS 安全验证，有滑块请手动完成…");
      if (run.phase === "search") await doSearch();
      else await doApply();
    } catch (e) {
      log("❌ 出错：" + e.message);
    } finally {
      busy = false;
    }
  }

  async function poll() {
    const r = await api(`/api/ext/poll?claim=${run ? 0 : 1}&page=${page}`);
    ui.dot && ui.dot.classList.toggle("on", !r.offline);
    if (r.offline) { if (!run) setStatus("未连接本地助手（请双击 start.command）"); return; }
    if (run && r.data.stop) return finish("已停止");
    const req = r.data.request;
    if (!run && req) {
      run = { ...req, phase: req.kind === "apply" ? "apply" : "search", taskIdx: 0, applyIdx: 0, applied: 0,
              applyQueue: req.apply_queue || [], step: null };
      await save();
      log(req.kind === "apply" ? `开始投递 ${run.applyQueue.length} 个岗位` : `开始筛选：${req.tasks.length} 组搜索`);
      resume();
    } else if (!run) {
      setStatus("空闲 · 在 8765 网页点「开始筛选」或这里点「开始」");
    }
  }

  (async () => {
    tabId = await new Promise((r) => chrome.runtime.sendMessage({ type: "tabId" }, r));
    run = (await chrome.storage.local.get(key()))[key()] || null;
    renderPanel();
    if (run) { setStatus("继续上次的任务…"); await sleep(rand(1500, 3000)); resume(); }
    poll();
    setInterval(poll, 3000);
  })();
})();
