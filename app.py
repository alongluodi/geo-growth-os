"""
GEO Growth OS - Flask 主应用
全链路GEO优化SaaS平台
"""
import os
import json
import random
import secrets
import time
from datetime import datetime
from functools import wraps
from flask import Flask, render_template, request, jsonify, redirect, url_for, session, flash
from werkzeug.security import check_password_hash
from database import get_db, init_db
from ai_service import (
    AIVisibilityDiagnostic, AIContentGenerator,
    KnowledgeBaseManager, AnalyticsDashboard
)

app = Flask(__name__, 
            template_folder=os.path.join(os.path.dirname(__file__), 'templates'),
            static_folder=os.path.join(os.path.dirname(__file__), 'static'))

# 会话安全配置：Secret Key 优先从环境变量读取，禁止硬编码生产密钥
app.secret_key = os.environ.get('SECRET_KEY', 'geo-growth-os-dev-secret-change-me')
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    # 生产环境通过环境变量 SESSION_COOKIE_SECURE=true 开启 HTTPS-only Cookie
    SESSION_COOKIE_SECURE=os.environ.get('SESSION_COOKIE_SECURE', 'false').lower() == 'true',
    # 限制请求体大小，防止超大 JSON 请求
    MAX_CONTENT_LENGTH=10 * 1024 * 1024,
)

# ==================== 登录防爆破（内存版限流） ====================
# 结构: { "ip:username": [失败次数, 首次失败时间戳] }
_login_attempts = {}
MAX_LOGIN_ATTEMPTS = 5
LOCKOUT_SECONDS = 900  # 15 分钟


def _login_attempts_key():
    """生成限流键（IP + 用户名）"""
    remote_ip = request.remote_addr or 'unknown'
    username = request.form.get('username', '').strip()
    return f"{remote_ip}:{username}"


def _is_locked(key):
    """检查是否被锁定，返回 (是否锁定, 剩余秒数)"""
    rec = _login_attempts.get(key)
    if not rec:
        return False, 0
    count, first_ts = rec
    if time.time() - first_ts >= LOCKOUT_SECONDS:
        _login_attempts.pop(key, None)
        return False, 0
    if count >= MAX_LOGIN_ATTEMPTS:
        return True, int(LOCKOUT_SECONDS - (time.time() - first_ts))
    return False, 0


def _record_login_failure(key):
    """记录一次登录失败"""
    rec = _login_attempts.get(key)
    now = time.time()
    if rec and now - rec[1] < LOCKOUT_SECONDS:
        _login_attempts[key] = (rec[0] + 1, rec[1])
    else:
        _login_attempts[key] = (1, now)


def _clear_login_failures(key):
    """登录成功后清除失败记录"""
    _login_attempts.pop(key, None)


# ==================== 操作审计日志 ====================

def write_audit_log(user_id, action, target_table, target_id, details):
    """写入操作审计日志（失败不影响主流程）"""
    try:
        with get_db() as conn:
            conn.execute(
                'INSERT INTO audit_logs (user_id, action, target_table, target_id, details) VALUES (%s,%s,%s,%s,%s)',
                (user_id, action, target_table, target_id, details)
            )
    except Exception:
        pass


# ==================== CSRF 防护 ====================

@app.before_request
def csrf_protect():
    """非安全方法请求必须携带有效 CSRF Token"""
    if request.method in ('GET', 'HEAD', 'OPTIONS'):
        return
    token = session.get('_csrf_token')
    if not token:
        return jsonify({'error': '会话已过期，请重新登录'}), 403
    header_token = request.headers.get('X-CSRF-Token', '')
    form_token = request.form.get('_csrf_token', '')
    if header_token != token and form_token != token:
        return jsonify({'error': 'CSRF 校验失败，请刷新页面后重试'}), 403


@app.context_processor
def inject_csrf_token():
    """向模板注入 CSRF Token"""
    return {'csrf_token': session.get('_csrf_token', '')}


# ==================== 安全响应头 ====================

@app.after_request
def set_security_headers(response):
    """统一注入安全响应头，防止点击劫持、MIME 嗅探、XSS 等"""
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    # CSP：允许内联脚本（Jinja2 模板需要）、同源样式与图片
    response.headers['Content-Security-Policy'] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "font-src 'self'; "
        "connect-src 'self'"
    )
    return response

# ==================== 认证装饰器 ====================

