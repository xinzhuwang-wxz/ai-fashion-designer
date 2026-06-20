"""AssetStore — 资产谱系存储的 seam（ADR-0001）。

接口小而稳；背后可换 adapter（内存 / SQLite）。本文件先提供内存实现，
SQLite 实现在后续 cycle 加入，两者满足同一行为契约。
"""
from __future__ import annotations

import json
import sqlite3
import threading
from typing import Optional, Protocol

from app.assets.models import AssetKind, DesignAsset, DesignProject


class AssetStore(Protocol):
    """资产谱系存储的接口（seam）。内存与 SQLite adapter 都满足它。"""

    def create_project(self) -> DesignProject: ...

    def get_project(self, project_id: str) -> Optional[DesignProject]: ...

    def add_asset(
        self,
        project_id: str,
        kind: AssetKind,
        *,
        parent_id: Optional[str] = None,
        params: Optional[dict] = None,
        seed: Optional[int] = None,
        model: Optional[str] = None,
        file_path: Optional[str] = None,
    ) -> DesignAsset: ...

    def get_asset(self, asset_id: str) -> Optional[DesignAsset]: ...

    def latest(self, project_id: str, kind: AssetKind) -> Optional[DesignAsset]: ...

    def children(
        self, asset_id: str, kind: Optional[AssetKind] = None
    ) -> list[DesignAsset]: ...

    def select_variation(self, project_id: str, asset_id: str) -> None: ...

    def get_selected_variation(self, project_id: str) -> Optional[DesignAsset]: ...


class InMemoryAssetStore:
    def __init__(self) -> None:
        self._projects: dict[str, DesignProject] = {}
        self._assets: dict[str, DesignAsset] = {}
        self._selected: dict[str, str] = {}

    def create_project(self) -> DesignProject:
        project = DesignProject()
        self._projects[project.id] = project
        return project

    def get_project(self, project_id: str) -> Optional[DesignProject]:
        return self._projects.get(project_id)

    def add_asset(
        self,
        project_id: str,
        kind: AssetKind,
        *,
        parent_id: Optional[str] = None,
        params: Optional[dict] = None,
        seed: Optional[int] = None,
        model: Optional[str] = None,
        file_path: Optional[str] = None,
    ) -> DesignAsset:
        asset = DesignAsset(
            project_id=project_id,
            kind=kind,
            parent_id=parent_id,
            params=params or {},
            seed=seed,
            model=model,
            file_path=file_path,
        )
        self._assets[asset.id] = asset
        return asset

    def get_asset(self, asset_id: str) -> Optional[DesignAsset]:
        return self._assets.get(asset_id)

    def latest(self, project_id: str, kind: AssetKind) -> Optional[DesignAsset]:
        match = [
            a
            for a in self._assets.values()
            if a.project_id == project_id and a.kind == kind
        ]
        return match[-1] if match else None

    def children(
        self, asset_id: str, kind: Optional[AssetKind] = None
    ) -> list[DesignAsset]:
        return [
            a
            for a in self._assets.values()
            if a.parent_id == asset_id and (kind is None or a.kind == kind)
        ]

    def select_variation(self, project_id: str, asset_id: str) -> None:
        self._selected[project_id] = asset_id

    def get_selected_variation(self, project_id: str) -> Optional[DesignAsset]:
        asset_id = self._selected.get(project_id)
        return self._assets.get(asset_id) if asset_id else None


def _row_to_asset(row: sqlite3.Row) -> DesignAsset:
    return DesignAsset(
        project_id=row["project_id"],
        kind=AssetKind(row["kind"]),
        id=row["id"],
        parent_id=row["parent_id"],
        params=json.loads(row["params"]) if row["params"] else {},
        seed=row["seed"],
        model=row["model"],
        status=row["status"],
        file_path=row["file_path"],
        created_at=row["created_at"],
    )


