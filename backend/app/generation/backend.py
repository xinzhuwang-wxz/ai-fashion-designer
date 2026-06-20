"""ComfyUIBackend —— 所有 ComfyUI 推理的统一 seam（ADR-0001/0003/0006）。

generate(workflow_name, inputs) -> bytes：返回输出图片字节，无输出则返回 b""。
真实 HTTP adapter 的 base_url 来自环境变量 COMFYUI_URL（修硬编码缺陷）。
"""
from __future__ import annotations

import os
from typing import Protocol


def comfyui_base_url() -> str:
    return os.environ.get("COMFYUI_URL", "http://localhost:8188")


class ComfyUIBackend(Protocol):
    def generate(self, workflow_name: str, inputs: dict) -> bytes: ...


class HttpComfyUIBackend:
    """真实 ComfyUI HTTP adapter；base_url 默认取自 env COMFYUI_URL。"""

    def __init__(self, base_url: str | None = None):
        self.base_url = base_url or comfyui_base_url()

    def generate(self, workflow_name: str, inputs: dict) -> bytes:  # pragma: no cover
        # 真实推理在场景端点接入时实现（上传/排队/轮询/下载），本片先立 seam。
        raise NotImplementedError(
            "HttpComfyUIBackend.generate 待场景端点接入时实现"
        )
