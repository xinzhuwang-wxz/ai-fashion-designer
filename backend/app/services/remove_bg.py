"""
抠图服务 — rembg 一键去背景 + 可选 SAM2 精确分割
M4 MPS 后端运行
"""
import io
import base64
from PIL import Image


def remove_background(image_bytes: bytes) -> bytes:
    """rembg 去背景，一行代码"""
    from rembg import remove
    return remove(image_bytes)


def remove_background_with_sam(
    image_bytes: bytes, text_prompt: str = "dress"
) -> bytes:
    """Grounded-SAM-2 文本引导精确分割（可选，模型大）"""
    # M4 上 SAM2 较慢，先返回 basic 版本，后续可选升级
    return remove_background(image_bytes)


def pil_to_base64(img: Image.Image, format: str = "PNG") -> str:
    buf = io.BytesIO()
    img.save(buf, format=format)
    return base64.b64encode(buf.getvalue()).decode()


def base64_to_pil(b64: str) -> Image.Image:
    return Image.open(io.BytesIO(base64.b64decode(b64)))


def bytes_to_pil(data: bytes) -> Image.Image:
    return Image.open(io.BytesIO(data))
