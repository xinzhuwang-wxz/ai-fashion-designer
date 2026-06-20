# AutoDL 4090 部署指南（开机即跑 + 浏览器体验）

目标：在 AutoDL RTX 4090 上跑 **ComfyUI + 后端 + 前端**，AutoDL 端口映射成公网网址 → 浏览器直接体验（上传/画草图/出成衣/局部编辑）。

镜像：`comfyanonymous/ComfyUI/tzwm_ComfyUI:v22`（non-blackwell）。ComfyUI 在 `/root/ComfyUI`，conda base = `/root/miniconda3/bin/python`，数据盘 `/root/autodl-tmp`（200G，关机保留）。已预装节点：IPAdapter_plus、controlnet_aux、Advanced-ControlNet、Impact-Pack、BiRefNet、Manager 等。**模型目录空，需下载。**

## 0. SSH（避免 AutoDL 限流）
频繁新建 SSH 连接会被临时挡。**用 ControlMaster 持久连接**，认证一次后复用：
```bash
# 建主连接（输一次密码）
sshpass -p '<PASS>' ssh -o ControlMaster=auto -o ControlPath=/tmp/adl -o ControlPersist=4h \
  -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -p <PORT> root@<HOST> 'echo ok'
# 之后所有命令复用（不再认证）：
ssh -o ControlPath=/tmp/adl -p <PORT> root@<HOST> '<command>'
```

## 1. 下模型（SD1.5 链路，~5.5G → ComfyUI/models）
我们当前工作流用 SD1.5。三种方式，挑通的那个：
- **A 本地上传（最稳，文件一定对）**：在 Mac 上 rsync 本地已有模型到箱子
  ```bash
  rsync -avP -e "ssh -o ControlPath=/tmp/adl -p <PORT>" \
    comfyui/models/checkpoints/v1-5-pruned-emaonly.safetensors root@<HOST>:/root/ComfyUI/models/checkpoints/
  rsync -avP -e "ssh -o ControlPath=/tmp/adl -p <PORT>" \
    comfyui/models/controlnet/control_v11p_sd15_lineart.pth root@<HOST>:/root/ComfyUI/models/controlnet/
  rsync -avP -e "ssh -o ControlPath=/tmp/adl -p <PORT>" \
    comfyui/models/loras/lcm-lora-sdv1-5.safetensors root@<HOST>:/root/ComfyUI/models/loras/
  ```
- **B modelscope（魔搭，国内快）**：在箱子上 `pip install modelscope` 后用 `snapshot_download` 拉 `AI-ModelScope/stable-diffusion-v1-5` 等（repo id 现场确认）。
- **C network_turbo + HF**：`source /etc/network_turbo` 后 `huggingface-cli download stable-diffusion-v1-5/stable-diffusion-v1-5 ...`（hf-mirror 对该 repo 限速，turbo 直连 HF 更快）。

> 文件名必须严格匹配工作流引用：`v1-5-pruned-emaonly.safetensors` / `control_v11p_sd15_lineart.pth` / `lcm-lora-sdv1-5.safetensors`。
> 升 SDXL（ADR-0006）：另下 SDXL base + ControlNet-lineart-SDXL + IP-Adapter-SDXL，并新建 SDXL 版工作流。

## 2. 启动 ComfyUI（用 4090）
```bash
ssh -o ControlPath=/tmp/adl -p <PORT> root@<HOST> \
  'cd /root/ComfyUI && nohup /root/miniconda3/bin/python main.py --listen 127.0.0.1 --port 8188 > /root/comfy.log 2>&1 & echo started'
# 等就绪：curl -s localhost:8188/system_stats（在箱子上）
```

## 3. 部署后端 + 前端（单端口，供浏览器）
两种：
- **整套上箱子（推荐，公网网址体验）**：把仓库（backend + 构建好的 frontend/dist）传上去
  ```bash
  # 本地构建前端为相对 API（同源）
  cd frontend && VITE_API_BASE='' npm run build      # 产出 frontend/dist
  # 上传仓库到箱子（排除 venv/node_modules/comfyui/.git）
  rsync -avP -e "ssh -o ControlPath=/tmp/adl -p <PORT>" --exclude node_modules --exclude venv \
    backend frontend/dist workflows root@<HOST>:/root/aifd/
  # 箱子上装后端依赖 + 跑（main.py 在 dist 存在时单端口伺服前端）
  ssh -o ControlPath=/tmp/adl -p <PORT> root@<HOST> 'cd /root/aifd/backend && /root/miniconda3/bin/pip install -r requirements.txt fastapi uvicorn rembg controlnet_aux && \
    COMFYUI_URL=http://127.0.0.1:8188 AIFD_DB=/root/autodl-tmp/assets.db nohup /root/miniconda3/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 6006 > /root/aifd.log 2>&1 & echo started'
  ```
  然后 **AutoDL 控制台「自定义服务」把 6006 映射成公网 https 网址** → 浏览器打开即用。
- **本地开发 + 隧道（我开发用）**：前端/后端留本地，SSH 隧道把箱子 ComfyUI 接过来
  ```bash
  ssh -o ControlPath=/tmp/adl -L 8188:127.0.0.1:8188 -N -p <PORT> root@<HOST> &   # 隧道
  # 本地后端默认连 localhost:8188（=隧道）→ 渲染在 4090
  ```

## 4. 验证清单
- ComfyUI: `curl localhost:8188/object_info/CheckpointLoaderSimple` 能看到 `v1-5-pruned-emaonly.safetensors`
- 后端: `curl <网址>/api/projects -XPOST` 返回 project_id
- 浏览器: 上传服装图 → 左线稿 + 右成衣；画草图 → 用草图生成；右画布画笔 → 局部重绘(#7)

## 注意
- **用完关机**（关机不计 GPU 费、数据盘保留；释放才删数据）。
- 后端 httpx 已 `trust_env=False`，箱子内连 ComfyUI 不受系统代理影响。
- 系统代理会破坏 hf-mirror / controlnet_aux 模型下载——下模型时 `source /etc/network_turbo` 或清 `http_proxy`。
