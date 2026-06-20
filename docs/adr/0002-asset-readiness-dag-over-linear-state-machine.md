# 用"资产就绪度 DAG"取代线性状态机

## Status

accepted

## Context

旧 `DesignStateMachine.can_transition_to` 只比较枚举下标（`target_idx >= current_idx`），实测可从 `select` 直接跳到 `edit`；且枚举顺序 `lineart < fill < variations` 与产品所需的 `variations → lineart` 相反，变体接口又不执行状态迁移、由前端自行改 step，导致前后端状态分叉。叠加新需求"草图优先入口"（不上传照片、直接画/传线稿即可出成衣），流程本质上不再是一条直线。

## Decision

废弃线性 `Step` 枚举的顺序语义。某操作是否可执行，由"它的输入资产是否已存在于谱系"决定，而非步序下标：提线稿需要 `Selected Variation` 或 直传/手绘的 `Lineart`；试布需要 `Lineart`；完成需要 `Material` 或 `Edit Version`。后端是唯一真相源，前端只渲染"当前项目有哪些资产、下一步能做什么"。两个起点——上传照片链路（upload→cutout→variation→selected）与草图优先链路（手绘/直传 lineart）——汇合到 `Lineart` 节点。

## Consequences

- 删除按枚举下标比较的迁移逻辑；不再有"可任意前跳"。
- 前端不再自行设定 step，消除前后端分叉。
- 天然支持双入口与未来分支（同一抠图发散多轮、并行变体）。
