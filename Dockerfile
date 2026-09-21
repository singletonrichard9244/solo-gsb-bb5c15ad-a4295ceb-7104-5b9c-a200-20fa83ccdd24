FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    HOST=0.0.0.0 \
    PORT=8000 \
    DATABASE_URL=sqlite:////data/lineage.db

WORKDIR /app

# 构建期安装依赖（利用镜像层缓存）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY entrypoint.sh .
RUN chmod +x entrypoint.sh

EXPOSE 8000

# 启动时再次确保依赖就绪，然后启动服务（建库由应用启动时完成）
CMD ["./entrypoint.sh"]
