"""图片校验共享工具 —— 入库前的最低门槛（CLAUDE.md 红线 / ADR-0001）。"""
from __future__ import annotations

from io import BytesIO

from PIL import Image


def is_valid_image(data: bytes) -> bool:
    """非空且可解码为图片。"""
    if not data:
        return False
    try:
        Image.open(BytesIO(data)).verify()
        return True
    except Exception:
        return False
