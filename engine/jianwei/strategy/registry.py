"""策略注册表与回测运行记录（SQLite，事务型小数据）。"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, UTC

from jianwei.config import app_db_path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS strategies (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    params      TEXT NOT NULL,          -- JSON
    created_at  TEXT NOT NULL,
    UNIQUE(name, params)
);
CREATE TABLE IF NOT EXISTS backtest_runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_id INTEGER REFERENCES strategies(id),
    start       TEXT, "end" TEXT,
    metrics     TEXT,                   -- JSON
    created_at  TEXT NOT NULL
);
"""


class Registry:
    def __init__(self, path: str | None = None):
        self.con = sqlite3.connect(str(path or app_db_path()))
        self.con.executescript(_SCHEMA)

    def close(self) -> None:
        self.con.close()

    def register_strategy(self, name: str, params: dict) -> int:
        """同名同参幂等，返回 strategy_id。"""
        pj = json.dumps(params, sort_keys=True, ensure_ascii=False)
        cur = self.con.execute(
            "INSERT OR IGNORE INTO strategies (name, params, created_at) VALUES (?, ?, ?)",
            (name, pj, datetime.now(UTC).isoformat()),
        )
        self.con.commit()
        if cur.lastrowid:
            return cur.lastrowid
        row = self.con.execute(
            "SELECT id FROM strategies WHERE name = ? AND params = ?", (name, pj)
        ).fetchone()
        return row[0]

    def record_backtest(self, strategy_id: int, start: str, end: str, metrics: dict) -> int:
        cur = self.con.execute(
            'INSERT INTO backtest_runs (strategy_id, start, "end", metrics, created_at)'
            " VALUES (?, ?, ?, ?, ?)",
            (
                strategy_id,
                start,
                end,
                json.dumps(metrics, ensure_ascii=False),
                datetime.now(UTC).isoformat(),
            ),
        )
        self.con.commit()
        return cur.lastrowid

    def runs(self, strategy_id: int | None = None) -> list[dict]:
        q = (
            "SELECT r.id, s.name, s.params, r.start, r.\"end\", r.metrics, r.created_at "
            "FROM backtest_runs r JOIN strategies s ON s.id = r.strategy_id"
        )
        args: tuple = ()
        if strategy_id is not None:
            q += " WHERE s.id = ?"
            args = (strategy_id,)
        rows = self.con.execute(q + " ORDER BY r.id DESC", args).fetchall()
        keys = ["run_id", "name", "params", "start", "end", "metrics", "created_at"]
        return [dict(zip(keys, r)) for r in rows]
