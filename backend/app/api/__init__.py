"""
API 路由 — 所有 HTTP + WebSocket 端点
图片不再内联 base64，改为存入 static/images/ 返回 URL
"""
from fastapi import APIRouter, UploadFile, File, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, FileResponse
from pydantic import BaseModel
from typing import Optional
from pathlib import Path
import base64
import uuid
import os

router = APIRouter()

# 与资产链路同目录：老 session 路由的 /api/images 先注册会抢前，必须读同一 AIFD_IMAGES_DIR，
# 否则资产渲染存在新目录、老路由却去 static/images 找 → 404（实测踩坑）。
STATIC_IMAGES = Path(
    os.environ.get(
        "AIFD_IMAGES_DIR",
        str(Path(__file__).resolve().parent.parent.parent / "static" / "images"),
    )
)
STATIC_IMAGES.mkdir(parents=True, exist_ok=True)

from app.services.state_machine import Step, DesignStateMachine

_sm: Optional[DesignStateMachine] = None

def set_state_machine(sm: DesignStateMachine):
    global _sm
    _sm = sm

# ── image helpers ───────────────────────────────────────────────────

def _save_image(b64: str, prefix: str = "img") -> tuple[str, str]:
    """保存 base64 图片到 static/images，返回 (filename, url)"""
    fname = f"{prefix}_{uuid.uuid4().hex[:8]}.png"
    path = STATIC_IMAGES / fname
    data = base64.b64decode(b64)
    path.write_bytes(data)
    return fname, f"/api/images/{fname}"

def _save_image_bytes(data: bytes, prefix: str = "img") -> tuple[str, str]:
    """保存 bytes 图片，返回 (filename, url)"""
    fname = f"{prefix}_{uuid.uuid4().hex[:8]}.png"
    path = STATIC_IMAGES / fname
    path.write_bytes(data)
    return fname, f"/api/images/{fname}"

@router.get("/images/{filename}")
async def serve_image(filename: str):
    """直接提供图片文件"""
    path = STATIC_IMAGES / filename
    if not path.exists():
        return JSONResponse({"error": "not found"}, 404)
    return FileResponse(path, media_type="image/png")

# ── models ──────────────────────────────────────────────────────────

class FabricFillRequest(BaseModel):
    session_id: str
    fabric_type: str
    custom_prompt: Optional[str] = ""

class VariationRequest(BaseModel):
    session_id: str
    prompt: Optional[str] = ""
    num_variants: int = 3
    strength: float = 0.65

# ── REST endpoints ──────────────────────────────────────────────────

@router.get("/session/{session_id}")
async def get_session(session_id: str):
    state = _sm.get_session(session_id)
    if not state:
        return JSONResponse({"error": "session not found"}, 404)
    return {
        "session_id": state.session_id,
        "current_step": state.current_step,
        "has_image": state.original_image is not None,
    }

@router.post("/session/{session_id}")
async def create_session(session_id: str):
    state = _sm.create_session(session_id)
    return {"session_id": state.session_id, "current_step": Step.SELECT}

@router.post("/step/{session_id}/select")
async def step_select(session_id: str, file: UploadFile = File(...)):
    """上传参考图 → 抠图"""
    from app.services.remove_bg import remove_background

    state = _sm.get_session(session_id)
    if not state:
        return JSONResponse({"error": "session not found"}, 404)

    image_bytes = await file.read()
    state.original_image = base64.b64encode(image_bytes).decode()

    try:
        bg_removed = remove_background(image_bytes)
        _, url = _save_image_bytes(bg_removed, "removed_bg")
        state.removed_bg_image = base64.b64encode(bg_removed).decode()
        _sm.transition(state, Step.REMOVE_BG)
        return {"step": state.current_step, "image_url": url}
    except Exception as e:
        return JSONResponse({"error": f"Remove bg failed: {str(e)}"}, 500)

@router.post("/step/{session_id}/lineart")
async def step_lineart(session_id: str):
    """提取线稿"""
    from app.services.remove_bg import base64_to_pil
    from app.services.lineart import extract_lineart

    state = _sm.get_session(session_id)
    if not state:
        return JSONResponse({"error": "session not found"}, 404)

    if not state.removed_bg_image and not state.original_image:
        return JSONResponse({"error": "no image uploaded"}, 400)

    img = base64_to_pil(state.removed_bg_image or state.original_image)
    lineart_img = extract_lineart(img)

    # save to disk
    import io as _io
    buf = _io.BytesIO()
    lineart_img.save(buf, format="PNG")
    _, url = _save_image_bytes(buf.getvalue(), "lineart")

    state.lineart_image = base64.b64encode(buf.getvalue()).decode()
    _sm.transition(state, Step.LINEART)
    return {"step": state.current_step, "image_url": url}

@router.post("/step/{session_id}/variations")
async def step_variations(session_id: str, req: VariationRequest):
    """生成图片变体 (img2img)"""
    from app.services.comfyui_client import generate_variations

    state = _sm.get_session(session_id)
    if not state:
        return JSONResponse({"error": "session not found"}, 404)

    source = state.removed_bg_image or state.original_image
    if not source:
        return JSONResponse({"error": "no image uploaded"}, 400)

    prompt = req.prompt or "fashion design, professional photography, elegant"

    try:
        results = await generate_variations(
            source, prompt,
            num_variants=req.num_variants,
            strength=req.strength,
        )
        urls = []
        for r in results:
            _, url = _save_image(r, "variation")
            urls.append(url)
        return {"step": state.current_step, "image_urls": urls}
    except Exception as e:
        return JSONResponse({"error": f"Variation generation failed: {str(e)}"}, 500)

