# AI 服装设计工具 — 端到端设计实现笔记

> 本文是整个项目从产品到代码的全景图。术语以 [CONTEXT.md](../CONTEXT.md) 为准，决策见 [adr/](adr/)。

---

## 1. 产品本质（一句话）

**左画板改线稿、右画布实时跟着把整件成衣渲出来**——画师只动左边，右边自动跟。核心壁垒是**实时局部渲染**：改一小块只重渲那一块、其余像素级不动；并且要**既遵循画师笔触、又把笔触映射成服装语言**（画兜出兜、画竖线出褶）。

完整生命周期（6 场景）：
1. **上传参考图** → 抠图 `Cutout`
2. **方案发散** → N 个 `Variation`
3. **选变体 → 提线稿** `Lineart`
4. **布料试穿** → `Material`（Rendered Garment）
5. **实时局部编辑** → `EditVersion` / 实时帧
6. **完成定稿** → `Final`（高清）+ 下载/导出/恢复

---

## 2. 系统架构（三层）

```
前端 React + Vite (:6006 单端口)         后端 FastAPI (:6006)              ComfyUI (:8188, CUDA)
┌─────────────────────────┐   HTTP    ┌──────────────────────────┐  HTTP  ┌────────────────────┐
│ 双画布工作台 App.tsx       │ ───────▶ │ 资产 REST + 图片服务        │ ─────▶ │ SDXL + LCM-LoRA-SDXL │
│ 左:PaintCanvas(像素画板)   │          │ AssetStore(SQLite 谱系)    │        │ + union/scribble CN │
│ 右:成衣只读 <img>          │ ◀─────── │ GenerationJob(空输出校验)   │ ◀───── │ + VAEEncodeForInpaint│
│ useProject(镜像后端资产)    │   帧URL   │ ReadinessGate(就绪DAG)     │  图片  │ 12 工作流 JSON       │
└─────────────────────────┘          │ CLIP 意图识别 / 抠图 / 线稿  │        └────────────────────┘
                                     └──────────────────────────┘
```

- **单端口部署**：前端 build 进 `frontend/dist`，后端 `main.py` 用 StaticFiles 挂 `/` 一并伺服 → 一个 6006 端口 = 完整网页。
- **运行环境**：AutoDL 4090（租用）。本机 SSH 隧道 `localhost:6006` 访问（不过公网）。详见 [memory/gpu-deployment](../../.claude 或 项目记忆)。
- 后端用 `run_in_threadpool` 把同步推理（httpx→ComfyUI、CLIP、rembg、controlnet_aux）丢线程池，不阻塞事件循环。

---

## 3. 领域模型 — 资产谱系（ADR-0001/0002）

一切产物是 **`DesignAsset`**，挂在 **`DesignProject`** 下，靠 `parent_id` 连成**谱系 DAG**（取代散落的 base64 字段与线性状态机）。

```
Upload ──▶ Cutout ──▶ Lineart ──▶ Variation* ──▶ Material ──▶ Edit* ──▶ Final
            (rembg)   (controlnet_aux)  (ControlNet)  (ControlNet)  (inpaint)  (2x放大)
```

- **`AssetKind`**：`upload / cutout / variation / lineart / material / edit / final`（`backend/app/assets/models.py`）。
- **`AssetStore`**（`store.py`）：Protocol + `InMemory` / `Sqlite` 双 adapter（行为一致，测试用内存、生产用 SQLite 持久化重启可恢复）。**线程安全**：`check_same_thread=False` + `threading.Lock`。方法：`create_project/add_asset/get/latest/children/select_variation/get_selected_variation`。
- **`ReadinessGate`**（`readiness.py`）：就绪度判定取代线性状态机——`can(operation, project_id)` 看谱系里有没有前置资产（variations 需 cutout；lineart 需选中变体或 cutout；material 需 lineart；edit 需有成衣）。
- **空输出红线**：`GenerationJob.run_generation`（`generation/job.py`）生成后 `is_valid_image` 校验（解码/非空/尺寸）才入库——历史上出过 0 字节 PNG 污染状态的 bug。

---

## 4. 模块地图（调用关系）

