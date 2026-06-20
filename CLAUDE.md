# CLAUDE.md

AI 服装设计工具：上传服装照片 / 直接画线稿 → AI 抠图 → 方案发散 → 选变体提线稿 → 布料试穿 → 实时局部编辑 → 高清成品。复刻抖音"星雨图库Ai / 艾黎设计"那类**双画布工作台**产品。

## 开工前必读（按顺序）

1. **`CONTEXT.md`** — 领域术语表（ubiquitous language）。用词以它为准：资产用 `DesignAsset`/谱系，别叫"图片/base64 字段"。
2. **`docs/adr/0001–0006`** — 已锁定的架构决策。动手前先看，别推翻已决项。
3. **`docs/ARCHITECTURE.md`** — 基于实测的差距报告（v0.3.0）。状态以实测为准，**不以"代码存在/HTTP 200"视为功能完成**。

## 当前状态（诚实）

可运行的技术原型，六场景生命周期约 35–40%。**正在朝"复刻"重构中**。已核实的红线缺陷见 ARCHITECTURE.md §6/§7 与下方决策。根目录**当前不是 git 仓库**（重构第 0 步会 `git init`）；**无业务测试**。

## 架构

```
前端 React + Tldraw (:3000)  ──HTTP/WS──▶  后端 FastAPI (:8000)  ──HTTP──▶  ComfyUI (:8188, MPS)
  双画布工作台(重构目标)                     资产谱系 + 就绪DAG(重构目标)        SD1.5 + ControlNet-lineart + LCM
```

关键文件：
- `backend/app/api/__init__.py` — 所有 REST + WebSocket 端点
- `backend/app/services/state_machine.py` — 流程状态（重构为资产就绪 DAG，见 ADR-0002）
- `backend/app/services/comfyui_client.py` — ComfyUI 客户端 + 12 布料 prompt + 4 推理函数（已环境化：`COMFYUI_URL`；空结果已改 raise）
- `backend/app/assets/` — AssetStore 谱系存储（#2）；`backend/app/generation/` — GenerationJob 安全层（#3）
- `backend/app/services/{remove_bg,lineart,inpaint,mask_utils}.py` — 抠图/线稿/快速预览/mask
- `frontend/src/App.tsx` + `useProject.ts` — 双画布工作台（左 Tldraw 线稿/草图，右只读成衣渲染）+ useProject 镜像后端资产（#10，ADR-0005）
- `comfyui/workflows/*.json` — variation / fabric_fill_controlnet / lcm_variation

## 重构方向（已决，详见 ADR）

- **ADR-0001** 资产谱系图 `DesignAsset(parent/kind/seed/...)` + SQLite，取代散落 base64 字段。
- **ADR-0002** 资产就绪度 DAG 取代线性状态机；上传链路与草图优先链路汇合到 Lineart。
- **ADR-0003** 实时编辑 = 笔触 mask 局部重绘（非全图）；前端传图像归一化坐标；M4 改长 debounce/手动提交。
- **ADR-0004** 两阶段验收：**本地验正确性，速度/画质留 GPU**；本地不承诺实时帧率。
- **ADR-0005** 前端重写为真·双画布（左线稿可编辑/右成衣只读）+ 草图优先入口。
- **ADR-0006** 模型阶梯：本地 SD1.5 跑链路，目标基座 SDXL + IP-Adapter + BrushNet + 高清放大（租卡后）。

## 本地运行

```bash
# 1. ComfyUI（M4 原生跑，吃 MPS，注意发热）
cd comfyui && source venv/bin/activate && python main.py --listen 0.0.0.0 --port 8188
# 2. 后端
cd backend && HF_ENDPOINT=https://hf-mirror.com ./venv/bin/python -m uvicorn app.main:app --port 8000 --reload
# 3. 前端
cd frontend && npm run dev
```
模型下载走 `HF_ENDPOINT=https://hf-mirror.com`（HuggingFace 被墙）。本机有 Privoxy 代理会拦截对 :8188 的 curl，调试 ComfyUI 用 `lsof -iTCP:8188` 看进程更可靠。

## 验证（本地，轻量）

```bash
curl -s localhost:8000/health                          # 后端 + MPS
cd backend && ./venv/bin/python -m compileall -q app    # 后端编译
cd frontend && npx tsc --noEmit                          # 前端类型
```
重活（变体/试布/最终渲染）在 M4 上每张几十秒且发热，**别在循环里反复触发**。

## 约定

- 用词遵循 `CONTEXT.md`；新决策（难回退 + 反直觉 + 有取舍）写进 `docs/adr/`。
- 重构按"小步提交、可回滚"推进；每步留冒烟回归。
- 空 AI 结果必须先校验（解码/非空/尺寸）才入库并推进状态——历史上出过 0 字节 PNG 污染状态的 bug。
