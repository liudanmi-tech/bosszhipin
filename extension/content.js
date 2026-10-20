// AI 求职助手 · 招聘网站插件（BOSS直聘 / 猎聘 / 脉脉）
// 运行在你自己打开的招聘网站页面里（没有调试连接，不会触发 BOSS 的反调试跳空白页）。
// 页面跳转会让脚本重新加载，所以运行进度按标签页存在 chrome.storage.local 里，加载后接着做。
//
// 流程：搜索阶段  列表页逐个查看岗位 → 发给本地服务打分 → 高分且开启自动投递的进入投递队列
//       投递阶段  打开岗位详情页 → 点沟通按钮 → 在聊天框发送打招呼 → 下一个
// 每个网站的页面差异都在下面的 SITES 里，改版后在对应 adapter 里校准选择器。

(() => {
  if (window.top !== window) return;

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const rand = (a, b) => a + Math.random() * (b - a);
  const $ = (s, root = document) => root.querySelector(s);
  const $$ = (s, root = document) => [...root.querySelectorAll(s)];
  const textOf = (root, sel) => { const e = root.querySelector(sel); return e ? e.innerText.trim() : ""; };
  const visible = (e) => !!(e && (e.offsetWidth || e.offsetHeight || e.getClientRects().length));
  const enc = encodeURIComponent;
  const SALARY = /\d+(?:\.\d+)?(?:-\d+(?:\.\d+)?)?\s*[kK万千](?:[·.]\d+薪)?|面议/;
  const hash = (s) => { let h = 0; for (const c of s) h = (h * 31 + c.charCodeAt(0)) | 0; return "h" + (h >>> 0).toString(36); };

  // BOSS 的薪资数字用动态字体加密（私有区字符），读不出就当作未知，不把乱码交给 AI
  const plainSalary = (s) => (/[\ue000-\uf8ff]/.test(s) ? "" : s);

  // 找文字完全等于 texts 之一的可见元素（按钮、链接）；last=true 取最后出现的（弹窗里的）
  function findByText(texts, exclude, last = false) {
    let hits = $$("a, button, span, div").filter((e) =>
      e !== exclude && visible(e) && e.innerText.length < 12 && texts.includes(e.innerText.trim()));
    hits = hits.filter((e) => !hits.some((o) => o !== e && e.contains(o))); // 只要最内层（真正的按钮），不要包着它的容器
    return (last ? hits[hits.length - 1] : hits[0]) || null;
  }

  // 像真人一样点：鼠标移入 → 在元素里移动几下 → 按下 → 停顿 → 松开 → click，坐标随机落在元素内部
  async function humanClick(el) {
    const r = el.getBoundingClientRect();
    const x = r.left + r.width * rand(0.25, 0.75), y = r.top + r.height * rand(0.3, 0.7);
    const opts = () => ({ bubbles: true, cancelable: true, view: window, clientX: x + rand(-3, 3), clientY: y + rand(-2, 2), button: 0 });
    el.dispatchEvent(new PointerEvent("pointerover", opts()));
    el.dispatchEvent(new MouseEvent("mouseover", opts()));
    el.dispatchEvent(new MouseEvent("mouseenter", { ...opts(), bubbles: false }));
    for (let k = 0; k < 3; k++) { el.dispatchEvent(new MouseEvent("mousemove", opts())); await sleep(rand(30, 90)); }
    el.dispatchEvent(new PointerEvent("pointerdown", opts()));
    el.dispatchEvent(new MouseEvent("mousedown", opts()));
    await sleep(rand(60, 160));
    el.dispatchEvent(new PointerEvent("pointerup", opts()));
    el.dispatchEvent(new MouseEvent("mouseup", opts()));
    el.dispatchEvent(new MouseEvent("click", opts()));
  }

  // 查看岗位的节奏（毫秒）：scroll 滚到卡片后、read 读详情、gap 两个岗位之间。投递间隔不受影响
  const SPEEDS = {
    fast: { scroll: [300, 700], read: [500, 1200], gap: [800, 2000] },
    normal: { scroll: [500, 1100], read: [1000, 2500], gap: [1500, 3500] },
    slow: { scroll: [600, 1400], read: [1500, 3500], gap: [4000, 10000] },
  };
  const pause = ([a, b]) => sleep(rand(a, b));

  async function waitFor(fn, timeout = 20000, interval = 500) {
    const end = Date.now() + timeout;
    while (Date.now() < end) {
      const v = fn();
      if (v) return v;
      await sleep(interval);
    }
    return null;
  }

  const CHAT_INPUT = "#chat-input, .chat-input[contenteditable], div[contenteditable='true'], textarea.input-area, .im-chat textarea, [class*='chat'] textarea";
  const chatInput = () => $$(CHAT_INPUT).find(visible) || null;

  // 抓取同站详情页 HTML 并取出职位描述（服务端渲染的网站可用，不用跳转页面）
  async function fetchDescription(url, selectors) {
    try {
      const html = await (await fetch(url, { credentials: "include" })).text();
      const doc = new DOMParser().parseFromString(html, "text/html");
      for (const sel of selectors) {
        const e = doc.querySelector(sel);
        if (e && e.textContent.trim()) return e.textContent.replace(/\s+\n/g, "\n").replace(/[ \t]+/g, " ").trim().slice(0, 4000);
      }
    } catch (e) { /* 拿不到就只用卡片信息 */ }
    return "";
  }

  // ======================= 各网站适配 =======================
  const SITES = {
    boss: {
      label: "BOSS直聘",
      host: /(^|\.)zhipin\.com$/,
      pageType: (p) => p.startsWith("/web/geek/job") ? "list" : p.startsWith("/job_detail/") ? "detail"
        : p.startsWith("/web/geek/chat") ? "chat" : p.includes("security") ? "security" : "other",
      searchUrl: (t) => `https://www.zhipin.com/web/geek/jobs?query=${enc(t.keyword)}&city=${t.city_code}`,
      onTarget: (t, q) => q.get("query") === t.keyword && q.get("city") === t.city_code,
      cards: () => $$(".job-card-box, .job-card-wrapper"),
      cardToJob(card) {
        const a = card.querySelector("a[href*='/job_detail/']");
        const href = a ? a.getAttribute("href") : "";
        return {
          job_id: href ? href.split("/job_detail/")[1].split(".html")[0] : "",
          title: textOf(card, ".job-name, .job-title"),
          company: textOf(card, ".boss-name, .company-name, .company-info h3"),
          salary: plainSalary(textOf(card, ".job-salary, .salary")),
          city: textOf(card, ".company-location, .job-area"),
          url: href ? new URL(href, location.origin).href : "",
          tags: $$(".tag-list li", card).map((e) => e.innerText.trim()),
        };
      },
      async readDetail(card, job) { // 新版：点卡片本身（不点标题链接），右侧显示详情
        const DESC = ".job-detail-box .desc, .job-detail-body .desc, p.desc, .job-sec-text";
        const before = textOf(document, DESC);
        await humanClick(card);
        await waitFor(() => { const d = textOf(document, DESC); return d && d !== before; }, 8000);
        job.description = textOf(document, DESC) || textOf(document, ".job-detail-box, .job-detail-container");
      },
      chatButton: () => findByText(["立即沟通", "继续沟通"]),
      contactedTexts: ["继续沟通"],
      followTexts: ["继续沟通"], // 点击后弹窗「已向BOSS发送消息」→ 继续沟通 进入聊天页
      loggedOut: () => false,
    },

    liepin: {
      label: "猎聘",
      host: /(^|\.)liepin\.com$/,
      pageType: (p) => p.startsWith("/zhaopin") ? "list" : /^\/(job|a)\/\d+/.test(p) ? "detail"
        : /im|chat/.test(p) ? "chat" : "other",
      searchUrl: (t) => `https://www.liepin.com/zhaopin/?key=${enc(t.keyword)}&dq=${t.city_code}`,
      onTarget: (t, q) => q.get("key") === t.keyword && q.get("dq") === t.city_code,
      cards: () => $$("[class*='jobCardPcContainer']"),
      cardToJob(card) {
        const a = card.querySelector("a[data-nick='job-detail-job-info'], a[href*='/job/'], a[href*='/a/']");
        const url = a ? new URL(a.getAttribute("href"), location.origin) : null;
        const m = url && url.pathname.match(/\/(job|a)\/(\d+)/);
        const titleBox = card.querySelector("[class*='jobTitleBox']");
        const parts = titleBox ? $$(".ellipsis-1", titleBox).map((e) => e.innerText.trim()) : [];
        const text = card.innerText;
        return {
          job_id: m ? `${m[1]}${m[2]}` : "",
          title: parts[0] || "",
          city: parts[1] || "",
          company: textOf(card, "[class*='companyName']"),
          salary: (text.match(SALARY) || [""])[0],
          url: url ? url.origin + url.pathname : "",
          tags: text.split("\n").map((s) => s.trim()).filter((s) => /年|本科|硕士|大专|学历/.test(s) && s.length < 8),
        };
      },
      async readDetail(card, job) { // 详情页是服务端渲染的，直接抓取，不用来回跳转
        card.dispatchEvent(new MouseEvent("mouseover", { bubbles: true }));
        job.description = await fetchDescription(job.url, [".job-intro-container", ".job-description", "[class*='job-intro']"])
          || card.innerText;
      },
      chatButton: () => $$(".job-apply-container a.btn-main, a.btn-main, a.btn-chat, .job-apply-content button")
        .find((e) => visible(e) && /聊一聊|继续聊|立即沟通/.test(e.innerText)) || null,
      contactedTexts: ["继续聊"],
      followTexts: [],
      loggedOut: () => !!findByText(["登录/注册", "登录"]),
    },

    // 脉脉：测试版。搜索需登录，页面结构没能实测，用通用规则识别，可能需要按你的截图校准
    maimai: {
      label: "脉脉",
      host: /(^|\.)maimai\.cn$/,
      pageType: (p) => p.includes("search_center") ? "list" : /job|position/.test(p) ? "detail"
        : /chat|message|im/.test(p) ? "chat" : "other",
      searchUrl: (t) => `https://maimai.cn/web/search_center?type=job&query=${enc(t.keyword)}`,
      onTarget: (t, q) => q.get("query") === t.keyword,
      cards() {
        const seen = new Set();
        return $$("a[href*='job'], a[href*='position'], a[href*='jid=']")
          .filter((a) => visible(a) && a.innerText.trim().length > 3)
          .map((a) => a.closest("li, [class*='item'], [class*='card'], [class*='Item'], [class*='Card']") || a)
          .filter((c) => !seen.has(c) && seen.add(c));
      },
      cardToJob(card) {
        const a = card.matches("a") ? card : card.querySelector("a[href*='job'], a[href*='position'], a[href*='jid=']");
        const href = a ? new URL(a.getAttribute("href"), location.origin).href : "";
        const lines = card.innerText.split("\n").map((s) => s.trim()).filter(Boolean);
        const id = (href.match(/[?&](?:jid|job_id|id)=(\w+)/) || href.match(/(\d{4,})/) || [])[1];
        return {
          job_id: id || hash(lines.slice(0, 2).join("|")),
          title: lines[0] || "",
          salary: (card.innerText.match(SALARY) || [""])[0],
          company: lines.find((l, i) => i > 0 && !SALARY.test(l) && l.length < 30) || "",
          city: "",
          url: href,
          tags: [],
        };
      },
      async readDetail(card, job) {
        job.description = (job.url && await fetchDescription(job.url, ["[class*='description']", "[class*='detail']", "main"]))
          || card.innerText;
      },
      chatButton: () => findByText(["立即沟通", "沟通", "聊一聊", "申请职位", "立即申请", "投递简历", "继续沟通", "已申请"]),
      contactedTexts: ["继续沟通", "已申请", "已投递"],
      followTexts: ["继续沟通", "去聊天", "发消息"],
      loggedOut: () => /\/login/.test(location.pathname),
    },
  };

  const siteName = Object.keys(SITES).find((k) => SITES[k].host.test(location.hostname));
  if (!siteName) return;
  const site = SITES[siteName];
  const page = site.pageType(location.pathname);

  function api(path, body) {
    return new Promise((resolve) => {
      try {
        chrome.runtime.sendMessage({ type: "api", path, body }, (r) =>
          resolve(r || { ok: false, offline: true, data: {} }));
      } catch (e) { resolve({ ok: false, offline: true, data: { detail: String(e) } }); }
    });
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
      <div class="box"><div class="hd"><span class="dot"></span><b>AI 求职助手 · ${site.label}</b><span style="margin-left:auto">－</span></div>
        <div class="bd"><div class="st">空闲</div><div class="lg"></div>
          <div class="bt"><button class="p go">开始</button><button class="stop">停止</button></div></div></div>`;
    document.documentElement.appendChild(host);
    ui = { box: $(".box", root), dot: $(".dot", root), st: $(".st", root), lg: $(".lg", root) };
    $(".hd", root).onclick = () => ui.box.classList.toggle("min");
    $(".go", root).onclick = async () => {
      const r = await api("/api/search", { site: siteName });
      if (!r.ok) log("⚠ " + (r.offline ? "连不上本地助手，请先双击 start.command" : r.data.detail), false);
    };
    $(".stop", root).onclick = async () => { await api("/api/stop", { site: siteName }); await finish("已手动停止"); };
  }
  const setStatus = (s) => ui.st && (ui.st.textContent = s);
  function log(msg, remote = true) {
    if (ui.lg) { ui.lg.textContent += (ui.lg.textContent ? "\n" : "") + msg; ui.lg.scrollTop = 1e9; }
    if (remote) api("/api/ext/log", { site: siteName, msg, run: run && run.id });
  }

  async function finish(reason) {
    if (!run) return;
    log(`⏹ ${reason}`);
    const id = run.id;
    run = null;
    await save();
    await api("/api/ext/done", { site: siteName, run: id });
    setStatus("空闲");
  }

  // ---------- 搜索阶段 ----------
  async function doSearch() {
    const task = run.tasks[run.taskIdx];
    if (!task) {
      run.phase = "apply";
      await save();
      log(`搜索完成，待投递 ${run.applyQueue.length} 个`);
      return doApply();
    }
    if (!(page === "list" && site.onTarget(task, new URLSearchParams(location.search)))) {
      run.navTries = (run.navTries || 0) + 1;
      if (run.navTries > 3) { // 反复跳不到目标页，跳过这个搜索
        log(`⚠ 打不开「${task.keyword}」@ ${task.city} 的搜索页，跳过`);
        run.taskIdx++; run.navTries = 0; await save();
        return doSearch();
      }
      await save();
      log(`🔍 搜索「${task.keyword}」@ ${task.city}`);
      await sleep(rand(800, 1800));
      location.href = site.searchUrl(task);
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

  async function scanList(task) {
    setStatus(`搜索「${task.keyword}」@ ${task.city}`);
    if (!(await waitFor(() => site.cards().length, 25000))) {
      log("⚠ 没找到职位列表（页面结构可能变了，或正在安全验证）");
      return;
    }
    const speed = SPEEDS[run.speed] || SPEEDS.normal;
    const known = new Set(run.known || []);
    const pending = new Set(); // 正在打分的请求：边看边打分，不等 AI 返回就看下一个
    let i = 0, viewed = 0, idleScrolls = 0;
    while (run && viewed < run.limit) {
      const cards = site.cards();
      if (i >= cards.length) { // 滚动加载更多
        if (idleScrolls >= 4) break;
        cards[cards.length - 1].scrollIntoView({ block: "end", behavior: "smooth" });
        await sleep(rand(1500, 2500));
        idleScrolls = site.cards().length > cards.length ? 0 : idleScrolls + 1;
        continue;
      }
      const card = cards[i++];
      const job = { platform: siteName, description: "", ...site.cardToJob(card) };
      if (!job.job_id || !job.title || known.has(job.job_id)) continue;

      card.scrollIntoView({ block: "center", behavior: "smooth" });
      await pause(speed.scroll);
      await site.readDetail(card, job);
      if (!run) break; // 后台打分出错时可能已经结束了任务
      viewed++;
      known.add(job.job_id);
      run.known = [...known];
      setStatus(`查看第 ${viewed} 个：${job.title}`);
      await pause(speed.read); // 像人一样读一会儿
      if (!run) break;

      while (pending.size >= 3) await Promise.race(pending); // 最多同时打 3 个分
      const p = scoreJob(job).finally(() => pending.delete(p));
      pending.add(p);
      await pause(speed.gap);
    }
    await Promise.all(pending); // 离开列表页前等所有打分回来，否则高分岗位进不了投递队列
  }

  async function scoreJob(job) {
    const r = await api("/api/ext/job", { ...job, run: run.id });
    if (!run) return;
    if (!r.ok) {
      if (r.offline) return finish("连不上本地助手，请确认 start.command 正在运行");
      const why = r.data.detail || `打分失败（${r.status}）`;
      log("⚠ " + why, false);
      if (r.status >= 400 && r.status < 500) return finish(why);
      run.fails = (run.fails || 0) + 1;
      if (run.fails >= 3) return finish("AI 打分连续失败，请检查 8765 网页里的模型和 API Key：" + why);
      return;
    }
    run.fails = 0;
    log(`${r.data.score} 分  ${job.title} @ ${job.company}`, false);
    if (r.data.apply) run.applyQueue.push({ key: r.data.key, url: job.url, greeting: r.data.greeting, title: job.title });
    await save();
    if (r.data.stop) return finish(r.data.reason || "已停止");
  }

  // ---------- 投递阶段 ----------
  // 同一路径，且岗位链接里的参数（如 ?jid=）都一致；网站跳转后改了参数时，以「刚为这个岗位跳转过」为准
  const samePage = (item) => {
    const u = new URL(item.url), q = new URLSearchParams(location.search);
    if (u.origin + u.pathname !== location.origin + location.pathname) return false;
    return run.navFor === item.key || [...u.searchParams].every(([k, v]) => q.get(k) === v);
  };

  async function doApply() {
    const item = run.applyQueue[run.applyIdx];
    if (!item) return finish(`全部完成，本次投递 ${run.applied || 0} 个`);
    setStatus(`投递 ${run.applyIdx + 1}/${run.applyQueue.length}：${item.title}`);
    if (page === "chat" && run.step === "chat") return sendGreeting(item);
    if (page === "detail" && samePage(item)) return clickChat(item);
    run.navTries = run.navFor === item.key ? (run.navTries || 0) + 1 : 1;
    if (run.navTries > 3) return report(item, false, "打不开岗位页");
    run.step = null;
    run.navFor = item.key;
    await save();
    await sleep(rand(1000, 2500));
    location.href = item.url;
  }

  async function clickChat(item) {
    if (site.loggedOut()) return finish(`请先在${site.label}登录，然后重新开始`);
    const btn = await waitFor(site.chatButton, 15000);
    if (!btn) return report(item, false, "没找到沟通按钮");
    if (site.contactedTexts.includes(btn.innerText.trim())) return report(item, false, "之前已经沟通过");
    await sleep(rand(1500, 3500)); // 先看看岗位
    run.step = "chat";
    await save();
    await humanClick(btn);
    // 有的网站弹窗问是否继续沟通；有的跳到聊天页（脚本会在新页面接着发）；有的在当前页弹出聊天框
    if (site.followTexts.length) {
      const go = await waitFor(() => findByText(site.followTexts, btn, true), 6000);
      if (go) { await sleep(rand(800, 1500)); await humanClick(go); }
    }
    const box = await waitFor(chatInput, 8000);
    if (box) return sendGreeting(item, box);
    if (site.pageType(location.pathname) !== "detail") return; // 正在跳转到聊天页
    const tip = $$("[class*='dialog'], [class*='modal'], [class*='toast']").filter(visible)
      .map((e) => e.innerText.trim()).join(" ").slice(0, 60);
    if (siteName === "boss") return report(item, false, "点击后没有进入聊天页" + (tip ? "：" + tip : ""));
    // 猎聘/脉脉点「聊一聊」即已发起沟通（网站通常会自动发默认招呼），只是没找到能发个性化招呼的输入框
    return report(item, true, "已发起沟通，个性化招呼未发出" + (tip ? "：" + tip : ""));
  }

  async function sendGreeting(item, box) {
    box = box || await waitFor(chatInput, 20000);
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
    const send = findByText(["发送"]) || $(".btn-send, .submit-content .btn, [class*='send-btn'], [class*='sendBtn']");
    if (send) await humanClick(send);
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
    const r = await api("/api/ext/applied", { key: item.key, ok, error, run: run.id });
    log(`${ok ? "✅ 已投递" : "⚠ 投递失败"}：${item.title}${error ? "（" + error + "）" : ""}`, false);
    if (r.data && r.data.stop) return finish(r.data.reason || "已停止");
    await sleep(rand(run.min_delay * 1000, run.max_delay * 1000));
    return doApply();
  }

  // ---------- 调度 ----------
  async function resume() {
    if (busy || !run) return;
    busy = true;
    try {
      if (page === "security") return setStatus("等待安全验证，有滑块请手动完成…");
      if (run.phase === "search") await doSearch();
      else await doApply();
    } catch (e) {
      log("❌ 出错：" + e.message);
    } finally {
      busy = false;
    }
  }

  async function poll() {
    const r = await api(`/api/ext/poll?site=${siteName}&claim=${run ? 0 : 1}&run=${run ? run.id : ""}`);
    ui.dot && ui.dot.classList.toggle("on", !r.offline);
    if (r.offline) { if (!run) setStatus("未连接本地助手（请双击 start.command）"); return; }
    if (run && r.data.stop) return finish(r.data.reason || "已停止");
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
    // Chrome 会给后台标签页限流，运行中被切到后台时提醒一次
    document.addEventListener("visibilitychange", () => {
      if (document.hidden && run && !run.hiddenWarned) { // 每个任务只提醒一次（页面跳转后脚本会重载）
        run.hiddenWarned = true;
        save();
        log("💡 页面在后台会变慢：可以把这个标签页拖出来单独成一个窗口，再去看 8765 网页");
      }
    });
    if (run) { setStatus("继续上次的任务…"); await sleep(rand(1500, 3000)); resume(); }
    poll();
    setInterval(poll, 3000);
  })();
})();
