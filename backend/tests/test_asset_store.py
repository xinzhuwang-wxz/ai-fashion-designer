import pytest

from app.assets.models import AssetKind
from app.assets.store import InMemoryAssetStore, SqliteAssetStore


@pytest.fixture(params=["memory", "sqlite"])
def store(request, tmp_path):
    if request.param == "memory":
        return InMemoryAssetStore()
    return SqliteAssetStore(str(tmp_path / "conf.db"))


def test_cutout_records_kind_and_parent():
    """Cutout 资产应记录正确的 kind 与父资产（上传）。"""
    store = InMemoryAssetStore()
    project = store.create_project()
    upload = store.add_asset(project.id, AssetKind.UPLOAD, file_path="orig.png")
    cutout = store.add_asset(
        project.id, AssetKind.CUTOUT, parent_id=upload.id, file_path="cut.png"
    )

    fetched = store.get_asset(cutout.id)
    assert fetched is not None
    assert fetched.kind == AssetKind.CUTOUT
    assert fetched.parent_id == upload.id
    assert fetched.project_id == project.id


def test_latest_returns_most_recent_of_kind():
    """latest 返回该项目中某 kind 最近加入的资产。"""
    store = InMemoryAssetStore()
    project = store.create_project()
    store.add_asset(project.id, AssetKind.CUTOUT, file_path="a.png")
    second = store.add_asset(project.id, AssetKind.CUTOUT, file_path="b.png")

    latest = store.latest(project.id, AssetKind.CUTOUT)
    assert latest is not None
    assert latest.id == second.id


def test_children_returns_derived_assets_filtered_by_kind():
    """children 返回某资产的直接子资产，可按 kind 过滤。"""
    store = InMemoryAssetStore()
    project = store.create_project()
    upload = store.add_asset(project.id, AssetKind.UPLOAD, file_path="o.png")
    cutout = store.add_asset(
        project.id, AssetKind.CUTOUT, parent_id=upload.id, file_path="c.png"
    )
    store.add_asset(
        project.id, AssetKind.LINEART, parent_id=cutout.id, file_path="l.png"
    )

    kids = store.children(upload.id, kind=AssetKind.CUTOUT)
    assert [a.id for a in kids] == [cutout.id]


def test_sqlite_store_persists_across_reopen(tmp_path):
    """SQLite adapter：重开同一 db 后项目与资产仍可读（重启恢复）。"""
    db = str(tmp_path / "assets.db")
    store = SqliteAssetStore(db)
    project = store.create_project()
    upload = store.add_asset(project.id, AssetKind.UPLOAD, file_path="o.png")
    cutout = store.add_asset(
        project.id, AssetKind.CUTOUT, parent_id=upload.id, file_path="c.png"
    )

    reopened = SqliteAssetStore(db)
    again = reopened.get_asset(cutout.id)
    assert again is not None
    assert again.kind == AssetKind.CUTOUT
    assert again.parent_id == upload.id
    assert reopened.get_project(project.id) is not None
    latest = reopened.latest(project.id, AssetKind.CUTOUT)
    assert latest is not None and latest.id == cutout.id


def test_both_adapters_satisfy_same_lineage_contract(store):
    """内存与 SQLite adapter 对同一谱系行为给出相同结果。"""
    project = store.create_project()
    upload = store.add_asset(project.id, AssetKind.UPLOAD, file_path="o.png")
    c1 = store.add_asset(
        project.id, AssetKind.CUTOUT, parent_id=upload.id, file_path="c1.png"
    )
    c2 = store.add_asset(
        project.id, AssetKind.CUTOUT, parent_id=upload.id, file_path="c2.png"
    )

    assert store.get_asset(c1.id).parent_id == upload.id
    assert store.latest(project.id, AssetKind.CUTOUT).id == c2.id
    assert [a.id for a in store.children(upload.id, kind=AssetKind.CUTOUT)] == [
        c1.id,
        c2.id,
    ]
    assert store.get_project(project.id) is not None
