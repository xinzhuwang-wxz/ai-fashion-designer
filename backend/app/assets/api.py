"""资产化 HTTP 端点：项目创建 + 上传→Cutout + 图片服务（ADR-0001）。

这是新链路的入口；旧 session 端点保留待后续切片迁移。AssetStore 经
set_asset_store 注入，便于测试替换为内存 adapter。
"""
from __future__ import annotations

import base64
import io
import os
import random
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse
from PIL import Image
from pydantic import BaseModel

from app.assets.models import AssetKind
from app.assets.store import AssetStore
from app.generation.backend import ComfyUIBackend, HttpComfyUIBackend
from app.generation.job import GenerationError, run_generation
from app.imaging import is_valid_image
from app.readiness import ReadinessGate
from app.services.comfyui_client import FABRIC_PROMPTS
from app.services.lineart import extract_lineart
from app.services.mask_utils import normalized_strokes_to_mask
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


class SelectVariationRequest(BaseModel):
    variation_id: str


@router.post("/projects/{project_id}/select-variation")
async def select_variation(project_id: str, req: SelectVariationRequest):
    """场景3：选中某变体并写入后端（后续线稿以它为来源）。"""
    store = _require_store()
    if store.get_project(project_id) is None:
        return JSONResponse({"error": "project not found"}, status_code=404)
    asset = store.get_asset(req.variation_id)
    if (
        asset is None
        or asset.project_id != project_id
        or asset.kind != AssetKind.VARIATION
    ):
        return JSONResponse({"error": "不是该项目的有效变体"}, status_code=400)
    store.select_variation(project_id, req.variation_id)
    return {"selected_variation": req.variation_id}


@router.post("/projects/{project_id}/lineart")
async def lineart(project_id: str):
    """场景3：从【选中变体】提取线稿（非 Cutout）。就绪门拦截未选中/跳步。"""
    store = _require_store()
    if store.get_project(project_id) is None:
        return JSONResponse({"error": "project not found"}, status_code=404)

    decision = ReadinessGate(store).can("lineart", project_id)
    if not decision.allowed:
        return JSONResponse({"error": decision.reason}, status_code=409)

    # 选中变体优先（探索流，保"选了变体生效"）；否则用最新抠图（自动流，上传即出线稿）
    source = store.get_selected_variation(project_id) or store.latest(
        project_id, AssetKind.CUTOUT
    )
    src = Path(IMAGES_DIR) / source.file_path
    if not src.exists():
        return JSONResponse({"error": "线稿源文件缺失"}, status_code=500)

    img = Image.open(src).convert("RGB")
    lineart_img = await run_in_threadpool(extract_lineart, img)
    buf = io.BytesIO()
    lineart_img.save(buf, format="PNG")
    data = buf.getvalue()
    if not is_valid_image(data):
        return JSONResponse({"error": "线稿提取返回无效结果"}, status_code=502)

    fname = _save_bytes("lineart", data)
    asset = store.add_asset(
        project_id,
        AssetKind.LINEART,
        parent_id=source.id,  # 父=选中变体 或 抠图
        file_path=fname,
    )
    return {"lineart": {"id": asset.id, "url": f"/api/images/{asset.file_path}"}}


class MaterialRequest(BaseModel):
    fabric: str = "silk"
    color: Optional[str] = ""
    pattern: Optional[str] = ""
    custom: Optional[str] = ""


def _build_material_prompt(req: "MaterialRequest") -> str:
    fabric_base = FABRIC_PROMPTS.get(req.fabric, req.fabric)
    color = (req.color or "").strip()
    pattern = (req.pattern or "").strip()
    custom = (req.custom or "").strip()
    parts = [color, f"{pattern} pattern" if pattern else "", fabric_base, custom]
    return ", ".join(p for p in parts if p)


@router.post("/projects/{project_id}/material")
async def material(project_id: str, req: MaterialRequest):
    """场景4 布料试穿：线稿 + 面料/颜色/图案/自定义 → ControlNet 渲染成衣（Material 资产）。"""
    store = _require_store()
    if store.get_project(project_id) is None:
        return JSONResponse({"error": "project not found"}, status_code=404)

    decision = ReadinessGate(store).can("material", project_id)
    if not decision.allowed:
        return JSONResponse({"error": decision.reason}, status_code=409)

    lineart = store.latest(project_id, AssetKind.LINEART)
    src = Path(IMAGES_DIR) / lineart.file_path
    if not src.exists():
        return JSONResponse({"error": "线稿文件缺失"}, status_code=500)

    b64 = base64.b64encode(src.read_bytes()).decode()
    prompt = _build_material_prompt(req)
    seed = 42
    inputs = {
        "uploads": [{"node": "2", "b64": b64, "name": "lineart_input.png"}],
        "set": [
            ["3", "text", prompt],
            ["5", "control_net_name", "control_v11p_sd15_lineart.pth"],
            ["8", "seed", seed],
        ],
    }
    try:
        asset = await run_in_threadpool(
            run_generation,
            _comfyui_backend(),
            store,
            _save_bytes,
            project_id=project_id,
            kind=AssetKind.MATERIAL,
            workflow_name="fabric_fill_controlnet.json",
            inputs=inputs,
            parent_id=lineart.id,
            seed=seed,
            params={
                "fabric": req.fabric,
                "color": req.color,
                "pattern": req.pattern,
                "custom": req.custom,
                "prompt": prompt,
            },
        )
    except GenerationError as e:
        return JSONResponse({"error": f"布料渲染失败: {e}"}, status_code=502)

    return {"material": {"id": asset.id, "url": f"/api/images/{asset.file_path}"}}


