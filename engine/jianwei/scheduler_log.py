"""调度运行日志：记录每次 daily_job 的结果到 SQLite。"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, UTC

from jianwei.config import app_db_path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS scheduler_runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    job         TEXT NOT NULL,
    status      TEXT NOT NULL,   -- ok | error
    detail      TEXT,            -- JSON
    ran_at      TEXT NOT NULL
);
"""


class SchedulerLog:
    def __init__(self, path: str | None = None):
        self.con = sqlite3.connect(str(path or app_db_path()))
        self.con.executescript(_SCHEMA)

    def close(self) -> None:
        self.con.close()

    def record(self, job: str, status: str, detail: dict | None = None) -> int:
        cur = self.con.execute(
            "INSERT INTO scheduler_runs (job, status, detail, ran_at) VALUES (?, ?, ?, ?)",
            (job, status, json.dumps(detail, ensure_ascii=False) if detail else None,
             datetime.now(UTC).isoformat()),
        )
        self.con.commit()
        return cur.lastrowid

    def recent(self, job: str | None = None, limit: int = 20) -> list[dict]:
        q = "SELECT id, job, status, detail, ran_at FROM scheduler_runs"
        args: tuple = ()
        if job:
            q += " WHERE job = ?"
            args = (job,)
        rows = self.con.execute(q + " ORDER BY id DESC LIMIT ?", (*args, limit)).fetchall()
        out = []
        for r in rows:
            d = dict(zip(["id", "job", "status", "detail", "ran_at"], r))
            if d["detail"]:
                d["detail"] = json.loads(d["detail"])
            out.append(d)
        return out
