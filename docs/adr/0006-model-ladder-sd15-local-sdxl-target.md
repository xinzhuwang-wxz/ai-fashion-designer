# 模型阶梯：本地 SD1.5 跑链路，目标基座 SDXL（租卡后）

## Status

accepted

## Context

本地已下好 SD1.5（4.0G）+ ControlNet-lineart + LCM-LoRA，足以在 M4 上验证链路，但 SD1.5 画质天花板低，难逼近截图的成衣质量。需要先定目标基座，才能让现在写的 ComfyUI 工作流朝正确方向抽象，避免做一次性废工作流。Flux 画质上限最高但 VRAM/算力需求大（24–48G）、ControlNet/IP-Adapter/inpaint 生态较新、租卡更贵。

## Decision

采用渐进阶梯：

- **本地（M4）**：SD1.5，仅验证链路正确性。
- **租卡（GPU）目标基座 = SDXL** + 服装 LoRA + IP-Adapter（保留原款/品牌风格）+ BrushNet/PowerPaint（局部 inpaint）+ 高清放大到 1024×1536+，目标卡 16–24G（4090/A10 级）。

ComfyUI 工作流与客户端按"基座模型名 + 工作流文件可替换、ControlNet/IP-Adapter/inpaint 可插拔"设计，换基座只换工作流与模型名。

## Considered Options

- **直接上 Flux**：画质上限最高，但成本/VRAM/生态新，落地风险高——否决，留作未来更高质量需求时 revisit。
- **留在 SD1.5 仅提分辨率**：最省卡但质量天花板低，"美不美"难达截图——否决。

## Consequences

- 工作流参数化（模型名 + 工作流文件），SD1.5 与 SDXL 工作流并存。
- IP-Adapter / BrushNet / 高清放大归入租卡后阶段（见 ADR-0004），本地不阻塞。