class SqliteAssetStore:
    """AssetStore 的 SQLite adapter —— 与 InMemoryAssetStore 同一行为契约。

    rowid 保证插入顺序，用于 latest/children 排序。
    """

    def __init__(self, db_path: str) -> None:
        # check_same_thread=False：FastAPI 在 worker 线程执行端点，连接需跨线程；
        # 用 _lock 串行化访问保证安全（本地阶段单用户，足够）。
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS projects ("
            "id TEXT PRIMARY KEY, created_at TEXT)"
        )
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS assets ("
            "id TEXT PRIMARY KEY, project_id TEXT, kind TEXT, parent_id TEXT, "
            "params TEXT, seed INTEGER, model TEXT, status TEXT, "
            "file_path TEXT, created_at TEXT)"
        )
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS selections ("
            "project_id TEXT PRIMARY KEY, variation_id TEXT)"
        )
        self._conn.commit()

    def create_project(self) -> DesignProject:
        project = DesignProject()
        with self._lock:
            self._conn.execute(
                "INSERT INTO projects (id, created_at) VALUES (?, ?)",
                (project.id, project.created_at),
            )
            self._conn.commit()
        return project

    def get_project(self, project_id: str) -> Optional[DesignProject]:
        with self._lock:
            row = self._conn.execute(
                "SELECT id, created_at FROM projects WHERE id = ?", (project_id,)
            ).fetchone()
        if row is None:
            return None
        return DesignProject(id=row["id"], created_at=row["created_at"])

    def add_asset(
        self,
        project_id: str,
        kind: AssetKind,
        *,
        parent_id: Optional[str] = None,
        params: Optional[dict] = None,
        seed: Optional[int] = None,
        model: Optional[str] = None,
        file_path: Optional[str] = None,
    ) -> DesignAsset:
        asset = DesignAsset(
            project_id=project_id,
            kind=kind,
            parent_id=parent_id,
            params=params or {},
            seed=seed,
            model=model,
            file_path=file_path,
        )
        with self._lock:
            self._conn.execute(
                "INSERT INTO assets (id, project_id, kind, parent_id, params, seed, "
                "model, status, file_path, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    asset.id,
                    asset.project_id,
                    asset.kind.value,
                    asset.parent_id,
                    json.dumps(asset.params),
                    asset.seed,
                    asset.model,
                    asset.status,
                    asset.file_path,
                    asset.created_at,
                ),
            )
            self._conn.commit()
        return asset

    def get_asset(self, asset_id: str) -> Optional[DesignAsset]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM assets WHERE id = ?", (asset_id,)
            ).fetchone()
        return _row_to_asset(row) if row else None

    def latest(self, project_id: str, kind: AssetKind) -> Optional[DesignAsset]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM assets WHERE project_id = ? AND kind = ? "
                "ORDER BY rowid DESC LIMIT 1",
                (project_id, kind.value),
            ).fetchone()
        return _row_to_asset(row) if row else None

    def children(
        self, asset_id: str, kind: Optional[AssetKind] = None
    ) -> list[DesignAsset]:
        with self._lock:
            if kind is None:
                rows = self._conn.execute(
                    "SELECT * FROM assets WHERE parent_id = ? ORDER BY rowid",
                    (asset_id,),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM assets WHERE parent_id = ? AND kind = ? "
                    "ORDER BY rowid",
                    (asset_id, kind.value),
                ).fetchall()
        return [_row_to_asset(r) for r in rows]

    def select_variation(self, project_id: str, asset_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO selections (project_id, variation_id) "
                "VALUES (?, ?)",
                (project_id, asset_id),
            )
            self._conn.commit()

    def get_selected_variation(self, project_id: str) -> Optional[DesignAsset]:
        with self._lock:
            row = self._conn.execute(
                "SELECT variation_id FROM selections WHERE project_id = ?",
                (project_id,),
            ).fetchone()
        return self.get_asset(row["variation_id"]) if row else None
