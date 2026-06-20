# AI Fashion Designer — 架构与生命周期差距报告

> 版本：0.3.0 | 日期：2026-06-20 | 作者：Maxen Wang + Rina (Hermes Agent) + Codex
> 仓库：`/Users/bamboo/Githubs/ai-fashion-designer`
>
> 本文档包含方案设计与 2026-06-20 本地实测结果。状态以实测证据为准，不以代码存在或接口返回 HTTP 200 视为功能完成。

---

## 目录

1. [项目概述](#1-项目概述)
2. [宏观架构](#2-宏观架构)
3. [技术栈与复用轮子](#3-技术栈与复用轮子)
4. [工作流详解](#4-工作流详解)
5. [对照原始方案检查](#5-对照原始方案检查)
6. [完整用户生命周期差距核验](#6-完整用户生命周期差距核验)
7. [问题清单与实施优先级](#7-问题清单与实施优先级)
8. [部署指南](#8-部署指南)

---

## 1. 项目概述

AI Fashion Designer 是一个 AI 驱动的服装设计工具，在原始"服装 2D/3D 资产生成体系"方案中对应 **Phase 1：2D 创意生成**。

**核心能力**：上传参考图 → AI 自动抠图 → 提取线稿 → 布料填充 / 变体生成 → 画板实时编辑。

**定位**：设计辅助工具，让设计师快速从参考图出发，探索不同面料、风格的设计变体。

---

## 2. 宏观架构

```
┌─────────────────────────────────────────────────────────┐
│                    前端 (React + Tldraw)                   │
│  ┌──────────────────────┐  ┌──────────────────────────┐  │
│  │     Tldraw 画板       │  │    右侧操作面板            │  │
│  │  • 自由绘制            │  │  • 上传参考图              │  │
│  │  • 笔画实时追踪        │  │  • 操作按钮（线稿/填充）    │  │
│  │  • 矢量编辑            │  │  • 12 种布料选择器         │  │
│  │                       │  │  • 自定义 prompt 输入       │  │
│  │                       │  │  • 实时预览区              │  │
│  └──────────┬───────────┘  └────────────┬─────────────┘  │
│             │  WebSocket (笔画)          │  HTTP REST     │
└─────────────┼───────────────────────────┼────────────────┘
              │                           │
┌─────────────▼───────────────────────────▼────────────────┐
│                  后端 (FastAPI :8000)                      │
│                                                          │
│  ┌─────────────────┐  ┌──────────────────────────────┐   │
│  │  状态机引擎       │  │        服务层                  │   │
│  │  SELECT          │  │  remove_bg    (rembg)         │   │
│  │    ↓             │  │  lineart      (controlnet_aux)│   │
│  │  REMOVE_BG       │  │  comfyui_client               │   │
│  │    ↓             │  │  inpaint      (OpenCV)        │   │
│  │  LINEART         │  │  mask_utils                   │   │
│  │    ↓             │  │                               │   │
│  │  FILL            │  │                               │   │
│  └─────────────────┘  └──────────────┬────────────────┘   │
│                                      │                    │
│                    WebSocket (/api/ws/{id})               │
│                    • 接收笔画 → OpenCV 快速预览            │
│                    • commit → ComfyUI 高质量重绘           │
└──────────────────────────────────────┼────────────────────┘
                                       │ HTTP REST
┌──────────────────────────────────────▼────────────────────┐
│                  ComfyUI (MPS :8188)                       │
│                                                           │
│  模型：                                                    │
│  • SD 1.5 (v1-5-pruned-emaonly, 4.0 GB)                   │
│  • ControlNet v11 lineart (1.4 GB)                        │
│  • LCM-LoRA sdv1-5 (128 MB)                               │
│                                                           │
│  工作流：                                                  │
│  • variation.json          img2img 变体 (20步, ~60s)       │
│  • fabric_fill_controlnet.json  线稿→布料填充 (~60s)      │
│  • lcm_variation.json      快速变体 (4步, ~26s)           │
└───────────────────────────────────────────────────────────┘
```

---

## 3. 技术栈与复用轮子

### 3.1 前端

| 组件 | 选型 | 复用了什么 | 备注 |
|------|------|-----------|------|
| 框架 | React 18 | 社区标准 | TypeScript |
| 画板 | **Tldraw v2.4** | 完整白板 SDK（工具栏、撤销、缩放、导出） | 自带了 select/draw/eraser/arrow/text/shape 全部工具 |
| 构建 | Vite 5 | — | HMR 极快 |
| 通信 | 原生 WebSocket | — | 无额外依赖（前端 package.json 有 socket.io-client 但未使用） |
| 样式 | CSS-in-JS | — | 轻量 |

### 3.2 后端

| 组件 | 选型 | 复用了什么 | 备注 |
|------|------|-----------|------|
| 框架 | FastAPI | 异步 REST + WebSocket 原生支持 | — |
| 抠图 | **rembg** | u2net 模型 (176MB)，MPS 后端 | 一行代码去背景 |
| 线稿提取 | **controlnet_aux** | LineartDetector (lllyasviel/Annotators) | 基于 HED 边缘检测 + 神经网络 |
| 图像处理 | OpenCV + Pillow | 业界标准 | — |
| 实时预览 | **OpenCV inpaint** | `cv2.inpaint()` Telea/NS 算法 | <100ms，笔画即时反馈 |
| Mask | numpy | 羽化、融合、膨胀 | 自研 mask_utils |
| ComfyUI 客户端 | httpx | 异步 HTTP 调用 | 上传图片 / 提交工作流 / 轮询结果 / 下载输出 |

### 3.3 AI 推理

| 组件 | 选型 | 复用了什么 | 备注 |
|------|------|-----------|------|
| 推理引擎 | **ComfyUI** | 792 个节点，可视化工作流编排 | 业界标准，社区生态最大 |
| 基座模型 | **SD 1.5** (runwayml) | Stable Diffusion 最成熟版本 | 社区 LoRA/ControlNet 生态最丰富 |
| 线稿控制 | **ControlNet v11 lineart** (lllyasviel) | 线稿→条件生成 | 服装设计天然适合线稿控图 |
| 加速 | **LCM-LoRA** (latent-consistency) | 20步→4步，速度提升 2-3x | M4 上仍需 26s，未达到实时（需 GPU） |
| 后端 | MPS (Apple Silicon) | PyTorch MPS 原生支持 | 无需 CUDA，24GB 统一内存 |

### 3.4 模型镜像加速

由于 HuggingFace 被墙，模型下载统一走 **hf-mirror.com**：

```bash
export HF_ENDPOINT=https://hf-mirror.com
```

三个模型均已通过镜像下载完毕，总计 5.5GB（SD 4.0G + ControlNet 1.4G + LCM 128M）。

---

## 4. 工作流详解

### 4.1 主工作流（状态机驱动）

```
用户操作              前端                 后端                     AI 服务
───────              ────                 ────                     ──────
1. 上传参考图    →   POST /api/step/select
                    上传文件              remove_bg()
                                          rembg 抠图 ←─────────── MPS 推理 (~2s)
                                         返回 removed_bg PNG
                    显示预览, Step→REMOVE_BG

2. 点"提取线稿"  →   POST /api/step/lineart
                                          extract_lineart()
                                          controlnet_aux ←──────── 模型推理 (~5s)
                                         返回 lineart PNG
                    显示线稿, Step→LINEART

3. 选布料+点"填充" → POST /api/step/fill
                                          fabric_fill()
                                          上传线稿到 ComfyUI
                                          提交 fabric_fill_controlnet ──→ SD 1.5 + ControlNet (~60s)
                                          返回 filled PNG
                    显示结果, Step→FILL

4. 画板编辑      →   WebSocket /api/ws/{id}
                    笔画实时推送           OpenCV inpaint ←────────── 即时预览 (<100ms)
                    点"提交重绘"           ComfyUI inpainting ────→ 高质量重绘 (~60s)
```

### 4.2 变体生成工作流

```
POST /api/step/{id}/variations
  → comfyui_client.generate_variations()
    → 上传图片到 ComfyUI
    → variation.json 工作流:
      LoadImage → VAEEncode ┐
      CLIPTextEncode ───────┼→ KSampler (20步 euler) → VAEDecode → SaveImage
      CheckpointLoader ─────┘
    → 轮询等待 → 返回 base64 PNG
```

### 4.3 布料填充工作流 (ControlNet)

```
POST /api/step/{id}/fill
  → comfyui_client.fabric_fill()
    → fabric_fill_controlnet.json 工作流:
      LoadImage(lineart) ─────────┐
      ControlNetLoader ───────────┼→ ControlNetApply → KSampler → VAEDecode → SaveImage
      CLIPTextEncode(fabric) ─────┘
      EmptyLatentImage ───────────┘
```

### 4.4 实时编辑工作流

```
WebSocket 消息流:

前端画一笔 → {type: "stroke", points: [...], brush_size: 5}
  后端: fast_preview_inpaint(source_img, points)
    → 生成 mask → cv2.inpaint() → 返回 base64
  前端: 收到 {type: "preview", image: "..."} → 更新预览

前端停止绘制 300ms 后自动提交 → {type: "commit"}
  后端: fast_preview_inpaint(source_img, all_points)
    → 再次返回 OpenCV 快速预览
  后端: inpaint_with_lcm(source_img, fabric_prompt)
    → ComfyUI LCM 全图 img2img（当前未使用笔触 mask）
  前端: 收到 {type: "render", image: "...", status: "done"}
```

这里描述的是当前真实实现，不是目标方案。目标方案应把笔触转换为图像坐标下的 mask，并通过 masked inpainting 仅重绘修改区域。

---

## 5. 对照原始方案检查

原始方案出处：2026-06-11 会话，服装 2D/3D 资产生成体系 Agent Harness 方案

### Phase 1：2D 创意生成 ⚠️（技术骨架存在，产品链路未完成）

| 原始要求 | 完成状态 | 说明 |
|----------|---------|------|
| ComfyUI 本地部署 | ✅ | MPS 后端可运行，8188 端口；核验结束后因发热已停止 |
| SD + 服装 LoRA | ⚠️ | SD 1.5 已部署，**未下载专用服装 LoRA** |
| ControlNet 控制轮廓 | ⚠️ | 工作流存在，但输出完整性、材质控制和实际服装质量尚未验收 |
| IP-Adapter 品牌风格参考 | ❌ | 未实现，需要下载 IP-Adapter 模型 |
| 文字描述 → 批量概念图 | ⚠️ | 有单张变体生成，**无批量**（需改工作流） |
| 人工筛选 | ❌ | 前端能点击缩略图，但选择结果未写入后端，后续线稿仍使用原抠图 |

### Phase 2+（3D、Agent、资产管理）

| 原始要求 | 完成状态 | 说明 |
|----------|---------|------|
| Hunyuan3D-2 3D 生成 | ❌ | Phase 2 内容，需 GPU 服务器 |
| Blender headless 管线 | ❌ | Phase 3 |
| Clo3D 布料仿真 | ❌ | Phase 3（需商业许可） |
| Agent 编排 + 对话式交互 | ❌ | Phase 4 |
| 资产管理系统 | ❌ | Phase 4 |

### 额外完成的功能（超出原始 Phase 1 范围）

| 功能 | 状态 | 说明 |
|------|------|------|
| 抠图 (rembg) | ✅ | 上传自动去背景 |
| Tldraw 画板集成 | ✅ | 完整矢量编辑能力 |
| WebSocket 实时笔画 | ⚠️ | 通信和快速预览可用，但坐标没有从画布转换到原图 |
| OpenCV 快速预览 | ⚠️ | 能快速擦除/修补，不具备服装设计语义理解 |
| 12 种预置布料 prompt | ✅ | silk/denim/lace/leather/cotton/linen/wool/velvet/chiffon/brocade/embroidery/satin |
| LCM-LoRA 加速 | ⚠️ | 已接入 WebSocket commit，但执行的是无 mask 的全图 img2img |
| 状态机流程控制 | ❌ | 顺序与产品生命周期不一致，可跳步，且前后端状态会分叉 |
| CORS | ⚠️ | `localhost:3000` 可用；Vite 自动切换到 3001 或远程部署时失败 |

---

## 6. 完整用户生命周期差距核验

**核验日期**：2026-06-20  
**核验环境**：Apple Silicon M4、PyTorch MPS、FastAPI `:8000`、ComfyUI `:8188`、Vite `:3000`  
**总体结论**：当前项目是可运行的技术原型，不是完整产品复刻。六阶段生命周期约完成 35%～40%。阶段一可用，阶段二和四有模型工作流但体验与数据流不完整，阶段三存在关键断链，阶段五不是真正的区域 AI 编辑，阶段六不满足高清生产输出。

参考产品的核心交互不是单纯的“上传后依次点击按钮”，而是双画布工作台：

- 左侧：参考图、线稿或用户笔触；
- 右侧：结构一致的成衣效果；
- 用户画一笔后应看到近实时预览，停止绘制后再生成高质量局部结果；
- 除“照片上传”外，还应支持“直接画线稿 → 生成成衣”的草图优先入口。

### 6.1 生命周期逐阶段结论

| 阶段 | 目标体验 | 当前实现 | 实测/代码证据 | 判定 |
|------|----------|----------|---------------|------|
| 1. 上传抠图 | 上传带背景服装图，2-3 秒得到透明服装轮廓 | `rembg` 自动抠图 | 本地实测 1.89 秒；透明 PNG 正常生成 | 基本可用 |
| 2. 方案发散 | 生成 3 个结构稳定、细节不同的设计变体 | SD 1.5 img2img，前端有 3 个缩略图槽位 | 单张实测 48.65 秒；未使用 IP-Adapter；3 张顺序执行预计超过 2 分钟 | 技术可运行，体验不达标 |
| 3. 选择与线稿 | 选择某个变体后，从该变体提取线稿 | 前端可点击变体；后端可提取线稿 | 选择只更新前端 URL，后端仍从 `removed_bg_image` 提线稿；线稿实测 4.74 秒 | 关键流程断裂 |
| 4. 布料试穿 | 面料、颜色、图案和描述共同驱动结构一致的材质渲染 | ControlNet Lineart + 12 个英文布料 prompt | 仅有布料类型和自由文本；无独立颜色/图案参数；固定 512×768 | 半成品 |
| 5. 实时编辑 | 笔触表达设计意图，快速层近实时，停止后只重绘局部 | WebSocket + OpenCV Telea + LCM img2img | WS ping、笔画预览已通过；OpenCV 只会擦除/修补；LCM 工作流没有使用笔触 mask，实际是全图重绘 | 不是真正 AI 局部编辑 |
| 6. 完成输出 | 高清最终图、下载、参数导出、生产交付 | SD 1.5 finalize 接口 | 工作流仍是 512×768 img2img；无下载按钮、尺寸/布料参数导出、项目持久化 | 未达到产品要求 |

### 6.2 本次实际验证

| 检查 | 结果 |
|------|------|
| 前端 `npm run build` | 通过；存在主 bundle 约 1.1 MB 的体积警告 |
| 后端 `python -m compileall -q app` | 通过 |
| `GET /health` | 通过，返回 MPS 可用 |
| ComfyUI 模型枚举 | SD 1.5、ControlNet Lineart、LCM-LoRA 均可被节点发现 |
| 上传并抠图 | 通过，1.89 秒 |
| 线稿提取 | 通过，4.74 秒 |
| 单张变体 | 通过，48.65 秒 |
| WebSocket ping | 通过 |
| WebSocket 笔画快速预览 | 通过，生成有效 512×768 PNG |
| 布料填充中止 | 暴露 P0 故障：接口返回 200、生成 0 字节 PNG，并把状态推进到 `fill` |
| 最终渲染 | 因本机持续高负载和发热主动停止，未重新验证 |

核验结束后已清空 ComfyUI 队列并停止本地 `:8188` 推理进程，避免继续占用 MPS。

### 6.3 架构与数据流差距

#### 设计资产没有成为一等领域对象

当前状态只保存若干 base64 字段，缺少清晰的资产关系。产品至少需要以下可追溯资产：

```text
原始参考图
  → 抠图资产
    → 变体集合
      → 用户选中变体
        → 线稿资产
          → 材质方案
            → 编辑版本
              → 最终导出版本
```

每个资产需要 ID、父资产 ID、生成参数、模型版本、seed、尺寸、状态和文件位置。现在的 `variation_images` 未写入，选中变体也没有后端状态，因此生命周期无法可靠衔接。

#### 状态机与产品顺序不一致

`Step` 的声明顺序是：

```text
select → remove_bg → lineart → fill → variations → edit
```

产品顺序应是：

```text
select → remove_bg → variations → selected_variation → lineart → material → edit → finalize
```

当前 `can_transition_to()` 只比较枚举下标，因此从 `select` 可以直接跳到任意后续状态；同时变体接口不执行状态迁移，前端自行把 step 改成 `variations`，造成前后端状态分叉。

#### 画布坐标与图像坐标未建立变换

前端发送的是 DOM 容器内的屏幕坐标，后端直接把坐标当作原图像素。图片经过 Tldraw 缩放、平移或留白后，笔触 mask 会落在错误区域。需要显式传递图像在画布上的变换矩阵，或在前端先转换到归一化图像坐标。

#### “实时编辑”两层都未满足语义

- 快速层：`cv2.inpaint()` 只根据 mask 修补像素，不能理解“领口变小”或“增加蝴蝶结”；
- 高质量层：`lcm_variation.json` 接收整张图和文本，没有 mask 输入，不是 BrushNet/PowerPaint 类型的局部 inpainting；
- 前端每次结束笔画后 300ms 自动提交重型推理，MPS 环境下容易持续发热并形成任务堆积；
- WebSocket 高质量任务在连接处理协程中串行等待，缺少作业 ID、取消、进度、去重和过期结果丢弃。

#### 输出和生产交付能力缺失

- 工作流固定输出 512×768；
- 没有高清放大或分块重绘；
- 没有下载和格式选择；
- 没有导出面料、颜色、图案、seed、尺寸等设计参数；
- 没有项目保存、版本历史、重开会话；
- 会话仅存在进程内存中，后端重启即丢失。

### 6.4 运行与仓库问题

| 问题 | 影响 |
|------|------|
| 项目根目录不是 Git 仓库，只有内嵌 `comfyui/.git` | 无法可靠审查变更、回滚或协作 |
| 项目目录约 8.5 GB，包含 `backend/venv`、ComfyUI 源码和 5.5 GB 模型 | 难以版本管理和部署 |
| `COMFYUI_URL` 在 Docker Compose 中配置，但客户端硬编码 `http://localhost:8188` | Docker 后端无法按配置访问 ComfyUI |
| Docker 后端依赖未包含 `torch` 和 `controlnet_aux` | 干净镜像无法执行线稿能力 |
| 前端硬编码 API/WS 地址，未使用已配置的 Vite proxy | 非 localhost、HTTPS 或远程部署时容易失败 |
| CORS 只允许 `http://localhost:3000` | Vite 端口被占用后自动切到 3001 时，API 响应没有允许源 |
| 没有业务自动化测试 | 状态断链、空输出成功等问题无法回归防护 |

---

## 7. 问题清单与实施优先级

### 7.1 P0：先修正确性，再更换模型或租卡

| # | 问题 | 完成标准 |
|---|------|----------|
| 1 | 选中变体未进入后端状态 | 新增选择接口；后续线稿明确使用选中变体；自动化测试覆盖 |
| 2 | 状态机顺序错误且可任意跳步 | 用显式允许边定义迁移；前后端只使用后端返回状态 |
| 3 | 空结果仍被保存并推进状态 | 解码、非空、图片格式和尺寸校验全部通过后才能提交资产和状态 |
| 4 | 缺少任务取消与结果防串线 | 每个推理任务有 job ID、取消状态和版本号；旧结果不能覆盖新结果 |
| 5 | 画布坐标映射错误 | 任意缩放和平移下，笔触 mask 均与用户落笔位置对齐 |
| 6 | 实时编辑没有局部 mask 重绘 | 高质量工作流必须接收原图、mask、prompt，并保持 mask 外像素稳定 |
| 7 | 无业务回归测试 | 至少覆盖完整状态迁移、变体选择、空输出、取消和坐标转换 |

### 7.2 P1：形成可演示的本地 MVP

1. 建立 `DesignProject`、`DesignAsset`、`GenerationJob` 三类核心对象。
2. 支持两种入口：上传服装照片；直接上传或绘制服装线稿。
3. 将工作台固定为左侧编辑画布、右侧生成预览，并显示明确的快速预览/高质量结果状态。
4. 布料表单拆为面料、颜色、图案、自定义描述，生成参数随资产保存。
5. MPS 模式下改为手动提交或较长 idle debounce，禁止每笔自动触发重型推理。
6. 提供取消、进度、失败重试、下载 PNG 和项目恢复。
7. 将 ComfyUI URL、API URL、WS URL、允许源和输出尺寸全部环境化。
8. 初始化项目 Git 仓库，排除 venv、node_modules、模型、生成图片和 ComfyUI 内嵌仓库。

### 7.3 P2：租用 GPU 后的能力升级

1. 用 IP-Adapter 或等价参考图条件保持原始款式与品牌风格。
2. 用 BrushNet、PowerPaint 或成熟的 masked inpainting 工作流替代全图 LCM 重绘。
3. 评估 SDXL/Flux 系列与服装专用 LoRA，不再把 SD 1.5 视为最终产品基座。
4. 将 3 个变体批处理或并发生成，并支持 5-10 个方案的筛选、对比和继续分叉。
5. 增加 1024×1536 基础生成、高清修复和最终放大。
6. GPU 推理服务与业务后端分离，通过作业队列调度和限流。

### 7.4 暂不应承诺的产品指标

在完成 P0/P1 且经过真实服装数据集测试前，不应对外承诺：

- 30-60 FPS AI 生成；
- 每一笔 0.5-1 秒得到语义正确结果；
- 复杂蕾丝和褶皱均可精准保留；
- 修改区域之外绝对不变化；
- 最终图可直接用于生产；
- 上传到最终成品全过程 1-3 分钟。

当前可诚实描述为：本地 M4 可以验证抠图、线稿、基础变体、ControlNet 材质工作流和画布通信；高质量生成建议迁移到 NVIDIA GPU，但租卡不会自动修复资产流、状态机、mask 和产品交互问题。

---

## 8. 部署指南

### 8.1 本地开发启动

```bash
# 1. 启动 ComfyUI
cd /Users/bamboo/Githubs/ai-fashion-designer/comfyui
source venv/bin/activate
python main.py --listen 0.0.0.0 --port 8188

# 2. 启动后端
cd /Users/bamboo/Githubs/ai-fashion-designer/backend
HF_ENDPOINT=https://hf-mirror.com ./venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# 3. 启动前端
cd /Users/bamboo/Githubs/ai-fashion-designer/frontend
npm run dev
```

### 8.2 GPU 服务器部署（推荐用于生产）

将 ComfyUI 目录完整打包迁移到 GPU 服务器：

```bash
# 本地打包
cd /Users/bamboo/Githubs/ai-fashion-designer
tar -czf comfyui-models.tar.gz comfyui/models/ comfyui/workflows/

# 服务器上
pip install -r requirements.txt  # ComfyUI 依赖
python main.py --listen 0.0.0.0 --port 8188  # 自动用 CUDA

# 后端指向 GPU 服务器
# 注意：当前 comfyui_client.py 尚未读取该环境变量，实施部署前必须先修复
export COMFYUI_URL=http://<GPU_SERVER>:8188
```

### 8.3 Docker 部署（备选）

项目根目录有 `docker-compose.yml`，包含 backend + frontend + ComfyUI（注释掉的）：

```bash
docker compose up -d backend frontend
# ComfyUI 建议 M4 上原生跑，GPU 服务器上可 Docker 跑
```

---

## 附录 A：项目文件结构

```
ai-fashion-designer/
├── backend/
│   ├── app/
│   │   ├── api/__init__.py          # REST + WebSocket 路由
│   │   ├── main.py                  # FastAPI 入口 + CORS
│   │   ├── models/                  # (空)
│   │   ├── services/
│   │   │   ├── remove_bg.py         # rembg 抠图
│   │   │   ├── lineart.py           # controlnet_aux 线稿
│   │   │   ├── comfyui_client.py    # ComfyUI HTTP 客户端
│   │   │   ├── inpaint.py           # OpenCV 快速修补预览
│   │   │   ├── mask_utils.py        # Mask 生成工具
│   │   │   └── state_machine.py     # 工作流状态机
│   │   └── workflows/               # (空)
│   ├── venv/
│   ├── requirements.txt
│   └── Dockerfile
├── frontend/
│   ├── src/
│   │   ├── App.tsx                  # 主组件 (Tldraw + 面板)
│   │   └── main.tsx
│   ├── package.json
│   └── Dockerfile
├── comfyui/
│   ├── models/
│   │   ├── checkpoints/             # SD 1.5 (4.0 GB)
│   │   ├── controlnet/              # lineart (1.4 GB)
│   │   └── loras/                   # LCM (128 MB)
│   ├── workflows/
│   │   ├── variation.json
│   │   ├── fabric_fill_controlnet.json
│   │   └── lcm_variation.json
│   └── venv/
├── docker-compose.yml
└── docs/
    └── ARCHITECTURE.md              # 本文档
```

---

## 附录 B：推理速度基准测试

| 配置 | 步数 | 采样器 | M4 MPS 延时 | 预期 GPU 延时 |
|------|------|--------|-------------|--------------|
| SD 1.5 | 20 | euler | 60s | 3-5s |
| SD 1.5 | 4 | euler+LoRA | 25s | 1-2s |
| SD 1.5 + LCM-LoRA | 4 | lcm | 26s | 1-2s |
| SD 1.5 + LCM-LoRA | 1 | lcm | 13s | 0.5s |
| OpenCV inpaint | — | Telea | <100ms | <100ms |

**结论**：现有 SD 1.5/LCM 工作流在 M4 MPS 上无法达到演示视频中“画一笔立刻出”的体验。NVIDIA GPU 可以降低生成延迟，但仍必须先修复资产流、坐标转换、mask 局部重绘、任务取消和结果完整性；租卡本身不会解决这些产品与架构问题。
