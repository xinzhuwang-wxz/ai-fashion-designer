import io

from PIL import Image

import pytest

from app.assets.models import AssetKind
from app.assets.store import InMemoryAssetStore
from app.generation.backend import HttpComfyUIBackend, comfyui_base_url
from app.generation.job import (
    GenerationCancelled,
    GenerationError,
    JobRegistry,
    run_generation,
)


def _png(color=(10, 20, 30), size=(16, 16)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


class FakeBackend:
    """可注入结果的假 ComfyUI backend。"""

    def __init__(self, result: bytes):
        self.result = result
        self.calls = []

    def generate(self, workflow_name, inputs):
        self.calls.append((workflow_name, dict(inputs)))
        return self.result


def _saver():
    saved = {}

    def save(prefix, data):
        name = f"{prefix}_test.png"
        saved[name] = data
        return name

    save.saved = saved
    return save


def test_production_fabric_fill_raises_on_empty_output(monkeypatch):
    """真实 comfyui_client.fabric_fill 空结果时抛错（不再返回 ''→0 字节污染）。"""
    import asyncio

    import app.services.comfyui_client as cc

    async def fake_upload(*a, **k):
        return "x.png"

    async def fake_queue(*a, **k):
        return "pid"

    async def fake_wait(*a, **k):
        return {}  # 无任何输出节点

    monkeypatch.setattr(cc, "_upload_image", fake_upload)
    monkeypatch.setattr(cc, "queue_workflow", fake_queue)
    monkeypatch.setattr(cc, "wait_for_result", fake_wait)

    with pytest.raises(GenerationError):
        asyncio.run(cc.fabric_fill("Zm9v"))


def test_valid_result_creates_asset():
    store = InMemoryAssetStore()
    project = store.create_project()
    backend = FakeBackend(_png())
    save = _saver()

    asset = run_generation(
        backend,
        store,
        save,
        project_id=project.id,
        kind=AssetKind.VARIATION,
        workflow_name="variation.json",
        inputs={"prompt": "fashion"},
        seed=42,
    )

    assert asset.kind == AssetKind.VARIATION
    assert asset.seed == 42
    assert asset.file_path in save.saved
    assert store.get_asset(asset.id) is not None
    assert backend.calls == [("variation.json", {"prompt": "fashion"})]


def test_empty_result_raises_and_creates_no_asset():
    store = InMemoryAssetStore()
    project = store.create_project()
    with pytest.raises(GenerationError):
        run_generation(
            FakeBackend(b""),
            store,
            _saver(),
            project_id=project.id,
            kind=AssetKind.VARIATION,
            workflow_name="variation.json",
            inputs={},
        )
    assert store.latest(project.id, AssetKind.VARIATION) is None


def test_invalid_bytes_raises_and_creates_no_asset():
    store = InMemoryAssetStore()
    project = store.create_project()
    with pytest.raises(GenerationError):
        run_generation(
            FakeBackend(b"not-an-image"),
            store,
            _saver(),
            project_id=project.id,
            kind=AssetKind.MATERIAL,
            workflow_name="fabric_fill_controlnet.json",
            inputs={},
        )
    assert store.latest(project.id, AssetKind.MATERIAL) is None


def test_http_backend_base_url_from_env(monkeypatch):
    monkeypatch.setenv("COMFYUI_URL", "http://gpu-box:8188")
    assert comfyui_base_url() == "http://gpu-box:8188"
    assert HttpComfyUIBackend().base_url == "http://gpu-box:8188"
    monkeypatch.delenv("COMFYUI_URL", raising=False)
    assert HttpComfyUIBackend().base_url == "http://localhost:8188"


def test_cancelled_job_discards_result_without_asset():
    store = InMemoryAssetStore()
    project = store.create_project()
    registry = JobRegistry()
    job_id = registry.new_job()
    registry.cancel(job_id)
    backend = FakeBackend(_png())

    with pytest.raises(GenerationCancelled):
        run_generation(
            backend,
            store,
            _saver(),
            project_id=project.id,
            kind=AssetKind.VARIATION,
            workflow_name="variation.json",
            inputs={},
            job_id=job_id,
            registry=registry,
        )
    assert store.latest(project.id, AssetKind.VARIATION) is None
    # 前置取消应截断推理：generate 不被调用（ADR-0003 省算力/防发热）
    assert backend.calls == []
