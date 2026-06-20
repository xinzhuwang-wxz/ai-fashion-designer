from app.assets.models import AssetKind
from app.assets.store import InMemoryAssetStore
from app.readiness import ReadinessGate


def test_variations_requires_cutout():
    store = InMemoryAssetStore()
    gate = ReadinessGate(store)
    p = store.create_project()
    assert gate.can("variations", p.id).allowed is False
    store.add_asset(p.id, AssetKind.CUTOUT)
    assert gate.can("variations", p.id).allowed is True


def test_lineart_allowed_from_cutout_or_selected_variation():
    store = InMemoryAssetStore()
    gate = ReadinessGate(store)
    p = store.create_project()

    # 空项目不允许
    assert gate.can("lineart", p.id).allowed is False
    # 有抠图即可（自动流：上传即出线稿）
    store.add_asset(p.id, AssetKind.CUTOUT)
    assert gate.can("lineart", p.id).allowed is True


def test_material_requires_lineart():
    store = InMemoryAssetStore()
    gate = ReadinessGate(store)
    p = store.create_project()
    assert gate.can("material", p.id).allowed is False
    store.add_asset(p.id, AssetKind.LINEART)
    assert gate.can("material", p.id).allowed is True


def test_cannot_skip_to_lineart_from_empty_project():
    store = InMemoryAssetStore()
    gate = ReadinessGate(store)
    p = store.create_project()
    assert gate.can("lineart", p.id).allowed is False
