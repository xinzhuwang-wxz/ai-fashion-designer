# 以资产谱系图（DesignAsset lineage）+ SQLite 作为核心领域模型

## Status

accepted

## Context

旧实现把每一步产物（抠图、线稿、填充图）存为内存中 `DesignState` 对象上的 base64 字段，资产没有身份、没有父子关系、没有持久化。2026-06-20 的实测证据表明这一形状直接造成四个缺陷：选中变体不进后端态（线稿永远从 `removed_bg` 提取，场景 3 链路断裂）、空 ComfyUI 结果仍被存为 0 字节文件并推进状态、前后端状态分叉、后端重启即丢全部会话。

## Decision

引入一等领域对象 `DesignAsset(id, parent_id, kind, params, seed, model, status, file_path)`，把设计流程建模为以资产为节点的谱系图（lineage），`GenerationJob` 记录每次推理的运行元数据，全部落 SQLite 持久化。状态机与前端不再依赖散落的 base64 字段，而是依赖资产的身份、kind 与父链。

## Considered Options

- **轻量增量补字段**（保留 `DesignState`，仅加 `selected_variation` + 空输出校验）：最快见效，但谱系/可追溯/seed/版本仍然脆弱，场景 3/6 长期不稳——否决。
- **全事件溯源**（每步生成不可变事件，状态由回放得出）：可追溯/可回放/可撤销最强，但实现与心智成本对单机原型过重——否决，留作未来若需操作历史再升级。

## Consequences

- 状态机改为按"资产谱系是否就绪"驱动，而非枚举下标比较（见后续 ADR）。
- 前端只认后端返回的资产 ID / URL，消除前后端状态分叉。
- 新增 SQLite schema 与迁移；资产文件与元数据分离存储。
- "选中变体"成为显式写入后端的资产关系，场景 3 链路打通。
