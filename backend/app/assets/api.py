"""资产化 HTTP 端点：项目创建 + 上传→Cutout + 图片服务（ADR-0001）。

这是新链路的入口；旧 session 端点保留待后续切片迁移。AssetStore 经
set_asset_store 注入，便于测试替换为内存 adapter。
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from app.assets.models import AssetKind
from app.assets.store import AssetStore
from app.imaging import is_valid_image
from app.services.remove_bg import remove_background

router = APIRouter()

IMAGES_DIR = os.environ.get(
    "AIFD_IMAGES_DIR",
    str(Path(__file__).resolve().parent.parent.parent / "static" / "images"),
)

_store: Optional[AssetStore] = None


def set_asset_store(store: AssetStore) -> None:
    global _store
    _store = store


def _require_store() -> AssetStore:
    if _store is None:
        raise RuntimeError("AssetStore 未初始化（调用 set_asset_store）")
    return _store


def _save_bytes(prefix: str, data: bytes) -> str:
    Path(IMAGES_DIR).mkdir(parents=True, exist_ok=True)
    fname = f"{prefix}_{uuid.uuid4().hex[:12]}.png"
    (Path(IMAGES_DIR) / fname).write_bytes(data)
    return fname


@router.post("/projects")
async def create_project():
    project = _require_store().create_project()
    return {"project_id": project.id}


@router.post("/projects/{project_id}/upload")
async def upload(project_id: str, file: UploadFile = File(...)):
    store = _require_store()
    if store.get_project(project_id) is None:
        return JSONResponse({"error": "project not found"}, status_code=404)

    raw = await file.read()
    if not is_valid_image(raw):
        return JSONResponse(
            {"error": "上传不是有效图片"}, status_code=422
        )
    upload_fname = _save_bytes("upload", raw)
    upload_asset = store.add_asset(
        project_id, AssetKind.UPLOAD, file_path=upload_fname
    )

    cutout_bytes = remove_background(raw)
    if not is_valid_image(cutout_bytes):
        return JSONResponse(
            {"error": "抠图返回空/无效结果，未创建 Cutout"}, status_code=502
        )
    cutout_fname = _save_bytes("cutout", cutout_bytes)
    cutout = store.add_asset(
        project_id,
        AssetKind.CUTOUT,
        parent_id=upload_asset.id,
        file_path=cutout_fname,
    )

    return {
        "project_id": project_id,
        "cutout": {"id": cutout.id, "url": f"/api/images/{cutout.file_path}"},
    }


@router.get("/images/{filename}")
async def serve_image(filename: str):
    path = Path(IMAGES_DIR) / filename
    if not path.exists():
        return JSONResponse({"error": "not found"}, status_code=404)
    return FileResponse(path, media_type="image/png")
