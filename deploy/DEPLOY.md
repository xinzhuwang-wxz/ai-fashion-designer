# AutoDL 4090 部署指南 —— 全线 SDXL 版（换张卡照此重建）

目标：在 AutoDL RTX 4090 上跑 **ComfyUI + 后端 + 前端单端口**，本机 SSH 隧道 `localhost:6006` 浏览器体验（或 AutoDL「自定义服务」映射 6006 成公网）。

镜像：`tzwm_ComfyUI:v22`。ComfyUI 在 `/root/ComfyUI`，conda base `/root/miniconda3/bin/python`(py3.12)，**数据盘 `/root/autodl-tmp`（释放才删，模型放这）**。预装节点：IPAdapter_plus、controlnet_aux、Advanced-ControlNet 等。**模型目录空，需下载。**

> 卡释放后模型/服务全没——本文 + [docs/END-TO-END-DESIGN.md](../docs/END-TO-END-DESIGN.md) 是重建依据。

---

## 1. 完整模型清单（全线 SDXL）

放 `/root/autodl-tmp/models/{checkpoints,controlnet,loras}` + **软链回 ComfyUI**。**文件名必须严格匹配工作流引用**。

| 文件名 | 大小 | 用途 | 来源 |
|---|---|---|---|
| `sd_xl_base_1.0.safetensors` | 6.5G | SDXL 基座 | modelscope `AI-ModelScope/stable-diffusion-xl-base-1.0` |
| `controlnet-union-sdxl-promax.safetensors` | 2.4G | 实时/渲染 ControlNet | HF `xinsir/controlnet-union-sdxl-1.0` 的 `diffusion_pytorch_model_promax.safetensors` → 改名 |
| `lcm-lora-sdxl.safetensors` | 0.4G | SDXL 8步加速(LCM) | HF `latent-consistency/lcm-lora-sdxl` 的 `pytorch_lora_weights.safetensors` → 改名 |
| `controlnet-scribble-sdxl.safetensors` | 2.4G | （备用，松散解释） | HF `xinsir/controlnet-scribble-sdxl-1.0` 的 `diffusion_pytorch_model.safetensors` → 改名 |
| laion CLIP（HF 缓存，非文件） | ~0.6G | **意图识别**（#27 CLIP 零样本） | HF `laion/CLIP-ViT-B-32-laion2B-s34B-b79K`（**必须 laion，openai/clip 无 safetensors+CVE 拦**） |
| `u2net.onnx` → `~/.u2net/` | 168M | 抠图(rembg) | GitHub rembg release |
| Annotators（HF 缓存） | — | 提线稿(controlnet_aux LineartDetector) | HF `lllyasviel/Annotators` |
| 〔降级备用 SD1.5〕 v1-5-pruned-emaonly / control_v11p_sd15_lineart.pth / lcm-lora-sdv1-5 | ~5.5G | `AIFD_SDXL=0` 时回退 | modelscope `AI-ModelScope/stable-diffusion-v1-5` 等 |

### 下载命令（坑都标了）
```bash
# 大 checkpoint 优先 modelscope（比 hf-mirror 快百倍）
export PATH=/root/miniconda3/bin:$PATH
modelscope download --model AI-ModelScope/stable-diffusion-xl-base-1.0 sd_xl_base_1.0.safetensors --local_dir /root/autodl-tmp/models/checkpoints

# HF 模型走加速器 + 必须关 xet（xet CAS 走代理报 401）
source /etc/network_turbo; export HF_HUB_DISABLE_XET=1
curl -fL -o /root/autodl-tmp/models/controlnet/controlnet-union-sdxl-promax.safetensors \
  https://huggingface.co/xinsir/controlnet-union-sdxl-1.0/resolve/main/diffusion_pytorch_model_promax.safetensors
curl -fL -o /root/autodl-tmp/models/loras/lcm-lora-sdxl.safetensors \
  https://huggingface.co/latent-consistency/lcm-lora-sdxl/resolve/main/pytorch_lora_weights.safetensors

# 软链回 ComfyUI（base/controlnet/loras 同理）
ln -sf /root/autodl-tmp/models/checkpoints/sd_xl_base_1.0.safetensors /root/ComfyUI/models/checkpoints/
ln -sf /root/autodl-tmp/models/controlnet/controlnet-union-sdxl-promax.safetensors /root/ComfyUI/models/controlnet/
ln -sf /root/autodl-tmp/models/loras/lcm-lora-sdxl.safetensors /root/ComfyUI/models/loras/

# CLIP / Annotators / u2net 由后端首次用时下载到缓存 → 预热（network_turbo + 关 xet + transformers 用 laion）
source /etc/network_turbo; unset HF_HUB_OFFLINE; export HF_HUB_DISABLE_XET=1
/root/autodl-tmp/aifd-venv/bin/python -c "from transformers import CLIPModel,CLIPProcessor; CLIPProcessor.from_pretrained('laion/CLIP-ViT-B-32-laion2B-s34B-b79K'); CLIPModel.from_pretrained('laion/CLIP-ViT-B-32-laion2B-s34B-b79K')"
/root/autodl-tmp/aifd-venv/bin/python -c "import io;from PIL import Image;from rembg import remove;remove(Image.new('RGB',(64,64)).tobytes() and __import__('io').BytesIO(b''))" 2>/dev/null  # u2net；或先跑一次 upload
/root/autodl-tmp/aifd-venv/bin/python -c "from controlnet_aux import LineartDetector; LineartDetector.from_pretrained('lllyasviel/Annotators')"
```

