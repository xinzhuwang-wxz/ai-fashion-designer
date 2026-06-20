from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.assets.api import router, set_asset_store
from app.assets.models import AssetKind
from app.assets.store import InMemoryAssetStore, SqliteAssetStore


def make_client(store, images_dir):
    import app.assets.api as api

    api.IMAGES_DIR = images_dir
    set_asset_store(store)
    app = FastAPI()
    app.include_router(router, prefix="/api")
    return TestClient(app)


def test_upload_creates_cutout_asset_and_serves_it(tmp_path):
    """上传照片 → 建 Cutout 资产（parent=上传），返回 URL，文件可取回。"""
    import io

    from PIL import Image

    store = InMemoryAssetStore()
    client = make_client(store, str(tmp_path))

    pid = client.post("/api/projects").json()["project_id"]

    raw_buf = io.BytesIO()
    Image.new("RGB", (32, 48), (120, 30, 30)).save(raw_buf, format="PNG")
    cut_buf = io.BytesIO()
    Image.new("RGBA", (32, 48), (0, 0, 0, 0)).save(cut_buf, format="PNG")
    cutout_png = cut_buf.getvalue()

    with patch("app.assets.api.remove_background", return_value=cutout_png):
        resp = client.post(
            f"/api/projects/{pid}/upload",
            files={"file": ("photo.png", raw_buf.getvalue(), "image/png")},
        )

    assert resp.status_code == 200
    cutout_url = resp.json()["cutout"]["url"]
    assert cutout_url.startswith("/api/")

    # 谱系：cutout 在该项目下，kind 正确，parent 是上传资产
    cutout = store.latest(pid, AssetKind.CUTOUT)
    assert cutout is not None
    assert cutout.kind == AssetKind.CUTOUT
    upload = store.get_asset(cutout.parent_id)
    assert upload is not None and upload.kind == AssetKind.UPLOAD

    # 返回的图片可取回且与抠图结果一致
    got = client.get(cutout_url)
    assert got.status_code == 200
    assert got.content == cutout_png


def test_upload_rejects_empty_file_without_creating_assets(tmp_path):
    """空/坏上传被拒，且不留下任何资产（CLAUDE.md 红线 + ADR-0001）。"""
    store = InMemoryAssetStore()
    client = make_client(store, str(tmp_path / "img"))
    pid = client.post("/api/projects").json()["project_id"]

    resp = client.post(
        f"/api/projects/{pid}/upload", files={"file": ("e.png", b"", "image/png")}
    )
    assert resp.status_code == 422
    assert store.latest(pid, AssetKind.UPLOAD) is None
    assert store.latest(pid, AssetKind.CUTOUT) is None


def test_upload_rejects_empty_cutout_result_without_creating_cutout(tmp_path):
    """rembg 返回空结果时不得创建 Cutout 资产（0 字节污染红线）。"""
    import io

    from PIL import Image

    store = InMemoryAssetStore()
    client = make_client(store, str(tmp_path / "img"))
    pid = client.post("/api/projects").json()["project_id"]
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (1, 2, 3)).save(buf, format="PNG")

    with patch("app.assets.api.remove_background", return_value=b""):
        resp = client.post(
            f"/api/projects/{pid}/upload",
            files={"file": ("p.png", buf.getvalue(), "image/png")},
        )
    assert resp.status_code == 502
    assert store.latest(pid, AssetKind.CUTOUT) is None


def test_sqlite_store_usable_from_request_worker_thread(tmp_path):
    """SqliteAssetStore 在请求线程（≠创建线程）中可用——回归 check_same_thread。"""
    store = SqliteAssetStore(str(tmp_path / "a.db"))
    client = make_client(store, str(tmp_path / "img"))
    resp = client.post("/api/projects")
    assert resp.status_code == 200
    assert resp.json()["project_id"]
