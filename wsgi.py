"""
GEO Growth OS - WSGI 入口
供 Gunicorn / uWSGI 等生产 WSGI 服务器加载
"""
from app import app, init_db

# 确保 WSGI 服务器启动前数据库已初始化
init_db()

if __name__ == '__main__':
    # 直接运行时回退到 Flask 开发服务器
    app.run(host='0.0.0.0', port=5000)
