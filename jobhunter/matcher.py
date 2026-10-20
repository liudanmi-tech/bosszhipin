import json

from .llm import LLM
from .models import Job, MatchResult

MATCH_SYSTEM = """你是一位资深求职顾问。根据候选人的简历和求职偏好，评估一个职位的匹配度。
评分 0-100：硬性条件（城市、薪资、避开项）不满足直接低于 40。
同时写一段 80 字以内的打招呼话术：真诚、具体、点出简历中与该职位最相关的 1-2 个亮点，不要套话。
输出 JSON：{"score": int, "reasons": str, "greeting": str}"""

REPLY_SYSTEM = """你代表候选人与招聘方/猎头沟通。语气专业礼貌、简洁。
不要编造简历中没有的经历；涉及薪资底线、到岗时间、是否接受面试等需要候选人本人决定的问题，
回复中给出得体的过渡，并在 JSON 中标记 needs_human=true。
输出 JSON：{"reply": str, "needs_human": bool}"""


class Matcher:
    def __init__(self, llm: LLM, resume: str, preferences: dict):
        self.llm = llm
        self.resume = resume
        self.preferences = preferences

    def score(self, job: Job) -> MatchResult:
        user = (
            f"# 简历\n{self.resume}\n\n"
            f"# 求职偏好\n{json.dumps(self.preferences, ensure_ascii=False)}\n\n"
            f"# 职位\n标题：{job.title}\n公司：{job.company}\n薪资：{job.salary}\n"
            f"城市：{job.city}\n标签：{', '.join(job.tags)}\n描述：\n{job.description}"
        )
        data = self.llm.chat_json(MATCH_SYSTEM, user)
        return MatchResult(int(data["score"]), data.get("reasons", ""), data.get("greeting", ""))

    def reply(self, job: Job, history: list[dict]) -> dict:
        convo = "\n".join(f"{m['role']}: {m['text']}" for m in history)
        user = f"# 简历\n{self.resume}\n\n# 职位\n{job.summary()}\n\n# 对话记录\n{convo}\n\n请写下一条回复。"
        return self.llm.chat_json(REPLY_SYSTEM, user)
