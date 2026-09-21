#!/bin/sh
set -e

# 容器启动时确保依赖安装就绪（镜像构建期已安装时此步骤为快速校验）
pip install --no-cache-dir -r requirements.txt

# 数据库文件与表结构由应用启动钩子自动创建（见 app/main.py 的 lifespan）
exec uvicorn app.main:app --host "${HOST:-0.0.0.0}" --port "${PORT:-8000}"
