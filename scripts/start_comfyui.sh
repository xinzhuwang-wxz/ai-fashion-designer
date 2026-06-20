#!/bin/bash
cd /Users/bamboo/Githubs/ai-fashion-designer/comfyui
source venv/bin/activate
exec python -u main.py --listen 0.0.0.0 --port 8188 2>&1
