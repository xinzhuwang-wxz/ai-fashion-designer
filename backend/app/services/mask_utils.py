"""
Mask 生成 + 变化检测 + 无缝混合
画师笔触 → OpenCV 处理 → inpainting mask
"""
import cv2
import numpy as np


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