### 后端 `backend/app/`
| 模块 | 职责 | 谁调用 |
|---|---|---|
| `main.py` | FastAPI 装配、CORS、SQLite 注入、单端口静态伺服 | 入口 |
| `assets/api.py` | **所有 REST 端点**（15 个）+ 渲染选路常量 | 路由 |
| `assets/store.py` | 资产谱系存储（SQLite/内存） | api、job |
| `assets/models.py` | `DesignProject/DesignAsset/AssetKind` | 全局 |
| `readiness.py` | `ReadinessGate` 就绪 DAG | api |
| `generation/job.py` | `run_generation`：校验+取消+`post_process` 钩子 | api（variations/material/edit/finalize） |
| `generation/backend.py` | `HttpComfyUIBackend`：`generate`(批,可快轮询) / `generate_live`(source缓存+快轮询)。`trust_env=False` 绕系统代理 | job、api |
| `services/remove_bg.py` | rembg 抠图（u2net） | upload |
| `services/lineart.py` | controlnet_aux 提线稿 → **洗成实心黑线**（二值化+闭运算，与用户笔统一、保框架细节） | lineart 端点、_ensure_lineart |
| `services/mask_utils.py` | 笔触→mask、`composite_subject_lock(_bytes)` 主体锁定合成、`invert_lineart`（黑线白底↔白线黑底） | edit/render-local |
| `services/sketch_intent.py` | **CLIP 零样本意图识别**（laion CLIP-ViT-B-32，离线，~5-50ms） | render-local `_auto_intent` |
| `services/vectorize.py` | 线稿矢量化为折线（VectorCanvas 用，现未启用） | lineart-vector |
| `imaging.py` | `is_valid_image` 空输出校验 | job、api |
| `api/__init__.py` | 老 session 路由（遗留）+ `/api/images` 图片服务（已改读 `AIFD_IMAGES_DIR`） | main |

### 前端 `frontend/src/`
| 文件 | 职责 |
|---|---|
| `App.tsx` | 双画布工作台外壳；**实时回路 pump**（帧合并：首帧 render-live 引导、之后 render-local 局部，队列深度恒为 1） |
| `PaintCanvas.tsx` | **像素画板（最终方案）**：载完整细节线稿、笔/橡皮"过哪擦哪"、笔粗自选、改动区 bbox 追踪 |
| `VectorCanvas.tsx` | 矢量画板（Tldraw 原生笔画）——试过但橡皮"碰整条删"被否，保留未用 |
| `useProject.ts` | 镜像后端某 `DesignProject` 的资产；`upload/generateVariations/renderLive/renderLocal/finalizeDesign/...`；URL `?project=` 恢复 |

### REST 端点（`/api` 前缀）
`POST /projects` · `/upload` · `/variations` · `/select-variation` · `/lineart` · `/lineart-vector` · `/lineart-image`(草图优先) · `/material` · `/edit`(定稿inpaint) · `/edit-live`(笔触快inpaint) · **`/render-live`**(整张重渲,引导) · **`/render-local`**(局部重渲,壁垒) · `/finalize` · `GET /export` · `GET /images/{f}`

---

## 5. 两条核心管线

### 5a. 上传自动流水线
`upload`(rembg→Cutout) → `lineart`(controlnet_aux→洗成实心黑线→Lineart) → `material`(ControlNet→Material)。前端 `useProject.upload` 串起来，左出可编辑线稿、右出成衣。

### 5b. 实时局部渲染（核心壁垒）
```
左 PaintCanvas 改一笔(笔/橡皮)
  → onChange → App.pump
  → 导出整张画板 + 改动区 bbox→mask
  → 首帧/无改动: POST /render-live（整张 SDXL-LCM+ControlNet 重渲, 固定 seed=42）
  → 有改动:     POST /render-local（base=当前成衣 + mask + 改后线稿）
       后端: CLIP 自动认改动区画的是什么(带语境) → 注入语义 prompt
            → 按意图动态调遵循(纹理类降 end_percent 渲褶, 形状类保高遵循贴合)
            → sdxl_lcm_controlnet_inpaint.json (VAEEncodeForInpaint 只采样改动区, denoise 0.65 保底图面料)
            → composite_subject_lock_bytes 合成(区外像素级不动, 实测差 0.02)
  → 右 <img> 刷新, ~1s/帧暖
```
**"意图+服装+遵循"三者兼顾**（#27）：CLIP 管意图、ControlNet 管遵循、SDXL 管服装语言；denoise 0.65 让局部特征"长进"同款面料而非异色补丁。

