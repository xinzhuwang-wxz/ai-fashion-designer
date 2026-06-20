from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.assets.api as api
from app.assets.api import router, set_asset_store
from app.assets.models import AssetKind
from app.assets.store import InMemoryAssetStore


def _client(store, images_dir):
    api.IMAGES_DIR = images_dir
    set_asset_store(store)
    app = FastAPI()
    app.include_router(router, prefix="/api")
    return TestClient(app)


def test_export_returns_latest_of_each_kind_with_params(tmp_path):
    store = InMemoryAssetStore()
    client = _client(store, str(tmp_path / "img"))
    pid = client.post("/api/projects").json()["project_id"]

    cut = store.add_asset(pid, AssetKind.CUTOUT, file_path="c.png")
    la = store.add_asset(pid, AssetKind.LINEART, parent_id=cut.id, file_path="l.png")
    store.add_asset(
        pid,
        AssetKind.MATERIAL,
        parent_id=la.id,
        file_path="m.png",
        params={"fabric": "silk", "color": "酒红色"},
        seed=123,
    )

    r = client.get(f"/api/projects/{pid}/export")
    assert r.status_code == 200
    body = r.json()
    assert body["project_id"] == pid
    assert body["assets"]["cutout"]["url"] == "/api/images/c.png"
    assert body["assets"]["material"]["seed"] == 123
    assert body["assets"]["material"]["params"]["fabric"] == "silk"


def test_export_unknown_project_404(tmp_path):
    store = InMemoryAssetStore()
    client = _client(store, str(tmp_path / "img"))
    r = client.get("/api/projects/nope/export")
    assert r.status_code == 404
