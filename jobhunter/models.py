from dataclasses import dataclass, field


@dataclass
class Job:
    platform: str
    job_id: str
    title: str
    company: str
    url: str
    salary: str = ""
    city: str = ""
    description: str = ""
    recruiter: str = ""
    tags: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return f"[{self.platform}] {self.title} @ {self.company} | {self.salary} | {self.city}"


@dataclass
class MatchResult:
    score: int
    reasons: str
    greeting: str  # 大模型生成的打招呼话术
