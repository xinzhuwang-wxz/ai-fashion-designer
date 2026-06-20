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

from fastapi import APIRouter, File, Form, UploadFile
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
from app.services.mask_utils import (
    composite_subject_lock,
    composite_subject_lock_bytes,
    invert_lineart,
    normalized_strokes_to_mask,
)
from app.services.remove_bg import remove_background
from app.services.vectorize import vectorize_lineart

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


# 变体多样性来自风格扰动（+seed），而非牺牲质量的高 denoise（#23）
_VARIATION_STYLES = [
    "flowing silk, soft elegant drape",
    "structured tailoring, crisp clean lines",
    "romantic chiffon, ethereal layered",
    "modern minimalist, sleek",
    "luxurious satin, rich sheen",
    "avant-garde, bold dramatic silhouette",
]


async def _ensure_lineart(store: AssetStore, project_id: str):
    """取最新 Lineart；无则从最新 Cutout 提取并入库。供变体/试穿共用底图。"""
    lineart = store.latest(project_id, AssetKind.LINEART)
    if lineart is not None and lineart.file_path:
        return lineart
    cutout = store.latest(project_id, AssetKind.CUTOUT)
    if cutout is None or not cutout.file_path:
        return None
    csrc = Path(IMAGES_DIR) / cutout.file_path
    if not csrc.exists():
        return None
    img = Image.open(csrc).convert("RGB")
    la_img = await run_in_threadpool(extract_lineart, img)
    buf = io.BytesIO()
    la_img.save(buf, format="PNG")
    data = buf.getvalue()
    if not is_valid_image(data):
        return None
    fname = _save_bytes("lineart", data)
    return store.add_asset(
        project_id, AssetKind.LINEART, parent_id=cutout.id, file_path=fname
    )