---

## 6. 模型 / 工作流栈（ADR-0006 模型阶梯）

**全线 SDXL**（默认 `AIFD_SDXL=1`，SD1.5 留作降级）：
| 用途 | 工作流 | 基座 | ControlNet | 步数/时延 |
|---|---|---|---|---|
| 实时 render-live | `sdxl_lcm_controlnet.json` | SDXL + LCM-LoRA-SDXL | union | 8步 ~1s |
| 实时 render-local | `sdxl_lcm_controlnet_inpaint.json` | 同上 + VAEEncodeForInpaint | union | 8步 ~1s |
| material/变体 | `sdxl_controlnet.json` / 实时同款 | SDXL(+LCM) | union | 25步~13s / 8步 |
| 定稿 finalize | — | 当前成衣 2x Lanczos 放大 | — | 即时 |

模型（数据盘 `/root/autodl-tmp/models` + 软链 ComfyUI）：SDXL base 6.5G、controlnet-union-sdxl-promax 2.4G、lcm-lora-sdxl 0.4G、controlnet-scribble-sdxl 2.4G（备）、SD1.5/lineart/lcm-lora(降级)、u2net(抠图)、Annotators(线稿)、laion CLIP(意图)。

关键技巧：**end_percent 调度**（ControlNetApplyAdvanced：前段定结构、后段渲特征）；**LCM step-distillation**（8步出图，即用户问的"Turbo 同族"）；线稿喂 ControlNet 前 `invert_lineart`（黑线白底→白线黑底，否则渲成线框）。

---

## 7. 关键决策与演进（"为什么这么做"）

- **资产谱系 > 散 base64 / 线性状态机**（ADR-0001/0002）：可追溯、可恢复、就绪 DAG 取代脆弱的步进。
- **像素画板 > 矢量画板**：用户要"橡皮过哪擦哪"的局部擦 = 必须栅格（矢量橡皮碰整条就删）。VectorCanvas/vectorize 是中途弯路，保留未用。
- **线稿洗成实心黑线**：controlnet_aux 出的灰淡素描感与用户实心黑笔风格不一致 → 二值化统一；但**不做面积删除**以保框架/细节（兜扣缝线）。
- **实时 = 改左线稿→右整件/局部重渲（固定 seed）**，不是在成衣上涂 inpaint（一开始做反了，看竞品视频后拨正）。
- **实时局部渲染 = inpaint + composite 主体锁定**：改动区外像素级不动（壁垒）。
- **渲染对齐 = 成衣模板 + 强负向 + 3D + 控制 ControlNet 强度**：从"面料特写"→"白底立体成衣"。
- **CLIP 自动语义（非手动意图笔刷）**：用户要一个笔刷无感；CLIP 零样本 ~5ms 远快于 VLM ~2s；且"矩形不强判成兜"= 平衡。
- **denoise 0.65 局部**：特征同款面料、长进衣服而非异色补丁。

---

## 8. 状态与开放项

- **可用**：六场景端到端、SDXL 实时局部渲染（~1s）、CLIP 自动意图、像素画板局部擦、实心线稿、主体锁定、高清定稿。
- **开放 issue**：
  - **#27** 语义理解收口：模糊形状（兜/扣）识别增强（位置/上下文）、印花（画图案→面料印花）。
  - **#26** SDXL 精调 + IP-Adapter（风格/印花）+ BrushNet（高保真局部）。
  - **#1** PRD 父票。
- **已知差距**：稀疏徒手草图 render-live 偏"面料特写"（细节线稿好）；实时首帧冷载 SDXL ~10s（之后 ~1s）；局部 patch 边界偶有淡痕。

---

## 9. 本地验证

```bash
# 后端编译 + 测试（13 个测试文件，约 50 用例）
cd backend && ./venv/bin/python -m compileall -q app && ./venv/bin/python -m pytest -q
# 前端类型 + 构建
cd frontend && VITE_API_BASE= npm run build
```
重活（变体/试布/渲染）在 GPU 上每张数秒；遵循 ADR-0004：**本地验正确性，速度/画质留 GPU**。
