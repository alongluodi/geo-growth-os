FROM python:3.11-slim

WORKDIR /app

# 安装依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && useradd -m -u 1000 appuser \
    && mkdir -p /app/data && chown -R appuser:appuser /app/data

# 以非 root 用户运行（安全加固）
COPY --chown=appuser:appuser . .

USER appuser

EXPOSE 5000

# 生产环境务必通过环境变量注入强随机 SECRET_KEY，并保持 Debug 关闭
ENV SECRET_KEY=""
ENV FLASK_DEBUG="false"
ENV SESSION_COOKIE_SECURE="false"
# SQLite 数据库文件路径（默认 ./data/geo.db，挂载卷可持久化）
ENV DB_PATH="/app/data/geo.db"

# 生产环境通过 Gunicorn 运行（2 worker：SQLite 写操作串行化，worker 不宜过多）
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "2", "--timeout", "120", "wsgi:app"]
