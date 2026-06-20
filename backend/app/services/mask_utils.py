"""
Mask 生成 + 变化检测 + 无缝混合
画师笔触 → OpenCV 处理 → inpainting mask
"""
import io

import cv2
import numpy as np
from PIL import Image, ImageOps


def invert_lineart(png_bytes: bytes) -> bytes:
    """黑线白底（人看/手绘/画板）→ 白线黑底（controlnet_aux lineart 模型要的格式）。

    本管线统一：显示/编辑用黑线白底；喂 ControlNet 前在此边界转白线黑底，渲出实心成衣
    （黑线白底直喂会渲成线框）。
    """
    img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    out = io.BytesIO()
    ImageOps.invert(img).save(out, format="PNG")
    return out.getvalue()


def normalized_strokes_to_mask(
    points: list[dict],
    img_w: int,
    img_h: int,
    brush_frac: float = 0.06,
) -> np.ndarray:
    """图像归一化坐标 [0,1] → 按图像像素尺寸生成 mask（ADR-0003 坐标对齐核心）。

    画布缩放/平移的转换由前端完成（前端发归一化图像坐标），后端只按图尺寸落点，
    因此任意缩放/平移下 mask 都与落笔位置对齐。
    """
    mask = np.zeros((img_h, img_w), dtype=np.uint8)
    radius = max(1, int(brush_frac * min(img_w, img_h) / 2))
    for p in points:
        x = int(round(float(p.get("x", 0)) * img_w))
        y = int(round(float(p.get("y", 0)) * img_h))
        x = max(0, min(img_w - 1, x))
        y = max(0, min(img_h - 1, y))
        cv2.circle(mask, (x, y), radius, 255, -1)
    return mask


def stroke_to_mask(
    stroke_points: list[dict],
    canvas_width: int,
    canvas_height: int,
    brush_size: int = 10,
    dilation: int = 15,
    feather: int = 8,
) -> np.ndarray:
    """将画师笔触转换为 inpainting mask"""
    mask = np.zeros((canvas_height, canvas_width), dtype=np.uint8)

    for p in stroke_points:
        x, y = int(p.get("x", 0)), int(p.get("y", 0))
        cv2.circle(mask, (x, y), brush_size // 2, 255, -1)

    # 膨胀：重绘范围略大于修改范围
    kernel = cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE, (dilation, dilation)
    )
    mask = cv2.dilate(mask, kernel)

    # 高斯模糊羽化
    ksize = feather * 2 + 1
    mask = cv2.GaussianBlur(mask, (ksize, ksize), 0)

    return mask


def seamless_blend(
    result: np.ndarray, original: np.ndarray, mask: np.ndarray
) -> np.ndarray:
    """无缝混合 — 用 OpenCV seamlessClone 自然融合"""
    center = (original.shape[1] // 2, original.shape[0] // 2)
    mask_uint8 = (mask > 128).astype(np.uint8) * 255
    blended = cv2.seamlessClone(
        result, original, mask_uint8, center, cv2.NORMAL_CLONE
    )
    return blended


def simple_blend(
    result: np.ndarray, original: np.ndarray, mask: np.ndarray
) -> np.ndarray:
    """简单 Alpha 混合（更快，实时预览用）"""
    mask_3ch = np.stack([mask / 255.0] * 3, axis=-1)
    blended = result * mask_3ch + original * (1 - mask_3ch)
    return blended.astype(np.uint8)


def composite_subject_lock_bytes(
    source_png: bytes,
    rendered_png: bytes,
    mask_png: bytes,
    feather: int = 10,
) -> bytes:
    """字节版主体锁定：底图/渲染/mask 都是 PNG bytes；mask 白=改动区（保留渲染），黑=保留底图。"""
    src = Image.open(io.BytesIO(source_png)).convert("RGB")
    mask = np.array(Image.open(io.BytesIO(mask_png)).convert("L").resize(src.size))
    return composite_subject_lock(src, rendered_png, mask, feather)


def composite_subject_lock(
    source: Image.Image,
    rendered_png: bytes,
    mask: np.ndarray,
    feather: int = 12,
) -> bytes:
    """主体锁定（#24）：渲染只在 mask 区域生效，mask 外严格保留原图像素。

    扩散即便全局漂移，合成后 mask 外权重=0 → 与原图逐像素一致，主体不动；
    羽化让边界平滑过渡（feather=0 则硬边）。返回 PNG bytes。
    """
    src = np.array(source.convert("RGB"))
    h, w = src.shape[:2]
    rnd = Image.open(io.BytesIO(rendered_png)).convert("RGB")
    if rnd.size != (w, h):
        rnd = rnd.resize((w, h))
    rnd_np = np.array(rnd)
    m = mask.astype(np.float32)
    if feather > 0:
        k = feather * 2 + 1
        m = cv2.GaussianBlur(m, (k, k), 0)
    blended = simple_blend(rnd_np, src, m)
    out = io.BytesIO()
    Image.fromarray(blended).save(out, format="PNG")
    return out.getvalue()
