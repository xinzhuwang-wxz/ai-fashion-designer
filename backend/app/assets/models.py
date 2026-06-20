"""资产谱系领域对象 — DesignProject / DesignAsset（ADR-0001）。

术语遵循 CONTEXT.md：每一步产物都是有身份、有父链的 DesignAsset。
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class AssetKind(str, Enum):
    UPLOAD = "upload"
    CUTOUT = "cutout"
    VARIATION = "variation"
    LINEART = "lineart"
    MATERIAL = "material"
    EDIT = "edit"
    FINAL = "final"


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


@dataclass
class DesignProject:
    id: str = field(default_factory=lambda: _new_id("proj"))
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class DesignAsset:
    project_id: str
    kind: AssetKind
    id: str = field(default_factory=lambda: _new_id("asset"))
    parent_id: Optional[str] = None
    params: dict = field(default_factory=dict)
    seed: Optional[int] = None
    model: Optional[str] = None
    status: str = "ready"
    file_path: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
