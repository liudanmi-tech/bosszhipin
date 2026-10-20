"""用法：
    python -m jobhunter login boss          # 首次扫码登录
    python -m jobhunter run boss            # 搜索 → 打分 → 投递
    python -m jobhunter run boss --dry-run  # 只打分不投递
    python -m jobhunter chat boss           # 处理未读消息，AI 起草回复
"""
import argparse

from playwright.sync_api import sync_playwright

from .browser import open_context
from .config import load_config
from .llm import LLM
from .matcher import Matcher
from .platforms import PLATFORMS
from .storage import Storage


def confirm(prompt: str, safety: dict) -> bool:
    if not safety.get("require_confirm", True):
        return True
    return input(f"{prompt} [y/N] ").strip().lower() == "y"


def cmd_run(site, cfg, matcher, store, dry_run):
    prefs, safety = cfg["preferences"], cfg["safety"]
    threshold = cfg["llm"].get("match_threshold", 75)
    limit = safety.get("daily_apply_limit", 30)

    for keyword in prefs["keywords"]:
        for city in prefs.get("cities") or ["全国"]:
            for job in site.search(keyword, city):
                if store.applied_today(site.name) >= limit:
                    print(f"已达今日投递上限 {limit}，停止。")
                    return
                if store.seen(job):
                    continue
                site.fetch_detail(job)
                match = matcher.score(job)
                print(f"{match.score:>3}  {job.summary()}\n     {match.reasons}")

                if match.score < threshold or dry_run:
                    store.record(job, match, "skipped" if match.score < threshold else "seen")
                    continue
                print(f"     打招呼：{match.greeting}")
                if confirm("     投递该职位？", safety) and site.apply(job, match.greeting):
                    store.record(job, match, "applied")
                    print("     ✓ 已投递")
                else:
                    store.record(job, match, "skipped")


def cmd_chat(site, cfg, matcher):
    for convo in site.unread_conversations():
        draft = matcher.reply(convo["job"], convo["history"])
        print(f"\n{convo['job'].summary()}\n对方：{convo['history'][-1]['text']}\nAI 草稿：{draft['reply']}")
        if draft.get("needs_human"):
            print("⚠ 该问题需要你本人决定，已跳过自动发送。")
            continue
        if confirm("发送？", cfg["safety"]):
            site.send_message(convo["handle"], draft["reply"])


def main():
    parser = argparse.ArgumentParser(prog="jobhunter")
    parser.add_argument("command", choices=["login", "run", "chat"])
    parser.add_argument("platform", choices=list(PLATFORMS))
    parser.add_argument("--dry-run", action="store_true", help="只打分，不投递")
    parser.add_argument("--config", default=None)
    args = parser.parse_args()

    need_resume = args.command != "login"
    cfg = load_config(args.config, need_resume) if args.config else load_config(need_resume=need_resume)
    with sync_playwright() as pw:
        ctx = open_context(pw, args.platform)
        site = PLATFORMS[args.platform](ctx, cfg["safety"])
        site.ensure_login()
        if args.command == "login":
            print("登录态已保存。")
        else:
            matcher = Matcher(LLM(cfg["llm"]), cfg["resume"], cfg["preferences"])
            if args.command == "run":
                cmd_run(site, cfg, matcher, Storage(), args.dry_run)
            else:
                cmd_chat(site, cfg, matcher)
        ctx.close()


if __name__ == "__main__":
    main()
