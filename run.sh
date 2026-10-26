#!/usr/bin/env bash
# 阿赖耶记忆引擎 · 启动脚本
set -euo pipefail
cd "$(dirname "$0")"

PY="${PYTHON:-python3}"

if [ ! -d ".venv" ]; then
  echo "==> 创建虚拟环境 .venv"
  "$PY" -m venv .venv
  ./.venv/bin/pip install -q --upgrade pip
  ./.venv/bin/pip install -q -r requirements.txt
fi

if [ ! -f "config.json" ]; then
  cp config.example.json config.json
  echo "==> 已生成 config.json，请填入各厂商的 base_url / api_key / model"
  echo "    也可以启动后在页面「我的资料 → 模型设置」里填写。"
fi

exec ./.venv/bin/python server/index.py