@router.post("/step/{session_id}/fill")
async def step_fill(session_id: str, req: FabricFillRequest):
    """布料填充"""
    from app.services.comfyui_client import fabric_fill, FABRIC_PROMPTS

    state = _sm.get_session(session_id)
    if not state:
        return JSONResponse({"error": "session not found"}, 404)

    if not state.lineart_image:
        return JSONResponse({"error": "no lineart, run /lineart first"}, 400)

    prompt = req.custom_prompt or FABRIC_PROMPTS.get(req.fabric_type, req.fabric_type)
    state.fabric_prompt = prompt

    try:
        result = await fabric_fill(state.lineart_image, prompt)
        state.filled_image = result
        _, url = _save_image(result, "filled")
        _sm.transition(state, Step.FILL)
        return {"step": state.current_step, "image_url": url}
    except Exception as e:
        return JSONResponse({"error": f"ComfyUI fill failed: {str(e)}"}, 500)

@router.get("/fabric-types")
async def list_fabric_types():
    from app.services.comfyui_client import FABRIC_PROMPTS
    return {"fabrics": list(FABRIC_PROMPTS.keys())}


@router.post("/step/{session_id}/finalize")
async def step_finalize(session_id: str, req: FabricFillRequest):
    """
    场景 6: 完成设计 — 高质量最终渲染
    使用 SD 1.5 20 步 + low strength，输出高清成品图
    """
    from app.services.comfyui_client import final_render, FABRIC_PROMPTS

    state = _sm.get_session(session_id)
    if not state:
        return JSONResponse({"error": "session not found"}, 404)

    source = state.filled_image or state.removed_bg_image or state.original_image
    if not source:
        return JSONResponse({"error": "no image to render"}, 400)

    prompt = req.custom_prompt or FABRIC_PROMPTS.get(req.fabric_type, "fashion design, elegant")
    import random
    seed = random.randint(1, 1_000_000)

    try:
        result = await final_render(source, prompt=prompt, seed=seed)
        _, url = _save_image(result, "final")
        return {"step": state.current_step, "image_url": url, "status": "done"}
    except Exception as e:
        return JSONResponse({"error": f"Final render failed: {str(e)}"}, 500)


# ── WebSocket ───────────────────────────────────────────────────────

@router.websocket("/ws/{session_id}")
async def websocket_edit(websocket: WebSocket, session_id: str):
    await websocket.accept()

    state = _sm.get_session(session_id)
    if not state:
        await websocket.send_json({"error": "session not found"})
        await websocket.close()
        return

    # fixed preview filename — overwritten each time, browser busts with ?ts
    preview_fname = f"preview_{session_id}.png"

    try:
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type")

            if msg_type == "stroke":
                points = data.get("points", [])

                if state.filled_image or state.removed_bg_image:
                    try:
                        from app.services.inpaint import fast_preview_inpaint
                        source = state.filled_image or state.removed_bg_image
                        preview_b64 = fast_preview_inpaint(
                            source, points,
                            brush_size=data.get("brush_size", 10),
                        )
                        # write to disk, return URL
                        path = STATIC_IMAGES / preview_fname
                        path.write_bytes(base64.b64decode(preview_b64))
                        url = f"/api/images/{preview_fname}?ts={int(__import__('time').time() * 1000)}"
                        await websocket.send_json({
                            "type": "preview",
                            "image_url": url,
                            "points_count": len(points),
                        })
                    except Exception as e:
                        await websocket.send_json({
                            "type": "preview_ack",
                            "points_count": len(points),
                            "error": str(e),
                        })

            elif msg_type == "commit":
                if state.filled_image or state.removed_bg_image:
                    all_points = data.get("points", [])
                    if not all_points:
                        continue

                    await websocket.send_json({
                        "type": "render_start",
                        "points_count": len(all_points),
                    })

                    try:
                        from app.services.inpaint import fast_preview_inpaint
                        source = state.filled_image or state.removed_bg_image
                        preview_b64 = fast_preview_inpaint(
                            source, all_points,
                            brush_size=data.get("brush_size", 5),
                        )
                        path = STATIC_IMAGES / preview_fname
                        path.write_bytes(base64.b64decode(preview_b64))
                        url = f"/api/images/{preview_fname}?ts={int(__import__('time').time() * 1000)}"
                        await websocket.send_json({
                            "type": "preview",
                            "image_url": url,
                        })

                        from app.services.comfyui_client import inpaint_with_lcm, FABRIC_PROMPTS
                        fabric = data.get("fabric_prompt", "silk")
                        prompt = FABRIC_PROMPTS.get(fabric, fabric)
                        import random
                        seed = random.randint(1, 1_000_000)

                        result = await inpaint_with_lcm(
                            source,
                            prompt=prompt,
                            strength=0.6,
                            seed=seed,
                        )

                        if result:
                            state.filled_image = result
                            _, result_url = _save_image(result, "lcm")
                            await websocket.send_json({
                                "type": "render",
                                "image_url": result_url,
                                "status": "done",
                            })
                        else:
                            await websocket.send_json({
                                "type": "render",
                                "status": "empty_result",
                            })
                    except Exception as e:
                        await websocket.send_json({
                            "type": "render",
                            "status": "failed",
                            "error": str(e),
                        })

            elif msg_type == "ping":
                await websocket.send_json({"type": "pong"})

    except WebSocketDisconnect:
        pass
