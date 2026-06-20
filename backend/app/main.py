"""
AI 服装设计工具 — FastAPI 后端入口
M4 适配版：抠图/线稿本地跑，变体/填充走 ComfyUI MPS 或云端 GPU
"""
import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response
from app.api import router as api_router, set_state_machine
from app.services.state_machine import DesignStateMachine
from app.assets.api import router as assets_router, set_asset_store
from app.assets.store import SqliteAssetStore

app = FastAPI(title="AI Fashion Designer", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    # 本地开发：放行任意源（含 localhost/127.0.0.1/LAN IP 任意端口）。
    # 无凭据需求，故不与 allow_credentials 冲突。生产环境收敛见 #5。
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# WebSocket CORS: ensure upgrade handshake passes CORS
class WebSocketCORSMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # 对 WebSocket upgrade 请求，Origin 不在 allow_origins 时手动放行
        if request.headers.get("upgrade", "").lower() == "websocket":
            origin = request.headers.get("origin", "")
            if origin.startswith("http://localhost:3000"):
                # 放行，让 WebSocket 端点自行处理
                pass
        response = await call_next(request)
        return response


app.add_middleware(WebSocketCORSMiddleware)

# 全局状态机（旧 session 链路，待后续切片迁移）
state_machine = DesignStateMachine()
set_state_machine(state_machine)

# 资产谱系存储（ADR-0001）：SQLite 持久化，重启可恢复
_db_path = os.environ.get(
    "AIFD_DB", str(Path(__file__).resolve().parent.parent / "data" / "assets.db")
)
Path(_db_path).parent.mkdir(parents=True, exist_ok=True)
set_asset_store(SqliteAssetStore(_db_path))

app.include_router(api_router, prefix="/api")
app.include_router(assets_router, prefix="/api")


@app.get("/health")
async def health():
    return {"status": "ok", "gpu": "mps" if _has_mps() else "cpu"}


def _has_mps() -> bool:
    try:
        import torch
        return torch.backends.mps.is_available()
    except Exception:
        return False
