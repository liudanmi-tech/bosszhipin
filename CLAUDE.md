# CLAUDE.md

AI 求职代理：Python + Playwright 驱动 BOSS直聘 / 猎聘 / 脉脉网页版，LLM 负责职位打分、话术生成、消息回复。

## 结构
- `jobhunter/platforms/` — 每个平台一个 `Platform` 子类（见 `base.py` 接口）。新增平台：实现子类并登记到 `platforms/__init__.py` 的 `PLATFORMS`。
- `jobhunter/matcher.py` — 所有 prompt 都在这里。
- `jobhunter/storage.py` — SQLite（`data/jobhunter.db`），按 `(platform, job_id)` 去重。
- `extension/` — Chrome 插件（MV3），一个插件支持 BOSS直聘 / 猎聘 / 脉脉，网站差异都在 `content.js` 的 `SITES` 适配里（服务端对应 `jobhunter/web/sites.py`）。在用户自己的 Chrome 里运行：搜索 → 逐个查看岗位 → `POST /api/ext/job` 打分 → 投递队列 → 详情页点沟通按钮 → 聊天框发招呼。猎聘详情页是服务端渲染，直接 fetch 取描述；脉脉为测试版，用通用规则识别。页面跳转会重载脚本，进度按标签页存在 `chrome.storage.local`。`background.js` 代为请求 `127.0.0.1:8765`（内容脚本直连会被跨域拦截）。
- BOSS 不要再用 Playwright/CDP 驱动：BOSS 的反调试脚本会识别并把页面跳到 about:blank。Playwright 版 `platforms/boss.py` 仅保留给命令行。

## 规则
- 永远不要提交 `config.yaml`、`resume/`、`browser_profiles/`、`data/` —— 里面是简历和登录 Cookie。
- 不要删除或绕过 `safety` 的限速、每日上限、人工确认，这些是防封号的底线。
- 页面选择器是猜测/易变的，改动后用 `python -m jobhunter run <platform> --dry-run` 实测。
- 不要让 AI 代答薪资底线、到岗时间等需要本人决定的问题（`needs_human`）。

## 运行
```bash
pip install -r requirements.txt && playwright install chromium
python -m jobhunter login boss
python -m jobhunter run boss --dry-run
```
