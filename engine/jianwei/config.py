"""全局配置：数据目录解析与常量。"""

import os
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]


def data_dir() -> Path:
    """数据目录：JIANWEI_DATA_DIR 优先，开发期缺省 <项目根>/data。

    分发期由 Tauri 壳注入 JIANWEI_DATA_DIR（macOS 缺省 ~/.jianwei/data）。
    """
    d = Path(os.environ.get("JIANWEI_DATA_DIR", _REPO_ROOT / "data"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def market_db_path() -> Path:
    return data_dir() / "market.duckdb"


def app_db_path() -> Path:
    return data_dir() / "app.sqlite"
