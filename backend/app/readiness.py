"""ReadinessGate —— 操作可执行性由"输入资产是否就绪"决定（ADR-0002）。

取代旧的按枚举下标比较的状态机；防跳步、防前后端状态分叉。
"""
from __future__ import annotations

from dataclasses import dataclass

from app.assets.models import AssetKind
from app.assets.store import AssetStore


@dataclass
class Decision:
    allowed: bool
    reason: str = ""


class ReadinessGate:
    def __init__(self, store: AssetStore):
        self.store = store

    def can(self, operation: str, project_id: str) -> Decision:
        if operation == "variations":
            if self.store.latest(project_id, AssetKind.CUTOUT):
                return Decision(True)
            return Decision(False, "需要先上传参考图得到 Cutout")

        if operation == "lineart":
            # 线稿来源：选中的变体（草图优先直传 Lineart 在 #8 扩展）
            if self.store.get_selected_variation(project_id):
                return Decision(True)
            return Decision(False, "需要先选中一个变体")

        return Decision(False, f"未知操作: {operation}")