@router.post("/projects/{project_id}/lineart-image")
async def lineart_image(project_id: str, file: UploadFile = File(...)):
    """草图优先入口（#8）：直接上传/手绘的线稿图 → Lineart 资产（无需先传照片）。"""
    store = _require_store()
    if store.get_project(project_id) is None:
        return JSONResponse({"error": "project not found"}, status_code=404)
    raw = await file.read()
    if not is_valid_image(raw):
        return JSONResponse({"error": "不是有效线稿图片"}, status_code=422)
    fname = _save_bytes("lineart", raw)
    asset = store.add_asset(
        project_id, AssetKind.LINEART, parent_id=None, file_path=fname
    )
    return {"lineart": {"id": asset.id, "url": f"/api/images/{asset.file_path}"}}


class EditRequest(BaseModel):
    strokes: list[dict] = []  # 图像归一化坐标 [0,1]
    prompt: Optional[str] = ""
    brush_frac: float = 0.06
    strength: float = 0.6


@router.post("/projects/{project_id}/edit")
async def edit(project_id: str, req: EditRequest):
    """场景5 局部编辑：笔触(归一化坐标)→mask→masked inpaint（只改 mask 区域，mask 外稳定）。"""
    store = _require_store()
    if store.get_project(project_id) is None:
        return JSONResponse({"error": "project not found"}, status_code=404)
    if not ReadinessGate(store).can("edit", project_id).allowed:
        return JSONResponse({"error": "需要先有成衣渲染"}, status_code=409)
    if not req.strokes:
        return JSONResponse({"error": "没有笔触"}, status_code=400)

    source = None
    for k in (AssetKind.EDIT, AssetKind.MATERIAL, AssetKind.VARIATION):
        source = store.latest(project_id, k)
        if source:
            break
    src = Path(IMAGES_DIR) / source.file_path
    if not src.exists():
        return JSONResponse({"error": "源图文件缺失"}, status_code=500)

    img = Image.open(src).convert("RGB")
    w, h = img.size
    mask = normalized_strokes_to_mask(req.strokes, w, h, req.brush_frac)
    mbuf = io.BytesIO()
    Image.fromarray(mask).save(mbuf, format="PNG")

    seed = random.randint(1, 1_000_000)
    inputs = {
        "uploads": [
            {"node": "2", "b64": base64.b64encode(src.read_bytes()).decode(), "name": "edit_src.png"},
            {"node": "3", "b64": base64.b64encode(mbuf.getvalue()).decode(), "name": "edit_mask.png"},
        ],
        "set": [
            ["7", "text", req.prompt or "fashion design, refined detail"],
            ["9", "seed", seed],
            ["9", "denoise", req.strength],
        ],
    }
    try:
        asset = await run_in_threadpool(
            run_generation,
            _comfyui_backend(),
            store,
            _save_bytes,
            project_id=project_id,
            kind=AssetKind.EDIT,
            workflow_name="edit_inpaint.json",
            inputs=inputs,
            parent_id=source.id,
            seed=seed,
            params={"prompt": req.prompt, "strength": req.strength, "n_strokes": len(req.strokes)},
        )
    except GenerationError as e:
        return JSONResponse({"error": f"局部重绘失败: {e}"}, status_code=502)
    return {"edit": {"id": asset.id, "url": f"/api/images/{asset.file_path}"}}


@router.get("/projects/{project_id}/export")
async def export_project(project_id: str):
    """场景6：导出/恢复用——项目各阶段最新资产 + 生成参数（下载导出 & 重开恢复共用）。"""
    store = _require_store()
    if store.get_project(project_id) is None:
        return JSONResponse({"error": "project not found"}, status_code=404)
    assets = {}
    for kind in AssetKind:
        a = store.latest(project_id, kind)
        if a:
            assets[kind.value] = {
                "id": a.id,
                "url": f"/api/images/{a.file_path}" if a.file_path else None,
                "params": a.params,
                "seed": a.seed,
            }
    return {"project_id": project_id, "assets": assets}


@router.get("/images/{filename}")
async def serve_image(filename: str):
    path = Path(IMAGES_DIR) / filename
    if not path.exists():
        return JSONResponse({"error": "not found"}, status_code=404)
    return FileResponse(path, media_type="image/png")
