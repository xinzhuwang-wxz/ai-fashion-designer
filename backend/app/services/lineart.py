"""
线稿提取服务 — controlnet_aux.LineartDetector
纯推理，无自研

性能：检测器模块级缓存（避免每次请求重建 + 重新加载权重），可用时移到 CUDA。
联网检查由调用方设 HF_HUB_OFFLINE=1 关闭（首个请求曾因联网查更新卡 ~170s）。
"""
import cv2
import numpy as np
from PIL import Image, ImageOps

_lineart_detector = None
_pidinet_detector = None


def _to_device(detector):
    """可用时把检测器移到 GPU；失败则保持 CPU（不致命）。"""
    try:
        import torch

        if torch.cuda.is_available():
            return detector.to("cuda")
    except Exception:
        pass
    return detector


def extract_lineart(image: Image.Image) -> Image.Image:
    """提取服装线稿（检测器缓存 + GPU），反相为【黑线白底】。

    controlnet_aux 默认输出白线黑底；反相后与用户手绘、竞品草图一致，画板显示干净，
    本管线 ControlNet 在黑线白底下渲染质量也更好（实测）。
    """
    global _lineart_detector
    if _lineart_detector is None:
        from controlnet_aux import LineartDetector

        _lineart_detector = _to_device(
            LineartDetector.from_pretrained("lllyasviel/Annotators")
        )
    raw = _lineart_detector(image)  # controlnet_aux：白线黑底、灰淡"素描感"
    g = np.array(ImageOps.autocontrast(ImageOps.invert(raw.convert("L")), cutoff=1))
    # 只把"灰阶素描感"洗成"实心黑线"（与用户实心黑笔统一），不做面积删除——
    # 二值化保留每一条线（框架+细节如领/兜/扣/缝线与原图一致），仅阈值滤掉最淡噪点。
    lines = (g < 170).astype(np.uint8) * 255  # 暗于阈值=线
    lines = cv2.morphologyEx(lines, cv2.MORPH_CLOSE, np.ones((2, 2), np.uint8))  # 轻平滑/连断口
    out = 255 - lines  # 黑线白底
    return Image.fromarray(out).convert("RGB")


def extract_lineart_soft(image: Image.Image) -> Image.Image:
    """更柔和的线稿（PidiNet，检测器缓存 + GPU）。"""
    global _pidinet_detector
    if _pidinet_detector is None:
        from controlnet_aux import PidiNetDetector

        _pidinet_detector = _to_device(
            PidiNetDetector.from_pretrained("lllyasviel/Annotators")
        )
    return _pidinet_detector(image)
