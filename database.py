"""
GEO Growth OS - 数据库模型
SQLite 零配置版本：无需安装数据库服务，首次运行自动建库。

设计说明：
  上层代码沿用 MySQL 风格的 %s 占位符与 NOW() 函数，
  由下方的 _ConnWrapper 在运行时转换为 SQLite 语法，因此业务代码无需改动。
"""
import sqlite3
import os
import re
from contextlib import contextmanager
from datetime import datetime
from werkzeug.security import generate_password_hash

DB_PATH = os.environ.get('DB_PATH', os.path.join(os.path.dirname(__file__), 'data', 'geo.db'))


def _to_sqlite_sql(sql):
    """MySQL SQL -> SQLite SQL（仅转换本项目用到的语法）"""
    sql = sql.replace('NOW()', 'CURRENT_TIMESTAMP')
    sql = sql.replace('LONGTEXT', 'TEXT')
    sql = sql.replace('INT PRIMARY KEY AUTO_INCREMENT', 'INTEGER PRIMARY KEY AUTOINCREMENT')
    # 去掉 MySQL 表选项
    sql = re.sub(r'\)\s*ENGINE=InnoDB\s+DEFAULT\s+CHARSET=utf8mb4', ')', sql)
    # 去掉 SQLite 不支持的 ON UPDATE 子句
    sql = re.sub(r'\s+ON\s+UPDATE\s+CURRENT_TIMESTAMP', '', sql)
    # %s 占位符 -> ?（注意跳过 %% 字面量，本项目没有）
    sql = sql.replace('%s', '?')
    return sql


class _ConnWrapper:
    """包装 sqlite3 连接，自动转换 MySQL 风格 SQL。"""

    def __init__(self, conn):
        self._conn = conn
        self._conn.row_factory = sqlite3.Row
        # sqlite3.Row 不支持 .get()，补一层 dict 转换
        self._cursor = conn.cursor()

    def execute(self, sql, args=None):
        sql = _to_sqlite_sql(sql)
        cur = self._cursor.execute(sql, args or ())
        # 返回支持 dict 风格访问的对象
        return _CursorWrapper(cur)

    def executemany(self, sql, args):
        return _CursorWrapper(self._cursor.executemany(_to_sqlite_sql(sql), args))

    def commit(self):
        self._conn.commit()

    def fetchone(self):
        """代理 fetchone（兼容 init_db 里 c.execute() 后直接 c.fetchone() 的写法）"""
        row = self._cursor.fetchone()
        return _Row(row) if row is not None else None

    def fetchall(self):
        return [_Row(r) for r in self._cursor.fetchall()]

    def close(self):
        self._cursor.close()
        self._conn.close()


class _CursorWrapper:
    """让 cursor 结果同时支持 row['key']、row.get() 和 dict()"""

    def __init__(self, cur):
        self._cur = cur

    @property
    def lastrowid(self):
        return self._cur.lastrowid

    @property
    def rowcount(self):
        return self._cur.rowcount

    def fetchone(self):
        row = self._cur.fetchone()
        return _Row(row) if row is not None else None

    def fetchall(self):
        return [_Row(r) for r in self._cur.fetchall()]

    def __iter__(self):
        for r in self._cur:
            yield _Row(r)


class _Row(dict):
    """dict 子类，支持 row['key'] 和 row.get()"""

    def __init__(self, row):
        super().__init__(dict(row))


@contextmanager
def get_db():
    """获取 SQLite 连接（上下文管理器，自动提交与关闭）"""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    wrapper = _ConnWrapper(conn)
    try:
        yield wrapper
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        wrapper.close()


