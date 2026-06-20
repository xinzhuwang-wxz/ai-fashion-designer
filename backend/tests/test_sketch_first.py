import io

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

import app.assets.api as api
from app.assets.api import router, set_asset_store
from app.assets.models import AssetKind
from app.assets.store import InMemoryAssetStore


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (16, 16), (255, 255, 255)).save(buf, format="PNG")
    return buf.getvalue()


def _client(store, images_dir):
    api.IMAGES_DIR = images_dir
    set_asset_store(store)
    app = FastAPI()
    app.include_router(router, prefix="/api")
    return TestClient(app)


def test_lineart_image_creates_lineart_asset_without_photo(tmp_path):
    """草图优先：直接上传线稿图 → Lineart 资产（parent=None，无需先传照片）。"""
    store = InMemoryAssetStore()
    client = _client(store, str(tmp_path / "img"))
    pid = client.post("/api/projects").json()["project_id"]

    r = client.post(
        f"/api/projects/{pid}/lineart-image",
        files={"file": ("sketch.png", _png(), "image/png")},
    )
    assert r.status_code == 200
    la = store.latest(pid, AssetKind.LINEART)
    assert la is not None
    assert la.parent_id is None
    # 之后即可直接试布（无需抠图/变体）
    assert store.latest(pid, AssetKind.CUTOUT) is None


def test_lineart_image_rejects_invalid(tmp_path):
    store = InMemoryAssetStore()
    client = _client(store, str(tmp_path / "img"))
    pid = client.post("/api/projects").json()["project_id"]

    r = client.post(
        f"/api/projects/{pid}/lineart-image",
        files={"file": ("x.png", b"not-an-image", "image/png")},
    )
    assert r.status_code == 422
