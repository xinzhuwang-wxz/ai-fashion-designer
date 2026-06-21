"""草图意图识别（#27）——CLIP 零样本把"改动区草图"分类到意图词表 → 自动选语义。

用户一个笔刷无感：画什么，CLIP（~5ms/帧，远快于 VLM 的 ~2s）自动判断是兜/褶/扣/领…，
该语义喂给局部渲染（render-local），渲成对应服装特征。仅固定词表用 CLIP；自由描述才需 VLM。
"""
from __future__ import annotations

import io

from PIL import Image

# (CLIP 文字标签, 注入渲染的语义 prompt)。plain → 不注入。
INTENT_VOCAB: list[tuple[str, str]] = [
    ("plain garment fabric with simple outline", ""),
    ("a pocket", "a pocket"),
    ("a row of buttons", "a row of buttons"),
    ("vertical pleats", "vertical pleats, accordion pleats"),
    ("wavy ripples on fabric", "fabric ripples, undulating soft drape"),
    ("a shirt collar", "a collar"),
    ("a zipper", "a zipper"),
    ("a ruffle frill", "a ruffle frill"),
    ("lace trim", "lace trim"),
]

_model = None
_proc = None
# 用有 safetensors 的 laion CLIP（openai/clip 仓库无 safetensors，.bin 被 CVE-2025-32434 拦）
_CLIP_ID = "laion/CLIP-ViT-B-32-laion2B-s34B-b79K"


def _load():
    global _model, _proc
    if _model is None:
        import torch
        from transformers import CLIPModel, CLIPProcessor

        _proc = CLIPProcessor.from_pretrained(_CLIP_ID)
        m = CLIPModel.from_pretrained(_CLIP_ID)
        if torch.cuda.is_available():
            m = m.to("cuda")
        m.eval()
        _model = m
    return _model, _proc


def classify_intent(region_png: bytes, min_prob: float = 0.28) -> str:
    """改动区草图 PNG → 意图语义 prompt（plain 或低置信 → ''）。失败静默返回 ''。"""
    try:
        import torch

        model, proc = _load()
        img = Image.open(io.BytesIO(region_png)).convert("RGB")
        labels = [f"a fashion design line sketch of {t}" for t, _ in INTENT_VOCAB]
        inputs = proc(text=labels, images=img, return_tensors="pt", padding=True)
        if torch.cuda.is_available():
            inputs = {k: v.to("cuda") for k, v in inputs.items()}
        with torch.no_grad():
            probs = model(**inputs).logits_per_image.softmax(dim=1)[0]
        idx = int(probs.argmax())
        if float(probs[idx]) < min_prob:
            return ""
        return INTENT_VOCAB[idx][1]
    except Exception:
        return ""