def init_db():
    """初始化数据库表结构（幂等，可重复执行）"""
    with get_db() as conn:
        c = conn

        # 租户/公司表
        c.execute('''CREATE TABLE IF NOT EXISTS companies (
            id INT PRIMARY KEY AUTO_INCREMENT,
            name VARCHAR(255) NOT NULL,
            industry VARCHAR(255),
            website VARCHAR(500),
            description TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4''')

        # 用户表
        c.execute('''CREATE TABLE IF NOT EXISTS users (
            id INT PRIMARY KEY AUTO_INCREMENT,
            company_id INT,
            username VARCHAR(100) UNIQUE NOT NULL,
            password VARCHAR(255) NOT NULL,
            role VARCHAR(50) DEFAULT 'admin',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (company_id) REFERENCES companies(id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4''')

        # 企业知识库表
        c.execute('''CREATE TABLE IF NOT EXISTS knowledge_base (
            id INT PRIMARY KEY AUTO_INCREMENT,
            company_id INT,
            field_name VARCHAR(255) NOT NULL,
            field_value TEXT,
            category VARCHAR(100) DEFAULT 'general',
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            FOREIGN KEY (company_id) REFERENCES companies(id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4''')

        # 产品表
        c.execute('''CREATE TABLE IF NOT EXISTS products (
            id INT PRIMARY KEY AUTO_INCREMENT,
            company_id INT,
            name VARCHAR(255) NOT NULL,
            description TEXT,
            specs TEXT,
            use_cases TEXT,
            keywords TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (company_id) REFERENCES companies(id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4''')

        # AI可见度诊断记录
        c.execute('''CREATE TABLE IF NOT EXISTS visibility_diagnostics (
            id INT PRIMARY KEY AUTO_INCREMENT,
            company_id INT,
            brand_name VARCHAR(255) NOT NULL,
            competitors TEXT,
            keywords TEXT,
            doubao_score DOUBLE DEFAULT 0,
            qianwen_score DOUBLE DEFAULT 0,
            wenxin_score DOUBLE DEFAULT 0,
            kimi_score DOUBLE DEFAULT 0,
            overall_score DOUBLE DEFAULT 0,
            report_data LONGTEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (company_id) REFERENCES companies(id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4''')

        # GEO文章表
        c.execute('''CREATE TABLE IF NOT EXISTS geo_articles (
            id INT PRIMARY KEY AUTO_INCREMENT,
            company_id INT,
            title VARCHAR(500) NOT NULL,
            content LONGTEXT,
            article_type VARCHAR(100) DEFAULT 'industry_insight',
            target_keywords TEXT,
            status VARCHAR(50) DEFAULT 'draft',
            published_url VARCHAR(1000),
            platform VARCHAR(100),
            ai_model VARCHAR(100),
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (company_id) REFERENCES companies(id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4''')

        # GEO视频脚本表
        c.execute('''CREATE TABLE IF NOT EXISTS geo_videos (
            id INT PRIMARY KEY AUTO_INCREMENT,
            company_id INT,
            title VARCHAR(500) NOT NULL,
            script LONGTEXT,
            product_info TEXT,
            duration VARCHAR(50),
            status VARCHAR(50) DEFAULT 'draft',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (company_id) REFERENCES companies(id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4''')

        # 分发渠道表


        # 分发任务表


        # 订单表


        # 引用截图/存证表
        c.execute('''CREATE TABLE IF NOT EXISTS citations (
            id INT PRIMARY KEY AUTO_INCREMENT,
            company_id INT,
            source_type VARCHAR(100),
            source_name VARCHAR(255),
            screenshot_path VARCHAR(1000),
            url VARCHAR(1000),
            description TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (company_id) REFERENCES companies(id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4''')

        # 系统配置表（key 是 MySQL 保留字，需用反引号）
        c.execute('''CREATE TABLE IF NOT EXISTS system_config (
            `key` VARCHAR(100) PRIMARY KEY,
            `value` TEXT,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4''')

        # 操作日志表
        c.execute('''CREATE TABLE IF NOT EXISTS audit_logs (
            id INT PRIMARY KEY AUTO_INCREMENT,
            user_id INT,
            action VARCHAR(100),
            target_table VARCHAR(100),
            target_id INT,
            details TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4''')

        # API 用量追踪表（记录每次 LLM 调用的 token 消耗）
        c.execute('''CREATE TABLE IF NOT EXISTS api_usage (
            id INT PRIMARY KEY AUTO_INCREMENT,
            company_id INT,
            feature VARCHAR(100),
            model VARCHAR(100),
            prompt_tokens INT DEFAULT 0,
            completion_tokens INT DEFAULT 0,
            total_tokens INT DEFAULT 0,
            success TINYINT DEFAULT 1,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (company_id) REFERENCES companies(id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4''')

        # ==================== 演示数据（UPSERT 语义，幂等） ====================

        # 插入默认系统配置
        default_configs = [
            ('ai_api_key', ''),
            ('ai_api_base', 'https://api.openai.com/v1'),
            ('ai_model', 'gpt-4o'),
            ('site_name', 'GEO Growth OS'),
            ('default_company', '1'),
        ]
        for key, value in default_configs:
            c.execute('SELECT `key` FROM system_config WHERE `key` = %s', (key,))
            if not c.fetchone():
                c.execute('INSERT INTO system_config (`key`, `value`) VALUES (%s, %s)', (key, value))

        # 插入/更新演示公司
        c.execute('SELECT id FROM companies WHERE id = 1')
        if c.fetchone():
            c.execute('''UPDATE companies SET name=%s, industry=%s, website=%s, description=%s WHERE id=1''',
                      ('演示企业', '软件和信息技术服务业', 'https://example.com',
                       '用于演示 GEO 诊断的示例企业，可在知识库页面自行修改'))
        else:
            c.execute('''INSERT INTO companies (id, name, industry, website, description)
                        VALUES (%s, %s, %s, %s, %s)''',
                      (1, '演示企业', '软件和信息技术服务业', 'https://example.com',
                       '用于演示 GEO 诊断的示例企业，可在知识库页面自行修改'))

        # 演示用户（密码哈希存储；存在则迁移明文为哈希，不存在则插入）
        c.execute('SELECT id, password FROM users WHERE username = %s', ('admin',))
        admin_row = c.fetchone()
        if admin_row:
            # 兼容旧数据：明文密码自动迁移为哈希（werkzeug 哈希格式包含 $）
            if admin_row['password'] and '$' not in admin_row['password']:
                c.execute('UPDATE users SET password=%s WHERE id=%s',
                          (generate_password_hash(admin_row['password']), admin_row['id']))
        else:
            c.execute('INSERT INTO users (company_id, username, password, role) VALUES (%s, %s, %s, %s)',
                      (1, 'admin', generate_password_hash('admin123'), 'admin'))

        # 全量明文密码迁移（幂等，兼容历史库）
        c.execute('SELECT id, password FROM users')
        for row in c.fetchall():
            if row['password'] and '$' not in row['password']:
                c.execute('UPDATE users SET password=%s WHERE id=%s',
                          (generate_password_hash(row['password']), row['id']))

        # 插入演示知识库数据（通用示例，用户可在页面自行修改）
        demo_kb = [
            ('company_name', '演示企业', 'basic_info'),
            ('company_full_name', '演示企业（示例）', 'basic_info'),
            ('founded_year', '2020', 'basic_info'),
            ('headquarters', '示例城市示例路 1 号', 'basic_info'),
            ('company_size', '小型企业', 'basic_info'),
            ('industry', '软件和信息技术服务业', 'basic_info'),
            ('core_products', '示例产品 A、示例产品 B', 'products'),
            ('key_technologies', '示例技术能力描述', 'technology'),
            ('certifications', '示例资质', 'qualification'),
            ('major_clients', '示例客户案例', 'business'),
            ('brand_slogan', '示例品牌口号', 'brand'),
            ('competitive_advantage', '示例竞争优势描述', 'brand'),
            ('contact_email', 'demo@example.com', 'contact'),
            ('contact_phone', '000-00000000', 'contact'),
        ]
        for field, value, cat in demo_kb:
            c.execute('SELECT id FROM knowledge_base WHERE company_id=1 AND field_name=%s', (field,))
            row = c.fetchone()
            if row:
                c.execute('UPDATE knowledge_base SET field_value=%s, category=%s WHERE id=%s', (value, cat, row['id']))
            else:
                c.execute('INSERT INTO knowledge_base (company_id, field_name, field_value, category) VALUES (%s,%s,%s,%s)',
                          (1, field, value, cat))

        # 插入演示文章
        demo_articles = [
            ('AI搜索时代的品牌突围：GEO优化全攻略', 'industry_insight', 'GEO优化、AI搜索、品牌可见度', 'published', '百家号'),
            ('2026企业AI营销白皮书', 'whitepaper', 'AI营销、数字化转型、GEO', 'published', '公众号'),
            ('大模型引用偏好研究报告', 'research', '大语言模型、引用分析、AI SEO', 'draft', None),
        ]
        for title, atype, kw, status, platform in demo_articles:
            c.execute('SELECT id FROM geo_articles WHERE company_id=1 AND title=%s', (title,))
            if not c.fetchone():
                c.execute('''INSERT INTO geo_articles (company_id, title, article_type, target_keywords, status, platform)
                            VALUES (%s,%s,%s,%s,%s,%s)''', (1, title, atype, kw, status, platform))

        print("[OK] 数据库初始化完成！")
        print("   数据库文件: %s" % DB_PATH)
        print("   默认账号: admin / admin123（首次登录后请立即修改）")


if __name__ == '__main__':
    init_db()
