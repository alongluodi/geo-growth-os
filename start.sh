#!/bin/bash
# GEO Growth OS 快速启动脚本（SQLite 零配置版）

echo "[OK] 正在启动 GEO Growth OS..."
echo ""

# 检查Python
if ! command -v python3 &> /dev/null; then
    echo "[ERROR] 未找到 Python3，请先安装"
    exit 1
fi

# SQLite 无需外部数据库，仅显示数据文件位置
echo "[OK] 数据库: SQLite（${DB_PATH:-./data/geo.db}，首次运行自动创建）"

# 安装依赖
echo "[OK] 安装依赖..."
pip install -r requirements.txt -q

# 初始化数据库（幂等，可重复执行）
echo "[OK] 初始化数据库..."
python3 database.py

# 启动模式选择：PROD_MODE=1 使用 Gunicorn，否则开发模式
if [ "$PROD_MODE" = "1" ]; then
    echo "[OK] 生产模式启动（Gunicorn 2 workers，SQLite 写串行化）..."
    echo "访问地址: http://localhost:5000"
    echo ""
    exec gunicorn --bind 0.0.0.0:5000 --workers 2 --timeout 120 wsgi:app
else
    echo "[OK] 开发模式启动..."
    echo "访问地址: http://localhost:5000"
    echo ""
    exec python3 app.py
fi
