"""
变体生成 & 布料填充服务 — 调用 ComfyUI REST API
"""
import json
import time
import base64
import httpx
from pathlib import Path
from typing import Optional

from app.generation.backend import comfyui_base_url
from app.generation.job import GenerationError
from app.imaging import is_valid_image

# 不再硬编码：统一从 env COMFYUI_URL 读取（ADR-0006，修部署缺陷）
COMFYUI_BASE = comfyui_base_url()
# 工作流 JSON 是本项目源码，独立于 vendored comfyui（后者整体不入库）
WORKFLOWS_DIR = Path(__file__).resolve().parent.parent.parent.parent / "workflows"
OUTPUT_DIR = Path(__file__).resolve().parent.parent.parent.parent / "comfyui" / "output"


def _load_workflow(name: str) -> dict:
    """加载工作流 JSON，返回可修改的深拷贝"""
    path = WORKFLOWS_DIR / name
    if not path.exists():
        raise FileNotFoundError(f"Workflow not found: {path}")
    return json.loads(path.read_text())


def _set_node_input(workflow: dict, node_id: str, input_name: str, value):
    """设置工作流中某个节点的输入参数"""
    if node_id in workflow:
        workflow[node_id]["inputs"][input_name] = value


async def _upload_image(image_b64: str, filename: str = "input.png") -> str:
    """上传 base64 图片到 ComfyUI，返回文件名"""
    image_data = base64.b64decode(image_b64)
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{COMFYUI_BASE}/upload/image",
            files={"image": (filename, image_data, "image/png")},
        )
        resp.raise_for_status()
        return resp.json()["name"]


async def queue_workflow(workflow: dict) -> str:
    """提交 ComfyUI 工作流，返回 prompt_id"""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{COMFYUI_BASE}/prompt",
            json={"prompt": workflow},
        )
        resp.raise_for_status()
        data = resp.json()
        if "node_errors" in data and data["node_errors"]:
            raise RuntimeError(f"Workflow errors: {data['node_errors']}")
        return data["prompt_id"]


async def wait_for_result(prompt_id: str, timeout: float = 300) -> dict:
    """等待 ComfyUI 任务完成，返回 outputs"""
    import asyncio
    deadline = time.time() + timeout
    async with httpx.AsyncClient(timeout=timeout) as client:
        while time.time() < deadline:
            resp = await client.get(f"{COMFYUI_BASE}/history/{prompt_id}")
            resp.raise_for_status()
            data = resp.json()
            if prompt_id in data:
                return data[prompt_id]["outputs"]
            await asyncio.sleep(1.0)
    raise TimeoutError(f"ComfyUI prompt {prompt_id} timed out after {timeout}s")


async def _fetch_output_image(filename: str, subfolder: str = "") -> bytes:
    """从 ComfyUI 下载输出图片"""
    params = {"filename": filename, "subfolder": subfolder, "type": "output"}
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(f"{COMFYUI_BASE}/view", params=params)
        resp.raise_for_status()
        return resp.content


def _collect_output_images(outputs: dict) -> list[dict]:
    """从 ComfyUI outputs 中收集所有输出图片信息"""
    images = []
    for node_id, node_output in outputs.items():
        for img in node_output.get("images", []):
            images.append({
                "filename": img["filename"],
                "subfolder": img.get("subfolder", ""),
                "type": img.get("type", "output"),
            })
    return images


async def generate_variations(
    image_b64: str,
    prompt: str = "fashion design, professional photography, high quality, elegant",
    num_variants: int = 3,
    strength: float = 0.65,
) -> list[str]:
    """
    生成图片变体 (img2img)
    返回 base64 编码的输出图片列表
    """
    workflow = _load_workflow("variation.json")
    
    # 上传输入图片
    image_name = await _upload_image(image_b64, "variation_input.png")
    
    # 填充工作流参数
    _set_node_input(workflow, "2", "image", image_name)
    _set_node_input(workflow, "3", "text", prompt)
    _set_node_input(workflow, "6", "denoise", strength)
    
    results = []
    for i in range(num_variants):
        _set_node_input(workflow, "6", "seed", 42 + i * 1000)
        _set_node_input(workflow, "8", "filename_prefix", f"variation_{i}")
        
        prompt_id = await queue_workflow(workflow)
        outputs = await wait_for_result(prompt_id)
        
        for img_info in _collect_output_images(outputs):
            img_bytes = await _fetch_output_image(
                img_info["filename"], img_info["subfolder"]
            )
            if is_valid_image(img_bytes):
                results.append(base64.b64encode(img_bytes).decode())

    if not results:
        raise GenerationError("ComfyUI 变体返回空/无效结果")
    return results


