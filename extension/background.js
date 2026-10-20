// 内容脚本运行在 zhipin.com 页面里，直接请求本地服务会被跨域 / 私有网络访问规则拦截，
// 所以统一由后台代为请求 http://127.0.0.1:8765。
const SERVER = "http://127.0.0.1:8765";

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.type === "tabId") {
    sendResponse(sender.tab && sender.tab.id);
    return false;
  }
  if (msg.type === "api") {
    fetch(SERVER + msg.path, {
      method: msg.body ? "POST" : "GET",
      headers: { "Content-Type": "application/json" },
      body: msg.body ? JSON.stringify(msg.body) : undefined,
    })
      .then(async (r) => sendResponse({ ok: r.ok, status: r.status, data: await r.json().catch(() => ({})) }))
      .catch((e) => sendResponse({ ok: false, offline: true, data: { detail: String(e) } }));
    return true; // 异步响应
  }
  return false;
});
