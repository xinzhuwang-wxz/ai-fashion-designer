# 实时编辑 = 笔触驱动的局部 masked 重绘（非全图重绘）

## Status

accepted

## Context

场景 5/6 与抖音截图要求"画一笔 → 只改局部、mask 外保持不变、近实时反馈"。旧实现是 LCM **全图** img2img（无 mask 输入，改一处会让脸/背景/其它部位一起漂移）、前端把**屏幕坐标直接当原图像素**（Tldraw 缩放/平移后 mask 错位）、且每笔停画 300ms **自动触发重型推理**（MPS 持续发热、任务堆积、结果串线）。

## Decision

- **坐标（必修，非可选）**：前端传"图像归一化坐标 + 画布→图像变换矩阵"，后端据此生成 mask；任意缩放/平移下 mask 都与落笔对齐。
- **HQ 层**：masked inpainting，只重绘 mask 区域、mask 外像素逐像素稳定。接口稳定不变；本地用 SD / ControlNet-inpaint 先跑通"局部重绘正确性"，租卡后把工作流换成 BrushNet / PowerPaint 提升质量。
- **快速预览层**：本地保留 OpenCV 占位（无语义，仅给即时视觉反馈）；租卡后可替换为 StreamDiffusion 实时层。
- **提交时机**：M4 上改为"停笔长 debounce 或手动『应用』"，禁止每笔自动触发重型推理。HQ 任务需带 job id、可取消、版本号，旧结果不得覆盖新结果。

## Consequences

- 本地（M4）只能验证**局部重绘的正确性**（只改 mask、其余不变）。**做不到截图里 0.5–1s/笔、30–60fps 的实时速度——那是 GPU + StreamDiffusion/BrushNet 阶段的交付**。
- 本地阶段不对外承诺实时帧率；速度与最高画质归入租卡后的第二阶段（见 ADR-0004）。
