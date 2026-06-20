"""ComfyUIBackend —— 所有 ComfyUI 推理的统一 seam（ADR-0001/0003/0006）。

generate(workflow_name, inputs) -> bytes：返回输出图片字节，无输出则返回 b""。
真实 HTTP adapter 的 base_url 来自环境变量 COMFYUI_URL（修硬编码缺陷）。

inputs 约定（与具体工作流的 node id 解耦，由调用方提供映射）：
  {
    "uploads": [{"node": "2", "b64": <图片base64>, "name": "in.png"}],
    "set":     [["3", "text", prompt], ["6", "seed", 42], ["6", "denoise", 0.65]],
    "timeout": 300,
  }
"""
from __future__ import annotations

import base64
import json
import os
import time
from pathlib import Path
from typing import Protocol

import httpx

WORKFLOWS_DIR = Path(__file__).resolve().parent.parent.parent.parent / "workflows"


def comfyui_base_url() -> str:
    return os.environ.get("COMFYUI_URL", "http://localhost:8188")


class ComfyUIBackend(Protocol):
    def generate(self, workflow_name: str, inputs: dict) -> bytes: ...


def _set_node_input(workflow: dict, node_id: str, input_name: str, value) -> None:
    if node_id in workflow:
        workflow[node_id]["inputs"][input_name] = value


class HttpComfyUIBackend:
    """真实 ComfyUI HTTP adapter（同步 httpx，供线程池调用）。base_url 取自 env。"""

    def __init__(self, base_url: str | None = None, workflows_dir: Path | None = None):
        self.base_url = base_url or comfyui_base_url()
        self.workflows_dir = workflows_dir or WORKFLOWS_DIR

    def generate(self, workflow_name: str, inputs: dict) -> bytes:
        workflow = json.loads((self.workflows_dir / workflow_name).read_text())
        timeout = inputs.get("timeout", 300)

        # trust_env=False：直连 ComfyUI，不走系统 HTTP 代理（否则 localhost:8188 被代理拦截）
        with httpx.Client(timeout=timeout, trust_env=False) as client:
            for up in inputs.get("uploads", []):
                data = base64.b64decode(up["b64"])
                resp = client.post(
                    f"{self.base_url}/upload/image",
                    files={"image": (up.get("name", "input.png"), data, "image/png")},
                )
                resp.raise_for_status()
                _set_node_input(workflow, up["node"], "image", resp.json()["name"])

            for node_id, input_name, value in inputs.get("set", []):
                _set_node_input(workflow, node_id, input_name, value)

            resp = client.post(f"{self.base_url}/prompt", json={"prompt": workflow})
            resp.raise_for_status()
            prompt_id = resp.json()["prompt_id"]

            deadline = time.time() + timeout
            outputs = None
            while time.time() < deadline:
                hist = client.get(f"{self.base_url}/history/{prompt_id}").json()
                if prompt_id in hist:
                    outputs = hist[prompt_id]["outputs"]
                    break
                time.sleep(1.0)
            if not outputs:
                return b""

            for node_output in outputs.values():
                for img in node_output.get("images", []):
                    view = client.get(
                        f"{self.base_url}/view",
                        params={
                            "filename": img["filename"],
                            "subfolder": img.get("subfolder", ""),
                            "type": img.get("type", "output"),
                        },
                    )
                    if view.status_code == 200 and view.content:
                        return view.content
        return b""
