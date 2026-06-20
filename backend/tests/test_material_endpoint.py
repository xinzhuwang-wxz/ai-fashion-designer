import io
import os

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

import app.assets.api as api
from app.assets.api import router, set_asset_store, set_comfyui_backend
from app.assets.models import AssetKind
from app.assets.store import InMemoryAssetStore


def _png(color=(30, 30, 30)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (16, 16), color).save(buf, format="PNG")
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


def _seed_lineart(store, images_dir, pid):
    with open(os.path.join(images_dir, "la.png"), "wb") as f:
        f.write(_png())
    return store.add_asset(pid, AssetKind.LINEART, file_path="la.png")


def test_material_creates_asset_under_lineart(tmp_path):
    store = InMemoryAssetStore()
    images = str(tmp_path / "img")
    backend = FakeBackend(_png((120, 20, 40)))
    client = _make_client(store, images, backend)
    pid = client.post("/api/projects").json()["project_id"]
    la = _seed_lineart(store, images, pid)

    r = client.post(
        f"/api/projects/{pid}/material",
        json={"fabric": "silk", "color": "酒红色", "pattern": "纯色"},
    )
    assert r.status_code == 200

    mat = store.latest(pid, AssetKind.MATERIAL)
    assert mat is not None
    assert mat.parent_id == la.id
    assert mat.params["fabric"] == "silk"
    assert mat.params["color"] == "酒红色"
    # 提示词把 color/fabric 都带上了
    wf, inputs = backend.calls[0]
    assert wf == "fabric_fill_controlnet.json"
    text = next(v for n, k, v in inputs["set"] if k == "text")
    assert "酒红色" in text and "silk" in text


def test_material_without_lineart_blocked(tmp_path):
    store = InMemoryAssetStore()
    images = str(tmp_path / "img")
    client = _make_client(store, images, FakeBackend(_png()))
    pid = client.post("/api/projects").json()["project_id"]

    r = client.post(f"/api/projects/{pid}/material", json={"fabric": "denim"})
    assert r.status_code == 409