---

## 2. 部署代码（前端在 Mac build，盒子无 node）

```bash
# Mac: 前端同源 build + 打包后端/工作流/dist
cd frontend && VITE_API_BASE= npm run build
cd .. && tar czf /tmp/aifd.tgz --exclude __pycache__ backend/app backend/requirements.txt workflows frontend/dist
scp -P <PORT> /tmp/aifd.tgz root@<HOST>:/root/autodl-tmp/
# 盒子: 解压 + venv（--system-site-packages 复用 base 的 torch/cv2/rembg/transformers，不碰 ComfyUI 环境）
ssh ... 'mkdir -p /root/autodl-tmp/ai-fashion-designer && tar xzf /root/autodl-tmp/aifd.tgz -C /root/autodl-tmp/ai-fashion-designer
  python -m venv --system-site-packages /root/autodl-tmp/aifd-venv
  /root/autodl-tmp/aifd-venv/bin/pip install -i https://pypi.tuna.tsinghua.edu.cn/simple fastapi "uvicorn[standard]" httpx python-multipart pydantic aiofiles controlnet_aux'
```

---

## 3. 启停脚本（盒子无 fuser/ss/lsof → 用 pidfile）

**ComfyUI**（8188）：`cd /root/ComfyUI && nohup python main.py --listen 127.0.0.1 --port 8188 > /root/comfy.log 2>&1 &`。换模型后需重启它才认到（kill 旧 pid 再起；**别 pkill -f main.py——同条命令含 main.py 会自杀**，base64 包脚本或按 pid）。

**后端**（6006）`/root/restart-backend.sh`：
```bash
#!/bin/bash
[ -f /root/backend.pid ] && kill "$(cat /root/backend.pid)" 2>/dev/null; sleep 2
cd /root/autodl-tmp/ai-fashion-designer/backend
export COMFYUI_URL=http://127.0.0.1:8188 AIFD_IMAGES_DIR=/root/autodl-tmp/aifd-data/images \
  AIFD_DB=/root/autodl-tmp/aifd-data/assets.db \
  HF_HUB_DISABLE_XET=1 HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 AIFD_SDXL=1
nohup /root/autodl-tmp/aifd-venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 6006 > /root/backend.log 2>&1 &
echo $! > /root/backend.pid
for i in $(seq 1 20); do curl -s -o /dev/null -m2 http://127.0.0.1:6006/health && break; sleep 1; done
echo "pid=$(cat /root/backend.pid) $(curl -s http://127.0.0.1:6006/health)"
```
> `AIFD_SDXL=1` 全线 SDXL（=0 回退 SD1.5）。`HF_HUB_OFFLINE=1` 必须——否则 lineart/CLIP 首请求联网查更新卡 ~170s（模型已预热缓存，离线直接读）。

**访问**：本机 `ssh -L 6006:127.0.0.1:6006 -p <PORT> root@<HOST>` → 浏览器 `localhost:6006`；或 AutoDL「自定义服务」映射 6006。

---

## 4. 验证 + 坑汇总
- ComfyUI 认到模型：`curl localhost:8188/object_info/CheckpointLoaderSimple | grep sd_xl_base`、`.../LoraLoader | grep lcm-lora-sdxl`、`.../ControlNetLoader | grep union`。
- 后端：`curl localhost:6006/health` → ok；`/api/projects -XPOST` → project_id。
- **坑**：① 盒子无 fuser/ss/lsof，pidfile 杀进程。② `pkill -f <pat>` 若 `<pat>` 也在启动命令里会自杀。③ HF 下载关 xet(`HF_HUB_DISABLE_XET=1`)。④ CLIP 用 laion（openai/clip 无 safetensors+CVE-2025-32434 拦 .bin）。⑤ 后端 httpx `trust_env=False` 绕代理连 ComfyUI。⑥ 线稿喂 ControlNet 前要 invert（黑线白底→白线黑底，否则渲线框）。⑦ 老 `app/api/__init__.py` 的 `/api/images` 必须读 `AIFD_IMAGES_DIR`（否则 404）。
- **用完关机不计费、数据盘留**；**释放才删数据/模型**（届时照本文重下）。
