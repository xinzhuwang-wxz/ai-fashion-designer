import io
import os

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

import app.assets.api as api
from app.assets.api import router, set_asset_store, set_comfyui_backend
from app.assets.models import AssetKind
from app.assets.store import InMemoryAssetStore


def _png(color=(20, 40, 60)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (16, 16), color).save(buf, format="PNG")
    return buf.getvalue()


class FakeBackend:
    def __init__(self, result: bytes):
        self.result = result
        self.calls = []

    def generate(self, workflow_name, inputs):
        self.calls.append(workflow_name)
        return self.result


def _make_client(store, images_dir, backend):
    api.IMAGES_DIR = images_dir
    os.makedirs(images_dir, exist_ok=True)
    set_asset_store(store)
    set_comfyui_backend(backend)
    app = FastAPI()
    app.include_router(router, prefix="/api")
    return TestClient(app)


def _seed_cutout(store, images_dir, pid):
    with open(os.path.join(images_dir, "cutout_x.png"), "wb") as f:
        f.write(_png())
    return store.add_asset(pid, AssetKind.CUTOUT, file_path="cutout_x.png")


def test_variations_creates_variation_assets_under_cutout(tmp_path):
    store = InMemoryAssetStore()
    images = str(tmp_path / "img")
    backend = FakeBackend(_png((90, 30, 30)))
    client = _make_client(store, images, backend)

    pid = client.post("/api/projects").json()["project_id"]
    cutout = _seed_cutout(store, images, pid)

    r = client.post(f"/api/projects/{pid}/variations", json={"num_variants": 2})
    assert r.status_code == 200
    assert len(r.json()["variations"]) == 2

    variations = store.children(cutout.id, kind=AssetKind.VARIATION)
    assert len(variations) == 2
    assert all(v.parent_id == cutout.id for v in variations)
    assert all(v.seed is not None for v in variations)


def test_variations_empty_result_creates_no_asset(tmp_path):
    store = InMemoryAssetStore()
    images = str(tmp_path / "img")
    client = _make_client(store, images, FakeBackend(b""))  # 空结果

    pid = client.post("/api/projects").json()["project_id"]
    cutout = _seed_cutout(store, images, pid)

    r = client.post(f"/api/projects/{pid}/variations", json={"num_variants": 1})
    assert r.status_code == 502
    assert store.children(cutout.id, kind=AssetKind.VARIATION) == []


def test_variations_without_cutout_returns_400(tmp_path):
    store = InMemoryAssetStore()
    images = str(tmp_path / "img")
    client = _make_client(store, images, FakeBackend(_png()))
    pid = client.post("/api/projects").json()["project_id"]

    r = client.post(f"/api/projects/{pid}/variations", json={"num_variants": 3})
    assert r.status_code == 400
