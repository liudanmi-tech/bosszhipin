# bosszhipin · AI 求职代理

让 AI 用你自己的浏览器登录态，自动浏览 **BOSS直聘 / 猎聘 / 脉脉**：

1. 按关键词和城市搜索职位
2. 大模型对照你的简历和偏好给职位打分（0–100）
3. 高分职位自动生成个性化打招呼话术并投递
4. 读取招聘方/猎头的未读消息，AI 起草回复（敏感问题交给你本人）

## 平台进度

| 平台 | 登录 | 搜索 | 投递/打招呼 | 消息回复 |
|------|------|------|-------------|----------|
| BOSS直聘 | ✅ | ✅ | ✅ | TODO |
| 猎聘 | TODO | TODO | TODO | TODO |
| 脉脉 | TODO | TODO | TODO | TODO |

> 网页选择器会随网站改版失效，首次使用请先用 `--dry-run` 实测。

## 快速开始

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium

cp config.example.yaml config.yaml     # 填写求职偏好
mkdir resume && vim resume/resume.md   # 放入简历纯文本
export OPENROUTER_API_KEY=sk-or-...

python -m jobhunter login boss          # 弹出浏览器，手机扫码登录（只需一次）
python -m jobhunter run boss --dry-run  # 只打分，先看看筛选效果
python -m jobhunter run boss            # 正式投递（默认每次需要回车确认）
```

## 目录结构

```
jobhunter/
├── __main__.py        CLI：login / run / chat
├── config.py          读取 config.yaml + 简历
├── browser.py         Playwright 持久化登录态、随机延时
├── llm.py             OpenAI 兼容接口（默认 OpenRouter）
├── matcher.py         职位打分 + 话术生成 + 消息回复
├── storage.py         SQLite：去重、投递记录、每日计数
└── platforms/
    ├── base.py        平台适配器接口
    ├── boss.py        BOSS直聘
    ├── liepin.py      猎聘
    └── maimai.py      脉脉
```

## ⚠️ 风险提示

这三个平台的用户协议都禁止自动化操作，而且风控严格。项目默认的设计是尽量降低封号风险：

- 用你**本人真实登录**的浏览器会话，不逆向接口
- 每次操作之间随机延时 4–12 秒，每个平台每天投递有上限（`daily_apply_limit`）
- `require_confirm: true`：每次投递、发消息之前都在终端由你确认
- 薪资、到岗时间这类需要你本人决定的问题，AI 不会代答

即便这样，账号仍有被限制的可能，后果由你自己承担。
