"""GenerationJob runner —— 校验非空结果才入库，支持取消（ADR-0001/0003）。"""
from __future__ import annotations

import uuid
from typing import Callable, Optional

from app.assets.models import AssetKind, DesignAsset
from app.assets.store import AssetStore
from app.generation.backend import ComfyUIBackend
from app.imaging import is_valid_image


class GenerationError(RuntimeError):
    """生成失败（含空/无效结果）。"""


class GenerationCancelled(GenerationError):
    """job 已被取消，结果丢弃不入库。"""


class JobRegistry:
    def __init__(self) -> None:
        self._cancelled: set[str] = set()

    def new_job(self) -> str:
        return f"job_{uuid.uuid4().hex[:12]}"

    def cancel(self, job_id: str) -> None:
        self._cancelled.add(job_id)

    def is_cancelled(self, job_id: str) -> bool:
        return job_id in self._cancelled


def run_generation(
    backend: ComfyUIBackend,
    store: AssetStore,
    save: Callable[[str, bytes], str],
    *,
    project_id: str,
    kind: AssetKind,
    workflow_name: str,
    inputs: dict,
    parent_id: Optional[str] = None,
    params: Optional[dict] = None,
    seed: Optional[int] = None,
    model: Optional[str] = None,
    job_id: Optional[str] = None,
    registry: Optional[JobRegistry] = None,
    post_process: Optional[Callable[[bytes], bytes]] = None,
) -> DesignAsset:
    """生成 → 校验非空可解码 → 落盘 → add_asset。空/坏抛 GenerationError；
    job 已取消抛 GenerationCancelled（均不创建资产）。

    取消在 generate 前后各检查一次：前置检查截断重型推理（ADR-0003，省算力/防发热），
    后置检查兜住推理期间到达的取消。"""

    def _cancelled() -> bool:
        return (
            registry is not None
            and job_id is not None
            and registry.is_cancelled(job_id)
        )

    if _cancelled():
        raise GenerationCancelled(job_id)
    data = backend.generate(workflow_name, inputs)
    if not is_valid_image(data):
        raise GenerationError(f"generation '{workflow_name}' 返回空/无效结果")
    # 主体锁定等后处理（#24）：在已校验的渲染字节上做 mask 合成，再落盘。
    if post_process is not None:
        data = post_process(data)
        if not is_valid_image(data):
            raise GenerationError(f"generation '{workflow_name}' 后处理返回无效结果")
    if _cancelled():
        raise GenerationCancelled(job_id)
    file_path = save(kind.value, data)
    return store.add_asset(
        project_id,
        kind,
        parent_id=parent_id,
        params=params,
        seed=seed,
        model=model,
        file_path=file_path,
    )
