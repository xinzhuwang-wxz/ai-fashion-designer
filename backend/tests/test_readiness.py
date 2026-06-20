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


def test_lineart_requires_selected_variation():
    store = InMemoryAssetStore()
    gate = ReadinessGate(store)
    p = store.create_project()
    cut = store.add_asset(p.id, AssetKind.CUTOUT)
    v = store.add_asset(p.id, AssetKind.VARIATION, parent_id=cut.id)

    # 有变体但未选中 → 不允许提线稿（防跳步/防"选了不生效"）
    assert gate.can("lineart", p.id).allowed is False
    store.select_variation(p.id, v.id)
    assert gate.can("lineart", p.id).allowed is True


def test_cannot_skip_to_lineart_from_empty_project():
    store = InMemoryAssetStore()
    gate = ReadinessGate(store)
    p = store.create_project()
    assert gate.can("lineart", p.id).allowed is False
