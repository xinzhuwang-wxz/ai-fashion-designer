"""
实时 Inpainting 服务 — 双层策略
1. 快速预览：OpenCV inpainting（即时，无 ML）
2. 高质量重绘：rembg 抠图 + diffusers inpainting（有 ML）
"""
import cv2
import numpy as np
import base64
from app.services.mask_utils import stroke_to_mask, simple_blend


def fast_preview_inpaint(
    image_b64: str,
    stroke_points: list[dict],
    brush_size: int = 10,
) -> str:
    """
    快速预览：OpenCV inpainting（无 ML，秒级返回）
    画师边画边看的版本
    """
    # 解码图片
    img_bytes = base64.b64decode(image_b64)
    img_array = np.frombuffer(img_bytes, np.uint8)
    original = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
    h, w = original.shape[:2]

    # 生成 mask（用源图实际尺寸）
    mask = stroke_to_mask(stroke_points, w, h, brush_size)

    # OpenCV inpainting（Telea 算法，快速）
    mask_uint8 = (mask > 64).astype(np.uint8) * 255
    inpainted = cv2.inpaint(original, mask_uint8, inpaintRadius=5, flags=cv2.INPAINT_TELEA)

    # 编码返回
    _, buf = cv2.imencode('.png', inpainted)
    return base64.b64encode(buf.tobytes()).decode()


# NOTE: high_quality_inpaint() removed — unused; ComfyUI LCM handles quality inpainting via WS commit.
# NOTE: composite_result() removed — unused; preview/commit flow uses OpenCV fast path + ComfyUI separately.
