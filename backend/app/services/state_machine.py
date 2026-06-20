"""
工作流状态机 — 选图→抠图→变体→线稿→布料填充→实时编辑
"""
from enum import Enum
from typing import Optional
from dataclasses import dataclass, field
from datetime import datetime


class Step(str, Enum):
    SELECT = "select"          # 选图
    REMOVE_BG = "remove_bg"    # 抠图
    LINEART = "lineart"        # 提取线稿
    FILL = "fill"              # 布料填充
    VARIATIONS = "variations"  # 生成变体 (optional)
    EDIT = "edit"              # 实时编辑


@dataclass
class DesignState:
    session_id: str
    current_step: Step = Step.SELECT
    original_image: Optional[str] = None      # base64
    removed_bg_image: Optional[str] = None    # base64
    variation_images: list[str] = field(default_factory=list)
    lineart_image: Optional[str] = None       # base64
    filled_image: Optional[str] = None        # base64
    fabric_prompt: str = ""
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())


class DesignStateMachine:
    """确定性状态机，管理设计流程"""
    def __init__(self):
        self._sessions: dict[str, DesignState] = {}

    def create_session(self, session_id: str) -> DesignState:
        state = DesignState(session_id=session_id)
        self._sessions[session_id] = state
        return state

    def get_session(self, session_id: str) -> Optional[DesignState]:
        return self._sessions.get(session_id)

    def can_transition_to(self, state: DesignState, target: Step) -> bool:
        order = list(Step)
        current_idx = order.index(state.current_step)
        target_idx = order.index(target)
        return target_idx >= current_idx

    def transition(self, state: DesignState, target: Step):
        if not self.can_transition_to(state, target):
            raise ValueError(f"Cannot go from {state.current_step} to {target}")
        state.current_step = target
