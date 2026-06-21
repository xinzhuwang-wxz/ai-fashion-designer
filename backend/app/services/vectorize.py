"""
线稿矢量化 —— 把提取的【光栅】线稿转成一条条折线笔画（归一化坐标），
供前端用矢量画板（Tldraw）的【原生 draw 笔画】画出来。

这样"提取的草图"与"用户后画的线"是同一种画板元素，原生橡皮/笔统一擦改
（而不是把光栅图硬印到画板上、只能用自定义擦除）。

方法：清洗→二值化→骨架化(centerline)→findContours 逐连通分量取中线→
approxPolyDP 简化→按长度过滤掉细碎纹理→归一化坐标。
"""
from __future__ import annotations

import io

import cv2
import numpy as np
from PIL import Image


def vectorize_lineart(
    png_bytes: bytes,
    min_len_frac: float = 0.03,
    epsilon: float = 1.6,
    max_strokes: int = 400,
) -> list[list[dict]]:
    """黑线白底 PNG → 折线笔画列表 [[{x,y}(归一化0..1), ...], ...]。"""
    img = Image.open(io.BytesIO(png_bytes)).convert("L")
    w, h = img.size
    g = np.array(img)
    binary = g < 128  # 线=True

    try:
        from skimage.morphology import skeletonize

        skel = skeletonize(binary).astype(np.uint8) * 255
    except Exception:
        # 退化：没有 skimage 就用细化近似（直接用二值线）
        skel = (binary.astype(np.uint8)) * 255

    contours, _ = cv2.findContours(skel, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    min_len = min_len_frac * max(w, h)
    scored: list[tuple[float, list[dict]]] = []
    for c in contours:
        length = cv2.arcLength(c, False)
        if length < min_len:
            continue
        approx = cv2.approxPolyDP(c, epsilon, False).reshape(-1, 2)
        pts = [{"x": float(x) / w, "y": float(y) / h} for x, y in approx]
        if len(pts) >= 2:
            scored.append((length, pts))

    # 长笔画优先，限制总数（避免上千碎线拖垮画板）
    scored.sort(key=lambda s: s[0], reverse=True)
    return [pts for _, pts in scored[:max_strokes]]
