# CLAUDE.md

AI 求职代理：Python + Playwright 驱动 BOSS直聘 / 猎聘 / 脉脉网页版，LLM 负责职位打分、话术生成、消息回复。

## 结构
- `jobhunter/platforms/` — 每个平台一个 `Platform` 子类（见 `base.py` 接口）。新增平台：实现子类并登记到 `platforms/__init__.py` 的 `PLATFORMS`。
- `jobhunter/matcher.py` — 所有 prompt 都在这里。
- `jobhunter/storage.py` — SQLite（`data/jobhunter.db`），按 `(platform, job_id)` 去重。

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