# 角色权限层级：admin > editor > viewer
ROLE_HIERARCHY = {'admin': 3, 'editor': 2, 'viewer': 1}

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def role_required(min_role):
    """角色权限校验装饰器，min_role 为允许访问的最低角色"""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if 'user_id' not in session:
                return redirect(url_for('login'))
            user_role = session.get('user_role', 'viewer')
            if ROLE_HIERARCHY.get(user_role, 0) < ROLE_HIERARCHY.get(min_role, 0):
                return jsonify({'error': '权限不足，需要 %s 或以上角色' % min_role}), 403
            return f(*args, **kwargs)
        return decorated_function
    return decorator

def get_current_user():
    if 'user_id' not in session:
        return None
    with get_db() as conn:
        user = conn.execute('SELECT * FROM users WHERE id = %s', (session['user_id'],)).fetchone()
        if user:
            company = conn.execute('SELECT * FROM companies WHERE id = %s', (user['company_id'],)).fetchone()
            return {'user': dict(user), 'company': dict(company) if company else None}
    return None

# ==================== 页面路由 ====================

@app.route('/')
def index():
    """首页/登录页"""
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
    return render_template('login.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    """登录"""
    if request.method == 'GET':
        # 生成 CSRF Token 供登录表单使用
        session['_csrf_token'] = secrets.token_hex(16)
        return render_template('login.html')

    username = request.form.get('username', '').strip()
    password = request.form.get('password', '').strip()

    if not username or not password:
        flash('请输入用户名和密码', 'error')
        return render_template('login.html')

    # 登录失败次数限流
    lock_key = _login_attempts_key()
    locked, remain = _is_locked(lock_key)
    if locked:
        write_audit_log(None, 'login_locked', 'users', None,
                        f'账号锁定: {username} (IP: {request.remote_addr})')
        flash(f'登录失败次数过多，请 {max(1, remain // 60)} 分钟后再试', 'error')
        return render_template('login.html'), 429

    with get_db() as conn:
        user = conn.execute('SELECT * FROM users WHERE username = %s', (username,)).fetchone()

    # 密码哈希校验（兼容历史明文自动迁移到哈希后校验）
    if user and check_password_hash(user['password'], password):
        session.clear()
        session['user_id'] = user['id']
        session['company_id'] = user['company_id']
        session['username'] = user['username']
        session['user_role'] = user['role'] if user['role'] in ROLE_HIERARCHY else 'viewer'
        session['_csrf_token'] = secrets.token_hex(16)
        _clear_login_failures(lock_key)
        write_audit_log(user['id'], 'login', 'users', user['id'], '用户登录')
        flash('登录成功！', 'success')
        return redirect(url_for('dashboard'))

    _record_login_failure(lock_key)
    write_audit_log(None, 'login_failed', 'users', None,
                    f'登录失败: {username} (IP: {request.remote_addr})')
    flash('用户名或密码错误！', 'error')
    return render_template('login.html')

@app.route('/logout')
def logout():
    """登出"""
    session.clear()
    flash('已退出登录', 'info')
    return redirect(url_for('login'))

@app.route('/register', methods=['GET', 'POST'])
def register():
    """用户注册（自助开通企业账号）"""
    if request.method == 'GET':
        if 'user_id' in session:
            return redirect(url_for('dashboard'))
        session['_csrf_token'] = secrets.token_hex(16)
        return render_template('register.html')

    # CSRF 校验（before_request 已处理，此处校验 token 存在性）
    company_name = request.form.get('company_name', '').strip()
    industry = request.form.get('industry', '').strip()
    username = request.form.get('username', '').strip()
    password = request.form.get('password', '').strip()
    contact_phone = request.form.get('contact_phone', '').strip()

    if not company_name or not username or not password:
        flash('企业名称、用户名和密码为必填项', 'error')
        return render_template('register.html')

    if len(password) < 6:
        flash('密码长度至少6位', 'error')
        return render_template('register.html')

    from werkzeug.security import generate_password_hash

    with get_db() as conn:
        # 检查用户名是否已存在
        existing = conn.execute('SELECT id FROM users WHERE username = %s', (username,)).fetchone()
        if existing:
            flash('该用户名已被注册，请更换', 'error')
            return render_template('register.html')

        # 创建企业
        cursor = conn.execute(
            'INSERT INTO companies (name, industry, description) VALUES (%s, %s, %s)',
            (company_name, industry or '未填写', '自主注册企业')
        )
        new_company_id = cursor.lastrowid

        # 创建管理员用户
        conn.execute(
            'INSERT INTO users (company_id, username, password, role) VALUES (%s, %s, %s, %s)',
            (new_company_id, username, generate_password_hash(password), 'admin')
        )

        write_audit_log(None, 'register', 'companies', new_company_id,
                        f'新企业注册: {company_name}, 用户: {username}')

    flash('注册成功！请登录', 'success')
    return redirect(url_for('login'))

@app.route('/dashboard')
@login_required
def dashboard():
    """主控仪表盘"""
    user_info = get_current_user()
    company_id = session['company_id']

    overview = AnalyticsDashboard.get_overview(company_id)
    trend = AnalyticsDashboard.get_visibility_trend(company_id)

    return render_template('dashboard.html',
                           user_info=user_info,
                           overview=overview,
                           trend=json.dumps(trend))

# ==================== AI诊断模块 ====================

@app.route('/ai-diagnostic')
@login_required
def ai_diagnostic_page():
    """AI可见度诊断页面"""
    user_info = get_current_user()
    return render_template('ai_diagnostic.html', user_info=user_info)

@app.route('/api/diagnostic/run', methods=['POST'])
@login_required
def api_run_diagnostic():
    """执行AI可见度诊断"""
    data = request.get_json()
    brand_name = data.get('brand_name', '').strip()
    competitors = [c.strip() for c in data.get('competitors', '').split(',') if c.strip()]
    keywords = [k.strip() for k in data.get('keywords', '').split(',') if k.strip()]

    if not brand_name:
        return jsonify({'error': '请输入品牌名称'}), 400

    company_id = session['company_id']
    report = AIVisibilityDiagnostic.run_diagnostic(company_id, brand_name, competitors, keywords)

    return jsonify({'success': True, 'report': report})

@app.route('/api/diagnostic/history')
@login_required
def api_diagnostic_history():
    """获取诊断历史"""
    company_id = session['company_id']
    with get_db() as conn:
        rows = conn.execute(
            'SELECT * FROM visibility_diagnostics WHERE company_id = %s ORDER BY created_at DESC LIMIT 20',
            (company_id,)
        ).fetchall()
    
    results = []
    for r in rows:
        results.append({
            'id': r['id'],
            'brand_name': r['brand_name'],
            'overall_score': r['overall_score'],
            'doubao_score': r['doubao_score'],
            'qianwen_score': r['qianwen_score'],
            'wenxin_score': r['wenxin_score'],
            'kimi_score': r['kimi_score'],
            'created_at': r['created_at'],
        })
    return jsonify(results)

# ==================== 知识库模块 ====================

@app.route('/knowledge-base')
@login_required
def knowledge_base_page():
    """企业知识库管理页面"""
    user_info = get_current_user()
    company_id = session['company_id']

    kb = KnowledgeBaseManager.get_knowledge_base(company_id)
    jsonld = KnowledgeBaseManager.export_jsonld(company_id)

    return render_template('knowledge_base.html',
                           user_info=user_info,
                           kb=kb,
                           jsonld=jsonld)

@app.route('/api/knowledge/update', methods=['POST'])
@login_required
@role_required('editor')
def api_kb_update():
    """更新知识库字段"""
    data = request.get_json()
    field_name = data.get('field_name', '').strip()
    field_value = data.get('field_value', '').strip()
    category = data.get('category', 'general')

    if not field_name:
        return jsonify({'error': '字段名不能为空'}), 400

    company_id = session['company_id']
    KnowledgeBaseManager.update_field(company_id, field_name, field_value, category)
    write_audit_log(session['user_id'], 'update', 'knowledge_base', None, f'更新知识库字段: {field_name}')

    return jsonify({'success': True})

@app.route('/api/knowledge/delete/<int:field_id>', methods=['DELETE'])
@login_required
@role_required('editor')
def api_kb_delete(field_id):
    """删除知识库字段（校验归属，防跨租户越权）"""
    company_id = session['company_id']
    if not KnowledgeBaseManager.delete_field(field_id, company_id):
        return jsonify({'error': '字段不存在或无权操作'}), 404
    write_audit_log(session['user_id'], 'delete', 'knowledge_base', field_id, '删除知识库字段')
    return jsonify({'success': True})

# ==================== AI内容生产模块 ====================

@app.route('/ai-writing')
@login_required
def ai_writing_page():
    """AI文章写作页面"""
    user_info = get_current_user()
    company_id = session['company_id']

    with get_db() as conn:
        articles = conn.execute(
            'SELECT * FROM geo_articles WHERE company_id = %s ORDER BY created_at DESC',
            (company_id,)
        ).fetchall()
        articles = [dict(a) for a in articles]

    return render_template('ai_writing.html',
                           user_info=user_info,
                           articles=articles)

@app.route('/api/article/generate', methods=['POST'])
@login_required
@role_required('editor')
def api_generate_article():
    """AI生成文章"""
    data = request.get_json()
    title = data.get('title', '').strip()
    article_type = data.get('article_type', 'industry_insight')
    direction = data.get('direction', '').strip()
    keywords = [k.strip() for k in data.get('keywords', '').split(',') if k.strip()]

    if not title:
        return jsonify({'error': '请输入文章标题'}), 400

    company_id = session['company_id']
    kb = KnowledgeBaseManager.get_knowledge_base(company_id)
    kb_flat = {}
    for cat, items in kb['data'].items():
        for item in items:
            kb_flat[item['field']] = item['value']

    result = AIContentGenerator.generate_article(
        company_id, title, article_type, direction, keywords, kb_flat
    )

    return jsonify({'success': True, 'article': result})

@app.route('/api/article/<int:article_id>')
@login_required
def api_get_article(article_id):
    """获取文章详情（校验归属，防跨租户越权）"""
    company_id = session['company_id']
    with get_db() as conn:
        article = conn.execute('SELECT * FROM geo_articles WHERE id = %s AND company_id = %s',
                               (article_id, company_id)).fetchone()
        if not article:
            return jsonify({'error': '文章不存在'}), 404
    return jsonify(dict(article))

@app.route('/api/article/<int:article_id>', methods=['DELETE'])
@login_required
def api_delete_article(article_id):
    """删除文章（校验归属，防跨租户越权）"""
    company_id = session['company_id']
    with get_db() as conn:
        cur = conn.execute('DELETE FROM geo_articles WHERE id = %s AND company_id = %s',
                           (article_id, company_id))
        if cur.rowcount == 0:
            return jsonify({'error': '文章不存在或无权操作'}), 404
    write_audit_log(session['user_id'], 'delete', 'geo_articles', article_id, '删除文章')
    return jsonify({'success': True})

# ==================== 视频创作模块 ====================

@app.route('/ai-video')
@login_required
def ai_video_page():
    """AI视频创作页面"""
    user_info = get_current_user()
    company_id = session['company_id']

    with get_db() as conn:
        videos = conn.execute(
            'SELECT * FROM geo_videos WHERE company_id = %s ORDER BY created_at DESC',
            (company_id,)
        ).fetchall()
        videos = [dict(v) for v in videos]

    return render_template('ai_video.html',
                           user_info=user_info,
                           videos=videos)

@app.route('/api/video/generate', methods=['POST'])
@login_required
@role_required('editor')
def api_generate_video_script():
    """生成视频脚本"""
    data = request.get_json()
    product_info = data.get('product_info', '').strip()
    duration = data.get('duration', '60秒')
    style = data.get('style', 'professional')

    if not product_info:
        return jsonify({'error': '请输入产品信息'}), 400

    company_id = session['company_id']
    kb = KnowledgeBaseManager.get_knowledge_base(company_id)
    kb_flat = {}
    for cat, items in kb['data'].items():
        for item in items:
            kb_flat[item['field']] = item['value']

    result = AIContentGenerator.generate_video_script(
        company_id, product_info, duration, style, kb_flat
    )

    return jsonify({'success': True, 'video': result})

# ==================== 内容资产模块 ====================

@app.route('/content-assets')
@login_required
def content_assets_page():
    """内容资产页面"""
    user_info = get_current_user()
    company_id = session['company_id']

    with get_db() as conn:
        articles = conn.execute(
            'SELECT * FROM geo_articles WHERE company_id = %s ORDER BY created_at DESC',
            (company_id,)
        ).fetchall()
        articles = [dict(a) for a in articles]

        videos = conn.execute(
            'SELECT * FROM geo_videos WHERE company_id = %s ORDER BY created_at DESC',
            (company_id,)
        ).fetchall()
        videos = [dict(v) for v in videos]

        citations = conn.execute(
            'SELECT * FROM citations WHERE company_id = %s ORDER BY created_at DESC',
            (company_id,)
        ).fetchall()
        citations = [dict(c) for c in citations]

    return render_template('content_assets.html',
                           user_info=user_info,
                           articles=articles,
                           videos=videos,
                           citations=citations)

# ==================== 系统管理模块 ====================

@app.route('/settings')
@login_required
@role_required('admin')
def settings_page():
    """系统配置页面"""
    user_info = get_current_user()

    with get_db() as conn:
        configs = conn.execute('SELECT * FROM system_config').fetchall()
        configs = {c['key']: c['value'] for c in configs}
        # API Key 脱敏：不回显明文，仅传递是否已配置状态
        has_api_key = bool(configs.get('ai_api_key'))
        configs['ai_api_key'] = ''

        # 用户列表仅展示当前租户用户，防跨租户信息泄露
        users = conn.execute('SELECT * FROM users WHERE company_id = %s ORDER BY created_at DESC',
                             (session['company_id'],)).fetchall()
        users = [dict(u) for u in users]
        # 用户数据脱敏：不返回密码哈希
        for u in users:
            u.pop('password', None)

    return render_template('settings.html',
                           user_info=user_info,
                           configs=configs,
                           users=users,
                           has_api_key=has_api_key)

@app.route('/api/config/update', methods=['POST'])
@login_required
@role_required('admin')
def api_update_config():
    """更新系统配置（键名白名单，防任意键写入）"""
    data = request.get_json()
    key = data.get('key', '').strip()
    value = data.get('value', '').strip()

    # 键名白名单：仅允许前端可配置项
    allowed_keys = {'ai_api_base', 'ai_model', 'ai_api_key', 'site_name'}
    if key not in allowed_keys:
        return jsonify({'error': '不允许修改该配置项'}), 403

    # API Key 留空表示不修改（避免误覆盖已配置的密钥）
    if key == 'ai_api_key' and not value:
        return jsonify({'success': True})

    with get_db() as conn:
        existing = conn.execute('SELECT `key` FROM system_config WHERE `key` = %s', (key,)).fetchone()
        if existing:
            conn.execute('UPDATE system_config SET `value` = %s, updated_at = NOW() WHERE `key` = %s',
                         (value, key))
        else:
            conn.execute('INSERT INTO system_config (`key`, `value`) VALUES (%s, %s)', (key, value))

    write_audit_log(session['user_id'], 'update', 'system_config', None,
                    '更新配置项: %s' % ('ai_api_key' if key == 'ai_api_key' else key))
    return jsonify({'success': True})

# ==================== 数据看板API ====================

@app.route('/api/dashboard/overview')
@login_required
def api_dashboard_overview():
    """获取看板总览数据"""
    company_id = session['company_id']
    overview = AnalyticsDashboard.get_overview(company_id)
    return jsonify(overview)

@app.route('/health')
def health_check():
    return jsonify({'status': 'healthy', 'service': 'GEO Growth OS', 'version': '1.0.0'})

# ==================== 启动入口 ====================

if __name__ == '__main__':
    # 初始化数据库
    init_db()
    # Debug 模式通过环境变量 FLASK_DEBUG=true 开启（生产环境默认关闭）
    debug_mode = os.environ.get('FLASK_DEBUG', 'false').lower() in ('1', 'true', 'yes')
    # Secret Key 安全校验：生产环境未配置则警告
    _secret = os.environ.get('SECRET_KEY', '')
    if not _secret or _secret == 'geo-growth-os-dev-secret-change-me':
        if not debug_mode:
            print('[WARNING] 生产环境未配置 SECRET_KEY 环境变量！')
            print('[WARNING] 请通过 export SECRET_KEY=$(python -c "import secrets;print(secrets.token_hex(32))") 设置强随机密钥')
        else:
            print('[INFO] 开发模式：使用默认 Secret Key（生产环境必须配置）')
    print('=' * 56)
    print('  [OK] GEO Growth OS v1.0 已启动')
    print('  访问地址: http://localhost:5000')
    print('  Debug 模式: %s' % ('ON' if debug_mode else 'OFF'))
    print('=' * 56)
    app.run(host='0.0.0.0', port=5000, debug=debug_mode)
