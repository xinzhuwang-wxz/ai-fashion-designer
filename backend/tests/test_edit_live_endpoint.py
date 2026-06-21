import io
import os

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

import app.assets.api as api
from app.assets.api import router, set_asset_store, set_comfyui_backend
from app.assets.models import AssetKind
from app.assets.store import InMemoryAssetStore


def _png(color=(120, 80, 80), size=(64, 96)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


class FakeLiveBackend:
    def __init__(self, result: bytes):
        self.result = result
        self.live_calls = []

    def generate(self, workflow_name, inputs):  # 批路径，本测试不用
        return self.result

    def generate_live(
        self,
        workflow_name,
        *,
        source_b64,
        source_key,
        mask_b64,
        set_inputs,
        poll_interval=0.05,
        timeout=30.0,
    ):
        self.live_calls.append(
            {"workflow": workflow_name, "source_key": source_key, "set": set_inputs}
        )
        return self.result


def _make_client(store, images_dir, backend):
    api.IMAGES_DIR = images_dir
    os.makedirs(images_dir, exist_ok=True)
    set_asset_store(store)
    set_comfyui_backend(backend)
    app = FastAPI()
    app.include_router(router, prefix="/api")
    return TestClient(app)


def _seed_render(store, images_dir, pid):
    with open(os.path.join(images_dir, "mat.png"), "wb") as f:
        f.write(_png())
    return store.add_asset(pid, AssetKind.MATERIAL, file_path="mat.png")


def test_edit_live_returns_transient_frame_without_lineage(tmp_path):
    store = InMemoryAssetStore()
    images = str(tmp_path / "img")
    backend = FakeLiveBackend(_png((10, 60, 90)))
    client = _make_client(store, images, backend)
    pid = client.post("/api/projects").json()["project_id"]
    mat = _seed_render(store, images, pid)

    r = client.post(
        f"/api/projects/{pid}/edit-live",
        json={"strokes": [{"x": 0.5, "y": 0.4}], "prompt": "golden lace"},
    )
    assert r.status_code == 200
    assert r.json()["frame"]["url"].startswith("/api/images/")
    # 实时预览帧【不入谱系】：连续涂抹不应刷爆资产树
    assert store.latest(pid, AssetKind.EDIT) is None
    # 走 LCM 快路径，且 source 按当前成衣资产 id 缓存（连续涂抹只重传 mask）
    assert backend.live_calls[0]["workflow"] == "lcm_edit.json"
    assert backend.live_calls[0]["source_key"] == mat.id


def test_edit_live_without_render_blocked(tmp_path):
    store = InMemoryAssetStore()
    images = str(tmp_path / "img")
    client = _make_client(store, images, FakeLiveBackend(_png()))
    pid = client.post("/api/projects").json()["project_id"]

    r = client.post(
        f"/api/projects/{pid}/edit-live", json={"strokes": [{"x": 0.5, "y": 0.5}]}
    )
    assert r.status_code == 409


def test_edit_live_no_strokes_rejected(tmp_path):
    store = InMemoryAssetStore()
    images = str(tmp_path / "img")
    client = _make_client(store, images, FakeLiveBackend(_png()))
    pid = client.post("/api/projects").json()["project_id"]
    _seed_render(store, images, pid)

    r = client.post(f"/api/projects/{pid}/edit-live", json={"strokes": []})
    assert r.status_code == 400