async def fabric_fill(
    lineart_b64: str,
    fabric_prompt: str = "silk fabric, smooth texture, elegant drape",
    controlnet_model: str = "control_v11p_sd15_lineart.pth",
) -> str:
    """
    线稿 + 布料描述 → 填充后的完整图像 (ControlNet)
    返回 base64 编码的输出图片
    """
    workflow = _load_workflow("fabric_fill_controlnet.json")
    
    # 上传线稿图片
    image_name = await _upload_image(lineart_b64, "lineart_input.png")
    
    # 填充工作流参数
    _set_node_input(workflow, "2", "image", image_name)
    _set_node_input(workflow, "3", "text", fabric_prompt)
    _set_node_input(workflow, "5", "control_net_name", controlnet_model)
    _set_node_input(workflow, "8", "seed", 42)
    
    prompt_id = await queue_workflow(workflow)
    outputs = await wait_for_result(prompt_id)
    
    images = _collect_output_images(outputs)
    if not images:
        raise GenerationError("ComfyUI 返回空结果（无输出图）")
    
    img_bytes = await _fetch_output_image(images[0]["filename"], images[0]["subfolder"])
    if not is_valid_image(img_bytes):
        raise GenerationError("ComfyUI 返回无效图片")
    return base64.b64encode(img_bytes).decode()


async def simple_img2img(
    image_b64: str,
    prompt: str,
    strength: float = 0.7,
    seed: int = 42,
) -> str:
    """
    简单 img2img：上传图片 + prompt → 重绘
    """
    return (await generate_variations(
        image_b64, prompt, num_variants=1, strength=strength
    ))[0]


# 布料提示词库
FABRIC_PROMPTS = {
    "silk": "silk fabric, smooth texture, elegant drape, luxurious sheen, fashion design, studio lighting",
    "denim": "denim fabric, blue jeans texture, cotton weave, casual wear, street style",
    "lace": "lace fabric, delicate pattern, transparent, intricate floral detail, bridal fashion",
    "leather": "leather material, glossy surface, textured, premium quality, edgy fashion",
    "cotton": "cotton fabric, soft texture, breathable, natural fiber, casual everyday wear",
    "linen": "linen fabric, natural texture, slightly wrinkled, summer wear, organic fashion",
    "wool": "wool fabric, warm texture, knitted pattern, winter fashion, cozy",
    "velvet": "velvet fabric, rich texture, deep color, luxurious evening wear",
    "chiffon": "chiffon fabric, sheer, flowing, lightweight, elegant dress, romantic",
    "brocade": "brocade fabric, ornate pattern, metallic thread, traditional Chinese, imperial",
    "embroidery": "embroidery detail, hand-stitched, intricate pattern, artisanal, couture",
    "satin": "satin fabric, glossy smooth, reflective sheen, evening wear, red carpet",
}


async def inpaint_with_lcm(
    image_b64: str,
    prompt: str = "fashion design, professional photography",
    strength: float = 0.65,
    seed: int = 42,
) -> str:
    """
    快速 LCM 重绘 — 用于实时编辑的自动触发
    返回 base64 编码的输出图片
    """
    workflow = _load_workflow("lcm_variation.json")

    image_name = await _upload_image(image_b64, "inpaint_input.png")

    _set_node_input(workflow, "3", "image", image_name)
    _set_node_input(workflow, "4", "text", prompt)
    _set_node_input(workflow, "7", "seed", seed)
    _set_node_input(workflow, "7", "denoise", strength)

    prompt_id = await queue_workflow(workflow)
    outputs = await wait_for_result(prompt_id)

    images = _collect_output_images(outputs)
    if not images:
        raise GenerationError("ComfyUI 返回空结果（无输出图）")

    img_bytes = await _fetch_output_image(images[0]["filename"], images[0]["subfolder"])
    if not is_valid_image(img_bytes):
        raise GenerationError("ComfyUI 返回无效图片")
    return base64.b64encode(img_bytes).decode()


async def final_render(
    image_b64: str,
    prompt: str = "fashion design, professional photography, high quality, 8K, detailed fabric texture, elegant",
    seed: int = 42,
) -> str:
    """
    高质量最终渲染 — 场景 6 完成设计
    使用完整 SD 1.5 + 20 步 denoising + low strength 保持结构
    GPU 上 ~5s，M4 上 ~60s
    """
    workflow = _load_workflow("variation.json")

    image_name = await _upload_image(image_b64, "final_input.png")

    _set_node_input(workflow, "2", "image", image_name)
    _set_node_input(workflow, "3", "text", prompt)
    _set_node_input(workflow, "6", "denoise", 0.35)   # low strength = preserve structure
    _set_node_input(workflow, "6", "seed", seed)
    _set_node_input(workflow, "8", "filename_prefix", "final_render")

    prompt_id = await queue_workflow(workflow)
    outputs = await wait_for_result(prompt_id, timeout=600)

    images = _collect_output_images(outputs)
    if not images:
        raise GenerationError("ComfyUI 返回空结果（无输出图）")

    img_bytes = await _fetch_output_image(images[0]["filename"], images[0]["subfolder"])
    if not is_valid_image(img_bytes):
        raise GenerationError("ComfyUI 返回无效图片")
    return base64.b64encode(img_bytes).decode()