@router.post("/projects/{project_id}/variations")
async def variations(project_id: str, req: VariationRequest):
    """场景2 方案发散：基于 Lineart + ControlNet 渲染 N 个高质量 Variation（#23）。

    旧版 img2img-on-Cutout 出图扁平低质；改走与 material 同款线稿+ControlNet 路线，
    多样性靠 seed + 风格提示扰动，每个变体都是棚拍级成衣（轮廓跟线稿，不会漂移）。
    """
    store = _require_store()
    if store.get_project(project_id) is None:
        return JSONResponse({"error": "project not found"}, status_code=404)

    lineart = await _ensure_lineart(store, project_id)
    if lineart is None:
        return JSONResponse({"error": "无 Cutout/Lineart，先上传参考图"}, status_code=400)
    src = Path(IMAGES_DIR) / lineart.file_path
    if not src.exists():
        return JSONResponse({"error": "Lineart 文件缺失"}, status_code=500)

    b64 = base64.b64encode(invert_lineart(src.read_bytes())).decode()  # 黑线白底→白线黑底
    backend = _comfyui_backend()
    base_prompt = req.prompt or "fashion design, professional studio photography, elegant"

    results = []
    try:
        for i in range(max(1, req.num_variants)):
            seed = 42 + i * 1000
            style = _VARIATION_STYLES[i % len(_VARIATION_STYLES)]
            prompt = GARMENT_POSITIVE_TEMPLATE.format(desc=f"{base_prompt}, {style}")
            inputs = {
                "uploads": [{"node": "2", "b64": b64, "name": "lineart_input.png"}],
                "set": [
                    ["3", "text", prompt],
                    ["4", "text", GARMENT_NEGATIVE],
                    ["5", "control_net_name", QUALITY_CONTROLNET],
                    ["6", "strength", 0.7],
                    ["8", "seed", seed],
                ],
            }
            asset = await run_in_threadpool(
                run_generation,
                backend,
                store,
                _save_bytes,
                project_id=project_id,
                kind=AssetKind.VARIATION,
                workflow_name=REALTIME_WORKFLOW,  # 变体走 LCM 快路径（×3 不至于太慢）
                inputs=inputs,
                parent_id=lineart.id,
                seed=seed,
                params={"prompt": prompt, "style": style},
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


@router.post("/projects/{project_id}/lineart-vector")
async def lineart_vector(project_id: str):
    """把最新 Lineart 矢量化为折线笔画 → 前端用画板原生 draw 笔画画出来（与用户手绘统一）。"""
    store = _require_store()
    if store.get_project(project_id) is None:
        return JSONResponse({"error": "project not found"}, status_code=404)
    la = store.latest(project_id, AssetKind.LINEART)
    if la is None or not la.file_path:
        return JSONResponse({"error": "无线稿"}, status_code=400)
    src = Path(IMAGES_DIR) / la.file_path
    if not src.exists():
        return JSONResponse({"error": "线稿文件缺失"}, status_code=500)
    strokes = await run_in_threadpool(vectorize_lineart, src.read_bytes())
    return {"strokes": strokes, "count": len(strokes)}


class MaterialRequest(BaseModel):
    fabric: str = "silk"
    color: Optional[str] = ""
    pattern: Optional[str] = ""
    custom: Optional[str] = ""


# 成衣导向模板：把"面料描述"包成"白底棚拍的一件完整成衣"，对齐竞品效果（而非面料特写）。
# 通用"时尚单品"模板：不锁死成衣——衣/包/鞋/配饰由【画的形状 + 自定义描述】决定。
# 仅保证"白底棚拍、一件、立体、细节"。具体品类靠 desc（含 custom，如"手袋/高跟鞋"）。
GARMENT_POSITIVE_TEMPLATE = (
    "{desc}, a single fashion item shown in full, centered, "
    "isolated on plain white studio background, "
    "professional product photography, realistic materials and texture, "
    "soft even studio lighting, three-dimensional, gentle soft shadows, "
    "highly detailed, sharp focus"
)
# 强负向：推离"面料特写/平面剪影"，逼出"白底立体单品"。
GARMENT_NEGATIVE = (
    "close-up, macro, extreme close-up, cropped, zoomed in, "
    "fabric swatch, texture sample, full frame fabric, seamless pattern, tiled, "
    "multiple items, collage, human face, mannequin head, "
    "busy background, cluttered, flat, 2d, paper cutout, sketch, line drawing, diagram, "
    "ugly, blurry, low quality, distorted, watermark, text, signature"
)


# 渲染基座阶梯（ADR-0006 / #25）：SDXL 高质量用于 material/variation/finalize；
# 实时 render-live 仍走 SD1.5+LCM 保速度。env AIFD_SDXL=1 切到 SDXL。
_SDXL = os.environ.get("AIFD_SDXL", "1") == "1"  # 默认全线 SDXL；AIFD_SDXL=0 才降级回 SD1.5
QUALITY_WORKFLOW = "sdxl_controlnet.json" if _SDXL else "fabric_fill_controlnet.json"
QUALITY_CONTROLNET = (
    "controlnet-union-sdxl-promax.safetensors"
    if _SDXL
    else "control_v11p_sd15_lineart.pth"
)
# 实时路径（render-live 全渲 / render-local 局部）：SDXL-LCM 或 SD1.5-LCM
REALTIME_WORKFLOW = "sdxl_lcm_controlnet.json" if _SDXL else "lcm_controlnet.json"
REALTIME_INPAINT_WORKFLOW = (
    "sdxl_lcm_controlnet_inpaint.json" if _SDXL else "lcm_controlnet_inpaint.json"
)
# 实时用 union（细线也抓，遵循强）；scribble 忽略细线导致心形/细纹被冲掉，已换回。
REALTIME_CONTROLNET = (
    "controlnet-union-sdxl-promax.safetensors" if _SDXL else "control_v11p_sd15_lineart.pth"
)


def _build_material_prompt(req: "MaterialRequest") -> str:
    fabric_base = FABRIC_PROMPTS.get(req.fabric, req.fabric)
    color = (req.color or "").strip()
    pattern = (req.pattern or "").strip()
    custom = (req.custom or "").strip()
    desc = ", ".join(
        p for p in [color, f"{pattern} pattern" if pattern else "", fabric_base, custom] if p
    )
    return GARMENT_POSITIVE_TEMPLATE.format(desc=desc)


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

    b64 = base64.b64encode(invert_lineart(src.read_bytes())).decode()  # 黑线白底→白线黑底
    prompt = _build_material_prompt(req)
    seed = 42
    inputs = {
        "uploads": [{"node": "2", "b64": b64, "name": "lineart_input.png"}],
        "set": [
            ["3", "text", prompt],
            ["4", "text", GARMENT_NEGATIVE],
            ["5", "control_net_name", QUALITY_CONTROLNET],
            ["6", "strength", 0.7],  # 降 ControlNet 强度 → 3D 垂坠（#对齐）
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
            workflow_name=QUALITY_WORKFLOW,
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
    strength: float = 0.8  # 提可见度（#24）；主体锁定合成保证 mask 外不动，可放心提强度


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
            post_process=lambda b: composite_subject_lock(img, b, mask),  # 主体锁定 #24
        )
    except GenerationError as e:
        return JSONResponse({"error": f"局部重绘失败: {e}"}, status_code=502)
    return {"edit": {"id": asset.id, "url": f"/api/images/{asset.file_path}"}}


class EditLiveRequest(BaseModel):
    strokes: list[dict] = []  # 图像归一化坐标 [0,1]
    prompt: Optional[str] = ""
    brush_frac: float = 0.06
    denoise: float = 0.9
    seed: Optional[int] = None


def _edit_source(store: AssetStore, project_id: str):
    """实时/编辑共用：取右画布当前成衣作为重绘底图（Edit>Material>Variation）。"""
    for k in (AssetKind.EDIT, AssetKind.MATERIAL, AssetKind.VARIATION):
        a = store.latest(project_id, k)
        if a:
            return a
    return None


def _auto_intent(sketch_png: bytes, mask_png: bytes) -> str:
    """裁出改动区草图 → CLIP 自动识别意图语义（#27，一个笔刷无感）。"""
    from app.services.sketch_intent import classify_intent

    try:
        m = Image.open(io.BytesIO(mask_png)).convert("L")
        bbox = m.getbbox()
        if bbox is None:
            return ""
        sk = Image.open(io.BytesIO(sketch_png)).convert("RGB")
        W, H = sk.size
        x0, y0, x1, y1 = bbox
        px, py = int((x1 - x0) * 0.8), int((y1 - y0) * 0.8)  # 扩 80% 语境
        crop = sk.crop((max(0, x0 - px), max(0, y0 - py), min(W, x1 + px), min(H, y1 + py)))
        buf = io.BytesIO()
        crop.save(buf, format="PNG")
        return classify_intent(buf.getvalue())
    except Exception:  # noqa: BLE001 自动识别失败不阻塞渲染
        return ""


@router.post("/projects/{project_id}/edit-live")
async def edit_live(project_id: str, req: EditLiveRequest):
    """场景5+ 实时局部重绘（#22）：笔触→mask→LCM 4 步快路径单帧。

    预览帧【不入谱系】（避免连续涂抹刷爆资产树）；满意后前端调 /edit 持久化。
    source 按资产 id 缓存（连续涂抹时成衣不变，只重传 mask），50ms 快轮询。
    """
    store = _require_store()
    if store.get_project(project_id) is None:
        return JSONResponse({"error": "project not found"}, status_code=404)
    if not ReadinessGate(store).can("edit", project_id).allowed:
        return JSONResponse({"error": "需要先有成衣渲染"}, status_code=409)
    if not req.strokes:
        return JSONResponse({"error": "没有笔触"}, status_code=400)

    source = _edit_source(store, project_id)
    if source is None or not source.file_path:
        return JSONResponse({"error": "无可重绘的成衣"}, status_code=409)
    src = Path(IMAGES_DIR) / source.file_path
    if not src.exists():
        return JSONResponse({"error": "源图文件缺失"}, status_code=500)

    img = Image.open(src).convert("RGB")
    w, h = img.size
    mask = normalized_strokes_to_mask(req.strokes, w, h, req.brush_frac)
    mbuf = io.BytesIO()
    Image.fromarray(mask).save(mbuf, format="PNG")

    seed = req.seed if req.seed is not None else random.randint(1, 1_000_000)
    try:
        out = await run_in_threadpool(
            _comfyui_backend().generate_live,
            "lcm_edit.json",
            source_b64=base64.b64encode(src.read_bytes()).decode(),
            source_key=source.id,
            mask_b64=base64.b64encode(mbuf.getvalue()).decode(),
            set_inputs=[
                ["6", "text", req.prompt or "fashion design, refined detail"],
                ["8", "seed", seed],
                ["8", "denoise", req.denoise],
            ],
        )
    except Exception as e:  # noqa: BLE001 — 实时路径任何失败都降级，不抛 500 中断涂抹
        return JSONResponse({"error": f"实时重绘失败: {e}"}, status_code=502)

    if not is_valid_image(out):
        return JSONResponse({"error": "实时重绘返回空帧"}, status_code=502)
    out = composite_subject_lock(img, out, mask)  # 主体锁定 #24：mask 外保留原成衣像素
    fname = _save_bytes("live", out)
    return {"frame": {"url": f"/api/images/{fname}", "seed": seed}}


@router.post("/projects/{project_id}/render-live")
async def render_live(
    project_id: str,
    file: UploadFile = File(...),
    fabric: str = Form("silk"),
    color: str = Form(""),
    pattern: str = Form(""),
    custom: str = Form(""),
    seed: int = Form(42),
    controlnet_strength: float = Form(0.65),
):
    """实时核心机制（#22 修正）：左线稿画板整张 → LCM+ControlNet 整件重渲 → 右成衣帧。

    用户只改左边线稿，右边跟着整件重渲；**固定 seed** → 只有改线处变（这才是"实时局部"）。
    预览帧不入谱系。竞品就是这个：左面画啥/改啥，右面即时渲成白底成衣。
    """
    store = _require_store()
    if store.get_project(project_id) is None:
        return JSONResponse({"error": "project not found"}, status_code=404)
    raw = await file.read()
    if not is_valid_image(raw):
        return JSONResponse({"error": "线稿图无效"}, status_code=422)

    mreq = MaterialRequest(fabric=fabric, color=color, pattern=pattern, custom=custom)
    prompt = _build_material_prompt(mreq)
    inputs = {
        "uploads": [
            {
                "node": "2",
                "b64": base64.b64encode(invert_lineart(raw)).decode(),  # 黑线白底→白线黑底
                "name": "sketch.png",
            }
        ],
        "set": [
            ["3", "text", prompt],
            ["4", "text", GARMENT_NEGATIVE],
            ["5", "control_net_name", REALTIME_CONTROLNET],
            ["6", "strength", controlnet_strength],
            ["8", "seed", seed],
        ],
        "poll_interval": 0.05,
        "timeout": 60,
    }
    try:
        out = await run_in_threadpool(
            _comfyui_backend().generate, REALTIME_WORKFLOW, inputs
        )
    except Exception as e:  # noqa: BLE001 实时路径失败降级，不中断涂改
        return JSONResponse({"error": f"实时渲染失败: {e}"}, status_code=502)
    if not is_valid_image(out):
        return JSONResponse({"error": "实时渲染返回空帧"}, status_code=502)
    fname = _save_bytes("live", out)
    return {"frame": {"url": f"/api/images/{fname}", "seed": seed}}


@router.post("/projects/{project_id}/render-local")
async def render_local(
    project_id: str,
    sketch: UploadFile = File(...),  # 左侧改后的完整线稿（黑线白底）
    mask: UploadFile = File(...),  # 改动区 mask（白=改动）
    base: UploadFile = File(...),  # 右侧当前成衣（被局部重绘的底）
    fabric: str = Form("silk"),
    color: str = Form(""),
    pattern: str = Form(""),
    custom: str = Form(""),
    feature: str = Form(""),  # 意图笔刷语义（#27）：如 "a pocket"/"buttons"/"vertical pleats"
    seed: int = Form(42),
    denoise: float = Form(0.65),  # 降 denoise：改动区保留底图面料（同色同料），形状长进衣服而非异色补丁
):
    """**实时局部渲染（核心壁垒 #22）**：左侧局部改线稿 → 右侧只重绘那一块。

    只对改动区做 ControlNet（跟新线稿）引导的 LCM inpaint，再 mask 合成回当前成衣
    → 改动区跟着新线稿变、其余像素级不动、单帧亚秒。竞品「艾黎设计」的核心做法。
    """
    store = _require_store()
    if store.get_project(project_id) is None:
        return JSONResponse({"error": "project not found"}, status_code=404)
    sketch_raw = await sketch.read()
    mask_raw = await mask.read()
    base_raw = await base.read()
    if not (is_valid_image(sketch_raw) and is_valid_image(mask_raw) and is_valid_image(base_raw)):
        return JSONResponse({"error": "图像无效"}, status_code=422)

    if not feature.strip():  # 一个笔刷无感：CLIP 自动识别改动区画的是什么（#27）
        feature = await run_in_threadpool(_auto_intent, sketch_raw, mask_raw)
    prompt = _build_material_prompt(MaterialRequest(fabric=fabric, color=color, pattern=pattern, custom=custom))
    if feature.strip():  # 把"画的是什么"作为该区域的语义前缀，让它渲成兜/扣/褶而非死线
        prompt = f"{feature.strip()}, {prompt}"
    # 按意图动态调遵循：纹理类（褶/起伏）降 end_percent 让线被理解成立体褶而非黑线；
    # 形状类（兜/扣/领…）保持高遵循贴合轮廓。
    _f = feature.lower()
    end_pct = 0.5 if ("pleat" in _f or "ripple" in _f or "drape" in _f) else 0.85
    inputs = {
        "uploads": [
            {"node": "2", "b64": base64.b64encode(base_raw).decode(), "name": "base.png"},
            {"node": "3", "b64": base64.b64encode(mask_raw).decode(), "name": "mask.png"},
            {"node": "12", "b64": base64.b64encode(invert_lineart(sketch_raw)).decode(), "name": "sketch.png"},
        ],
        "set": [
            ["7", "text", prompt],
            ["5", "control_net_name", REALTIME_CONTROLNET],
            ["13", "end_percent", end_pct],
            ["9", "seed", seed],
            ["9", "denoise", denoise],
        ],
        "poll_interval": 0.05,
        "timeout": 60,
    }
    try:
        out = await run_in_threadpool(
            _comfyui_backend().generate, REALTIME_INPAINT_WORKFLOW, inputs
        )
    except Exception as e:  # noqa: BLE001 实时降级
        return JSONResponse({"error": f"局部渲染失败: {e}"}, status_code=502)
    if not is_valid_image(out):
        return JSONResponse({"error": "局部渲染返回空帧"}, status_code=502)
    out = composite_subject_lock_bytes(base_raw, out, mask_raw, feather=10)  # 区外像素级锁定
    fname = _save_bytes("live", out)
    return {"frame": {"url": f"/api/images/{fname}", "seed": seed}}


@router.post("/projects/{project_id}/finalize")
async def finalize(project_id: str):
    """场景6 完成设计：当前成衣（Edit>Material>Variation）2x 高清放大 → Final 资产。

    保留用户已确认的设计（含局部编辑），仅提分辨率；经空结果校验。
    """
    store = _require_store()
    if store.get_project(project_id) is None:
        return JSONResponse({"error": "project not found"}, status_code=404)
    source = _edit_source(store, project_id)
    if source is None or not source.file_path:
        return JSONResponse({"error": "无可定稿的成衣"}, status_code=409)
    src = Path(IMAGES_DIR) / source.file_path
    if not src.exists():
        return JSONResponse({"error": "成衣文件缺失"}, status_code=500)

    img = Image.open(src).convert("RGB")
    w, h = img.size
    hi = img.resize((w * 2, h * 2), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    hi.save(buf, format="PNG")
    data = buf.getvalue()
    if not is_valid_image(data):  # 空结果不入库（红线）
        return JSONResponse({"error": "定稿生成无效"}, status_code=502)

    fname = _save_bytes("final", data)
    asset = store.add_asset(
        project_id,
        AssetKind.FINAL,
        parent_id=source.id,
        file_path=fname,
        params=source.params,
        seed=source.seed,
    )
    return {
        "final": {
            "id": asset.id,
            "url": f"/api/images/{fname}",
            "width": w * 2,
            "height": h * 2,
        }
    }


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
