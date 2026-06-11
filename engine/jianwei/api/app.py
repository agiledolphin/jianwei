"""FastAPI 本地服务骨架（阶段二接入 Tauri sidecar 时扩充路由）。"""

from __future__ import annotations

from fastapi import FastAPI

from jianwei import __version__

app = FastAPI(title="Jianwei Engine", version=__version__)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "version": __version__}
