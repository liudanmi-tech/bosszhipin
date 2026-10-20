import sqlite3
from datetime import date
from pathlib import Path

from .config import ROOT
from .models import Job, MatchResult

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    platform TEXT, job_id TEXT, title TEXT, company TEXT, url TEXT,
    salary TEXT, city TEXT, score INTEGER, reasons TEXT,
    status TEXT DEFAULT 'seen',          -- seen / skipped / applied
    applied_on TEXT,
    PRIMARY KEY (platform, job_id)
);
"""


class Storage:
    """本地 SQLite：职位去重、打分记录、每日投递计数。"""

    def __init__(self, path: Path = ROOT / "data" / "jobhunter.db"):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.executescript(SCHEMA)

    def seen(self, job: Job) -> bool:
        row = self.db.execute(
            "SELECT 1 FROM jobs WHERE platform=? AND job_id=?", (job.platform, job.job_id)
        ).fetchone()
        return row is not None

    def record(self, job: Job, match: MatchResult, status: str) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO jobs VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (job.platform, job.job_id, job.title, job.company, job.url, job.salary, job.city,
             match.score, match.reasons, status,
             date.today().isoformat() if status == "applied" else None),
        )
        self.db.commit()

    def applied_today(self, platform: str) -> int:
        return self.db.execute(
            "SELECT COUNT(*) FROM jobs WHERE platform=? AND status='applied' AND applied_on=?",
            (platform, date.today().isoformat()),
        ).fetchone()[0]
