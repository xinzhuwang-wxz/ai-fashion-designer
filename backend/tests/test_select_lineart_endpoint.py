import io
import os
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

import app.assets.api as api
from app.assets.api import router, set_asset_store
from app.assets.models import AssetKind
from app.assets.store import InMemoryAssetStore


def _png(color=(200, 200, 200)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (16, 16), color).save(buf, format="PNG")
    return buf.getvalue()


def _make_client(store, images_dir):
    api.IMAGES_DIR = images_dir
    os.makedirs(images_dir, exist_ok=True)
    set_asset_store(store)
    app = FastAPI()
    app.include_router(router, prefix="/api")
    return TestClient(app)


def _seed_variations(store, images_dir, pid):
    cut = store.add_asset(pid, AssetKind.CUTOUT, file_path="cut.png")
    for name in ("v1.png", "v2.png"):
        with open(os.path.join(images_dir, name), "wb") as f:
            f.write(_png())
    v1 = store.add_asset(pid, AssetKind.VARIATION, parent_id=cut.id, file_path="v1.png")
    v2 = store.add_asset(pid, AssetKind.VARIATION, parent_id=cut.id, file_path="v2.png")
    return cut, v1, v2


def test_lineart_derives_from_selected_variation(tmp_path):
    """核心修复：选中 v2 后，Lineart 的 parent 必须是 v2（不是 Cutout）。"""
    store = InMemoryAssetStore()
    images = str(tmp_path / "img")
    client = _make_client(store, images)
    pid = client.post("/api/projects").json()["project_id"]
    cut, v1, v2 = _seed_variations(store, images, pid)

    r = client.post(f"/api/projects/{pid}/select-variation", json={"variation_id": v2.id})
    assert r.status_code == 200

    with patch("app.assets.api.extract_lineart", return_value=Image.new("RGB", (16, 16), (250, 250, 250))):
        r = client.post(f"/api/projects/{pid}/lineart")
    assert r.status_code == 200

    la = store.latest(pid, AssetKind.LINEART)
    assert la is not None
    assert la.parent_id == v2.id
    assert la.parent_id != cut.id


def test_lineart_blocked_without_selection(tmp_path):
    """有变体但未选中 → 就绪门拦截（409）。"""
    store = InMemoryAssetStore()
    images = str(tmp_path / "img")
    client = _make_client(store, images)
    pid = client.post("/api/projects").json()["project_id"]
    _seed_variations(store, images, pid)  # 不选中

    r = client.post(f"/api/projects/{pid}/lineart")
    assert r.status_code == 409


def test_select_non_variation_rejected(tmp_path):
    store = InMemoryAssetStore()
    images = str(tmp_path / "img")
    client = _make_client(store, images)
    pid = client.post("/api/projects").json()["project_id"]
    cut, v1, v2 = _seed_variations(store, images, pid)

    r = client.post(f"/api/projects/{pid}/select-variation", json={"variation_id": cut.id})
    assert r.status_code == 400
