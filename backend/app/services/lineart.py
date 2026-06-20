"""
线稿提取服务 — controlnet_aux.LineartDetector
纯推理，无自研
"""
import io
from PIL import Image


def extract_lineart(image: Image.Image) -> Image.Image:
    """提取服装线稿"""
    from controlnet_aux import LineartDetector
    detector = LineartDetector.from_pretrained("lllyasviel/Annotators")
    return detector(image)


def extract_lineart_soft(image: Image.Image) -> Image.Image:
    """更柔和的线稿（PidiNet）"""
    from controlnet_aux import PidiNetDetector
    detector = PidiNetDetector.from_pretrained("lllyasviel/Annotators")
    return detector(image)
