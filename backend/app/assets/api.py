"""资产化 HTTP 端点：项目创建 + 上传→Cutout + 图片服务（ADR-0001）。

这是新链路的入口；旧 session 端点保留待后续切片迁移。AssetStore 经
set_asset_store 注入，便于测试替换为内存 adapter。
"""
from __future__ import annotations

import base64
import os
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from app.assets.models import AssetKind
from app.assets.store import AssetStore
from app.generation.backend import ComfyUIBackend, HttpComfyUIBackend
from app.generation.job import GenerationError, run_generation
from app.imaging import is_valid_image
from app.services.remove_bg import remove_background

router = APIRouter()

IMAGES_DIR = os.environ.get(
    "AIFD_IMAGES_DIR",
    str(Path(__file__).resolve().parent.parent.parent / "static" / "images"),
)

_store: Optional[AssetStore] = None
_backend: Optional[ComfyUIBackend] = None


def set_asset_store(store: AssetStore) -> None:
    global _store
    _store = store


def set_comfyui_backend(backend: ComfyUIBackend) -> None:
    global _backend
    _backend = backend


def _require_store() -> AssetStore:
    if _store is None:
        raise RuntimeError("AssetStore 未初始化（调用 set_asset_store）")
    return _store


def _comfyui_backend() -> ComfyUIBackend:
    global _backend
    if _backend is None:
        _backend = HttpComfyUIBackend()
    return _backend


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


class VariationRequest(BaseModel):
    prompt: Optional[str] = "fashion design, professional photography, elegant"
    num_variants: int = 3
    strength: float = 0.65


@router.post("/projects/{project_id}/variations")
async def variations(project_id: str, req: VariationRequest):
    """场景2 方案发散：基于最新 Cutout 生成 N 个 Variation 资产（经 GenerationJob 校验）。"""
    store = _require_store()
    if store.get_project(project_id) is None:
        return JSONResponse({"error": "project not found"}, status_code=404)

    cutout = store.latest(project_id, AssetKind.CUTOUT)
    if cutout is None or not cutout.file_path:
        return JSONResponse({"error": "无 Cutout，先上传参考图"}, status_code=400)
    src = Path(IMAGES_DIR) / cutout.file_path
    if not src.exists():
        return JSONResponse({"error": "Cutout 文件缺失"}, status_code=500)

    b64 = base64.b64encode(src.read_bytes()).decode()
    backend = _comfyui_backend()
    prompt = req.prompt or "fashion design, professional photography, elegant"

    results = []
    try:
        for i in range(max(1, req.num_variants)):
            seed = 42 + i * 1000
            inputs = {
                "uploads": [
                    {"node": "2", "b64": b64, "name": "variation_input.png"}
                ],
                "set": [
                    ["3", "text", prompt],
                    ["6", "seed", seed],
                    ["6", "denoise", req.strength],
                ],
            }
            asset = await run_in_threadpool(
                run_generation,
                backend,
                store,
                _save_bytes,
                project_id=project_id,
                kind=AssetKind.VARIATION,
                workflow_name="variation.json",
                inputs=inputs,
                parent_id=cutout.id,
                seed=seed,
                params={"prompt": prompt, "strength": req.strength},
            )
            results.append({"id": asset.id, "url": f"/api/images/{asset.file_path}"})
    except GenerationError as e:
        return JSONResponse({"error": f"变体生成失败: {e}"}, status_code=502)

    return {"project_id": project_id, "variations": results}


@router.get("/images/{filename}")
async def serve_image(filename: str):
    path = Path(IMAGES_DIR) / filename
    if not path.exists():
        return JSONResponse({"error": "not found"}, status_code=404)
    return FileResponse(path, media_type="image/png")
