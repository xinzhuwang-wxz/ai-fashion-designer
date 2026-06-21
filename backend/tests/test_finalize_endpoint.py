import io
import os

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

import app.assets.api as api
from app.assets.api import router, set_asset_store
from app.assets.models import AssetKind
from app.assets.store import InMemoryAssetStore


def _png(size=(64, 96)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, (40, 60, 90)).save(buf, format="PNG")
    return buf.getvalue()


def _client(store, images_dir):
    api.IMAGES_DIR = images_dir
    os.makedirs(images_dir, exist_ok=True)
    set_asset_store(store)
    app = FastAPI()
    app.include_router(router, prefix="/api")
    return TestClient(app)


def test_finalize_creates_final_asset_2x(tmp_path):
    store = InMemoryAssetStore()
    images = str(tmp_path / "img")
    client = _client(store, images)
    pid = client.post("/api/projects").json()["project_id"]
    with open(os.path.join(images, "mat.png"), "wb") as f:
        f.write(_png((64, 96)))
    mat = store.add_asset(
        pid, AssetKind.MATERIAL, file_path="mat.png", params={"fabric": "silk"}, seed=42
    )

    r = client.post(f"/api/projects/{pid}/finalize")
    assert r.status_code == 200
    body = r.json()["final"]
    assert body["width"] == 128 and body["height"] == 192  # 2x 高清
    final = store.latest(pid, AssetKind.FINAL)
    assert final is not None
    assert final.parent_id == mat.id
    assert final.seed == 42  # 设计参数随谱系延续


def test_finalize_without_render_409(tmp_path):
    store = InMemoryAssetStore()
    images = str(tmp_path / "img")
    client = _client(store, images)
    pid = client.post("/api/projects").json()["project_id"]

    r = client.post(f"/api/projects/{pid}/finalize")
    assert r.status_code == 409
