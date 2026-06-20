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


class FakeBackend:
    def __init__(self, result: bytes):
        self.result = result
        self.calls = []

    def generate(self, workflow_name, inputs):
        self.calls.append((workflow_name, inputs))
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


def test_edit_creates_edit_version_under_render(tmp_path):
    store = InMemoryAssetStore()
    images = str(tmp_path / "img")
    backend = FakeBackend(_png((10, 60, 90)))
    client = _make_client(store, images, backend)
    pid = client.post("/api/projects").json()["project_id"]
    mat = _seed_render(store, images, pid)

    r = client.post(
        f"/api/projects/{pid}/edit",
        json={"strokes": [{"x": 0.5, "y": 0.4}], "prompt": "add a bow"},
    )
    assert r.status_code == 200
    ev = store.latest(pid, AssetKind.EDIT)
    assert ev is not None and ev.parent_id == mat.id

    wf, inputs = backend.calls[0]
    assert wf == "edit_inpaint.json"
    assert len(inputs["uploads"]) == 2  # 源图 + mask 都传了


def test_edit_without_render_blocked(tmp_path):
    store = InMemoryAssetStore()
    images = str(tmp_path / "img")
    client = _make_client(store, images, FakeBackend(_png()))
    pid = client.post("/api/projects").json()["project_id"]

    r = client.post(f"/api/projects/{pid}/edit", json={"strokes": [{"x": 0.5, "y": 0.5}]})
    assert r.status_code == 409


def test_edit_no_strokes_rejected(tmp_path):
    store = InMemoryAssetStore()
    images = str(tmp_path / "img")
    client = _make_client(store, images, FakeBackend(_png()))
    pid = client.post("/api/projects").json()["project_id"]
    _seed_render(store, images, pid)

    r = client.post(f"/api/projects/{pid}/edit", json={"strokes": []})
    assert r.status_code == 400
