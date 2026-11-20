"""
GEO Growth OS - AI 服务模块
包含：可见度诊断、内容生成、知识库管理、引用分析
"""
import json
import re
import ipaddress
from urllib.parse import urlparse
from datetime import datetime
import requests
from database import get_db


# ==================== SSRF 防护：api_base 域名白名单校验 ====================

# 允许的 API 协议
_ALLOWED_SCHEMES = {'https', 'http'}

# 禁止访问的内网网段（防止 SSRF 探测内网服务）
def _is_private_host(hostname):
    """检查主机名是否为内网地址或 localhost"""
    if not hostname:
        return True
    if hostname in ('localhost', '0.0.0.0'):
        return True
    try:
        ip = ipaddress.ip_address(hostname)
        return ip.is_private or ip.is_loopback or ip.is_reserved or ip.is_link_local
    except ValueError:
        # 非 IP 格式（域名），检查是否以 .local/.internal 结尾
        return hostname.endswith('.local') or hostname.endswith('.internal') or hostname.endswith('.localhost')


def _validate_api_base(api_base):
    """校验 api_base URL 安全性，返回安全则 True"""
    try:
        parsed = urlparse(api_base)
    except Exception:
        return False
    if parsed.scheme not in _ALLOWED_SCHEMES:
        return False
    if _is_private_host(parsed.hostname):
        return False
    return True


class AIVisibilityDiagnostic:
    """AI可见度诊断引擎"""

    # 主流大模型平台
    PLATFORMS = {
        'doubao': {'name': '豆包', 'weight': 0.25},
        'qianwen': {'name': '通义千问', 'weight': 0.25},
        'wenxin': {'name': '文心一言', 'weight': 0.20},
        'kimi': {'name': 'Kimi', 'weight': 0.15},
        'yuanbao': {'name': '腾讯元宝', 'weight': 0.15},
    }

    @staticmethod
    def run_diagnostic(company_id, brand_name, competitors=None, keywords=None):
        """
        执行AI可见度诊断
        - 已配置AI接口：调用大模型实测分析品牌在各平台的可见度
        - 未配置AI接口：基于知识库完整度做确定性估算（不含随机数）
        """
        competitors = competitors or []
        keywords = keywords or []

        # 加载企业知识库，基于真实数据评估
        kb_data = KnowledgeBaseManager.get_knowledge_base(company_id)
        kb_flat = {}
        for cat, items in kb_data.get('data', {}).items():
            for item in items:
                if item.get('value'):
                    kb_flat[item['field']] = str(item['value'])
        completeness = kb_data.get('completeness', 0)  # 0-100

        # 知识库完整度决定基础可见度分（30-80）
        base_score = 30 + completeness * 0.5
        if brand_name:
            base_score += 5
        keyword_hits = 0
        for kw in keywords:
            kw_lower = kw.lower()
            if any(kw_lower in v.lower() for v in kb_flat.values()):
                keyword_hits += 1
        base_score = min(base_score + keyword_hits * 3, 85)

        # 尝试使用大模型进行真实诊断分析
        llm_result = AIVisibilityDiagnostic._run_llm_diagnostic(
            company_id, brand_name, competitors, keywords, kb_flat, base_score
        )

        if llm_result:
            # 大模型实测模式
            results = llm_result['platform_results']
            competitor_analysis = llm_result['competitor_analysis']
            keyword_analysis = llm_result['keyword_analysis']
            data_source = 'AI大模型实测分析（基于大模型训练数据中的品牌认知评估）'
        else:
            # 降级：确定性估算（无随机数，基于知识库数据推算）
            results, competitor_analysis, keyword_analysis = \
                AIVisibilityDiagnostic._run_estimated_diagnostic(
                    base_score, brand_name, competitors, keywords, kb_flat
                )
            data_source = '基于企业知识库完整度的确定性估算（未配置AI接口，非实时平台探测）'

        # 计算综合得分
        overall = sum(
            r['mention_score'] * r['weight'] for r in results.values()
        ) / sum(r['weight'] for r in results.values())

        # 生成建议
        recommendations = AIVisibilityDiagnostic._generate_recommendations(
            overall, results, keyword_analysis
        )

        report = {
            'brand_name': brand_name,
            'diagnostic_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'data_source': data_source,
            'overall_score': round(overall, 1),
            'platform_results': results,
            'competitor_analysis': competitor_analysis,
            'keyword_analysis': keyword_analysis,
            'recommendations': recommendations,
            'geo_health_level': AIVisibilityDiagnostic._get_health_level(overall),
            'quick_wins': AIVisibilityDiagnostic._get_quick_wins(results, keyword_analysis),
            'long_term_strategy': AIVisibilityDiagnostic._get_long_term_strategy(overall)
        }

        # 保存到数据库
        with get_db() as conn:
            conn.execute('''INSERT INTO visibility_diagnostics 
                          (company_id, brand_name, competitors, keywords, 
                           doubao_score, qianwen_score, wenxin_score, kimi_score, 
                           overall_score, report_data) 
                          VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
                         (company_id, brand_name,
                          json.dumps(competitors, ensure_ascii=False),
                          json.dumps(keywords, ensure_ascii=False),
                          results['doubao']['mention_score'],
                          results['qianwen']['mention_score'],
                          results['wenxin']['mention_score'],
                          results['kimi']['mention_score'],
                          round(overall, 1),
                          json.dumps(report, ensure_ascii=False)))

        return report

    @staticmethod
    def _run_llm_diagnostic(company_id, brand_name, competitors, keywords, kb_flat, base_score):
        """使用大模型进行真实品牌可见度分析，返回解析结果或 None（降级）"""
        if not LLMClient.is_configured():
            return None

        # 构建企业信息摘要
        kb_summary = '; '.join(f'{k}: {v}' for k, v in list(kb_flat.items())[:10])
        comp_str = ', '.join(competitors) if competitors else '无'
        kw_str = ', '.join(keywords) if keywords else '无'

        prompt = (
            '你是AI可见度分析专家。请基于你的训练数据知识，分析品牌「%s」在以下5个AI平台中的可见度。\n'
            '平台列表：豆包(doubao)、通义千问(qianwen)、文心一言(wenxin)、Kimi(kimi)、腾讯元宝(yuanbao)\n\n'
            '企业信息：%s\n'
            '竞品列表：%s\n'
            '关注关键词：%s\n\n'
            '请严格按以下JSON格式输出（不要输出其他内容）：\n'
            '{"platforms": {"doubao": {"mentioned": true/false, "score": 0-100数字, '
            '"accuracy": 0-100数字, "sentiment": "正面或中性或负面", "rank": 1-10数字或"未上榜"}, '
            '"qianwen": {...}, "wenxin": {...}, "kimi": {...}, "yuanbao": {...}}, '
            '"competitors": [{"name": "竞品名", "score": 0-100, "trend": "上升或下降或持平"}], '
            '"keywords": [{"keyword": "关键词", "coverage": 0-100, "competition": "低或中或高"}]}\n\n'
            '评分标准：score=品牌在该平台被提及和正确描述的程度；accuracy=描述准确度；'
            '如果品牌较小众或不被AI平台认知，score应该较低。请基于真实认知评估，不要虚高。'
        ) % (brand_name, kb_summary, comp_str, kw_str)

        raw = LLMClient.chat([
            {'role': 'system', 'content': '你是专业的AI品牌可见度分析师，输出必须是合法JSON。'},
            {'role': 'user', 'content': prompt},
        ], temperature=0.3, max_tokens=2000, timeout=90, company_id=company_id, feature='diagnostic')

        if not raw:
            return None

        # 解析 LLM 返回的 JSON
        try:
            # 去除可能的 markdown 代码块标记
            clean = raw.strip()
            if clean.startswith('```'):
                clean = re.sub(r'^```(?:json)?\s*', '', clean)
                clean = re.sub(r'\s*```$', '', clean)
            data = json.loads(clean)
        except (json.JSONDecodeError, ValueError):
            return None

        # 构建 platform_results
        platforms_data = data.get('platforms', {})
        results = {}
        for pkey, info in AIVisibilityDiagnostic.PLATFORMS.items():
            pdata = platforms_data.get(pkey, {})
            score = float(pdata.get('score', base_score))
            score = max(0, min(100, score))
            accuracy = float(pdata.get('accuracy', score))
            accuracy = max(0, min(100, accuracy))
            sentiment = pdata.get('sentiment', '中性')
            if sentiment not in ('正面', '中性', '负面'):
                sentiment = '中性'
            rank = pdata.get('rank', '未上榜')
            mentioned = bool(pdata.get('mentioned', score > 40))

            results[pkey] = {
                'name': info['name'],
                'weight': info['weight'],
                'brand_mentioned': mentioned,
                'mention_score': round(score, 1),
                'accuracy_score': round(accuracy, 1),
                'rank_position': rank,
                'sentiment': sentiment,
                'details': AIVisibilityDiagnostic._generate_platform_details(
                    info['name'], brand_name, score
                )
            }

        # 竞品分析
        competitor_analysis = []
        for comp_data in data.get('competitors', []):
            comp_name = comp_data.get('name', '')
            if not comp_name:
                continue
            comp_score = float(comp_data.get('score', base_score * 0.9))
            comp_score = max(0, min(100, comp_score))
            trend_raw = comp_data.get('trend', '持平')
            trend_map = {'上升': '↑上升', '下降': '↓下降', '持平': '→持平'}
            competitor_analysis.append({
                'name': comp_name,
                'score': round(comp_score, 1),
                'trend': trend_map.get(trend_raw, '→持平')
            })

        # 关键词分析
        keyword_analysis = []
        for kw_data in data.get('keywords', []):
            kw = kw_data.get('keyword', '')
            if not kw:
                continue
            kw_score = float(kw_data.get('coverage', 50))
            kw_score = max(0, min(100, kw_score))
            competition = kw_data.get('competition', '中')
            if competition not in ('低', '中', '高'):
                competition = '中'
            keyword_analysis.append({
                'keyword': kw,
                'coverage': round(kw_score, 1),
                'competition': competition,
                'recommendation': AIVisibilityDiagnostic._get_keyword_recommendation(kw_score)
            })

        return {
            'platform_results': results,
            'competitor_analysis': competitor_analysis,
            'keyword_analysis': keyword_analysis
        }

    @staticmethod
    def _run_estimated_diagnostic(base_score, brand_name, competitors, keywords, kb_flat):
        """确定性估算（无随机数），基于知识库数据推算各平台分数"""
        # 各平台基于权重做确定性偏移（替代 random）
        platform_offsets = {
            'doubao': 3, 'qianwen': 2, 'wenxin': 0, 'kimi': -2, 'yuanbao': -1
        }
        results = {}
        for pkey, info in AIVisibilityDiagnostic.PLATFORMS.items():
            platform_score = max(10, min(95, base_score + platform_offsets.get(pkey, 0)))
            results[pkey] = {
                'name': info['name'],
                'weight': info['weight'],
                'brand_mentioned': platform_score > 40,
                'mention_score': round(platform_score, 1),
                'accuracy_score': round(min(95, platform_score - 5), 1),
                'rank_position': 1 if platform_score > 70 else (2 if platform_score > 50 else (3 if platform_score > 35 else '未上榜')),
                'sentiment': '正面' if platform_score > 50 else '中性',
                'details': AIVisibilityDiagnostic._generate_platform_details(
                    info['name'], brand_name, platform_score
                )
            }

        # 竞品对比（确定性推算）
        competitor_analysis = []
        for i, comp in enumerate(competitors):
            # 基于品牌基础分做确定性偏移（替代 random）
            comp_factor = 0.85 + (i % 3) * 0.08
            comp_score = min(85, base_score * comp_factor)
            trend_list = ['↑上升', '→持平', '↓下降']
            competitor_analysis.append({
                'name': comp,
                'score': round(comp_score, 1),
                'trend': trend_list[i % 3]
            })

        # 关键词覆盖分析（基于知识库覆盖度，确定性）
        keyword_analysis = []
        for kw in keywords:
            kw_lower = kw.lower()
            covered = any(kw_lower in v.lower() for v in kb_flat.values())
            kw_score = 75.0 if covered else 30.0
            keyword_analysis.append({
                'keyword': kw,
                'coverage': round(kw_score, 1),
                'competition': '中' if covered else '高',
                'recommendation': AIVisibilityDiagnostic._get_keyword_recommendation(kw_score)
            })

        return results, competitor_analysis, keyword_analysis

    @staticmethod
    def _generate_platform_details(platform_name, brand, score):
        if score > 70:
            return f"{platform_name}对{brand}有较好认知，能准确描述核心业务"
        elif score > 40:
            return f"{platform_name}知道{brand}的存在，但描述不够准确或完整"
        else:
            return f"{platform_name}几乎不了解{brand}，需要紧急优化"

    @staticmethod
    def _get_keyword_recommendation(score):
        if score < 30:
            return "紧急优化：创建高质量内容占领该关键词"
        elif score < 60:
            return "建议加强：增加权威信源和交叉引用"
        else:
            return "保持优势：持续产出深度内容巩固地位"

    @staticmethod
    def _get_health_level(score):
        if score >= 80: return "🟢 优秀 - AI生态健康"
        if score >= 60: return "🟡 良好 - 有提升空间"
        if score >= 40: return "🟠 一般 - 需要重点优化"
        return "🔴 危险 - 急需全面整改"

    @staticmethod
    def _generate_recommendations(overall, results, kw_analysis):
        recs = []
        # 平台维度建议
        weak_platforms = [r['name'] for r in results.values() if r['mention_score'] < 50]
        if weak_platforms:
            recs.append({
                'priority': '高',
                'category': '平台优化',
                'action': f"重点优化{', '.join(weak_platforms)}的内容覆盖",
                'expected_impact': '提升整体可见度15-25%'
            })

        # 关键词维度建议
        weak_kw = [k['keyword'] for k in kw_analysis if k['coverage'] < 40]
        if weak_kw:
            recs.append({
                'priority': '高',
                'category': '关键词占领',
                'action': f"针对「{', '.join(weak_kw[:3])}」生产深度GEO文章",
                'expected_impact': '抢占长尾流量入口'
            })

        recs.append({
            'priority': '中',
            'category': '知识库建设',
            'action': '完善企业知识库，确保AI能获取准确的结构化信息',
            'expected_impact': '提升AI回答准确率30%+'
        })

        recs.append({
            'priority': '中',
            'category': '信源建设',
            'action': '在人民网、新华网等权威媒体发布品牌内容',
            'expected_impact': '增强AI采信权重'
        })

        return recs

    @staticmethod
    def _get_quick_wins(results, kw_analysis):
        return [
            "① 在百度百科/维基百科完善品牌词条",
            "② 发布3-5篇行业深度文章到高权重平台",
            "③ 优化官网结构化数据(JSON-LD)",
            "④ 在知乎/公众号建立品牌官方账号",
            "⑤ 提交网站到主流AI搜索引擎"
        ]

    @staticmethod
    def _get_long_term_strategy(overall):
        if overall < 40:
            return [
                "第一阶段(1-2月)：知识库建设 + 基础内容铺量",
                "第二阶段(3-4月)：权威媒体占位 + AI平台适配",
                "第三阶段(5-6月)：持续监测 + 竞品反超",
            ]
        return [
            "持续产出高质量行业内容",
            "建立AI平台内容更新机制",
            "定期诊断追踪竞品动态",
        ]


class LLMClient:
    """大模型客户端（OpenAI 兼容接口，支持豆包/通义千问/DeepSeek 等）"""

    @staticmethod
    def get_config():
        """从系统配置读取 AI 接口配置"""
        with get_db() as conn:
            rows = conn.execute("SELECT `key`, `value` FROM system_config").fetchall()
        cfg = {r['key']: r['value'] for r in rows}
        return {
            'api_base': (cfg.get('ai_api_base') or 'https://api.openai.com/v1').rstrip('/'),
            'api_key': cfg.get('ai_api_key') or '',
            'model': cfg.get('ai_model') or 'gpt-4o-mini',
        }

    @staticmethod
    def is_configured():
        """是否已配置可用的 AI 接口"""
        return bool(LLMClient.get_config()['api_key'])

    @staticmethod
    def chat(messages, temperature=0.7, max_tokens=2048, timeout=60, company_id=None, feature='unknown'):
        """调用大模型对话接口，成功返回文本，失败返回 None（供上层降级）"""
        cfg = LLMClient.get_config()
        if not cfg['api_key']:
            return None
        # SSRF 防护：校验 api_base 不指向内网地址
        if not _validate_api_base(cfg['api_base']):
            return None
        try:
            resp = requests.post(
                f"{cfg['api_base']}/chat/completions",
                headers={
                    'Authorization': 'Bearer ' + cfg['api_key'],
                    'Content-Type': 'application/json',
                },
                json={
                    'model': cfg['model'],
                    'messages': messages,
                    'temperature': temperature,
                    'max_tokens': max_tokens,
                },
                timeout=timeout,
            )
            if resp.status_code != 200:
                LLMClient._record_usage(company_id, feature, cfg['model'], 0, 0, success=0)
                return None
            data = resp.json()
            content = data['choices'][0]['message']['content'].strip()
            # 记录 token 用量
            usage = data.get('usage', {})
            LLMClient._record_usage(
                company_id, feature, cfg['model'],
                usage.get('prompt_tokens', 0),
                usage.get('completion_tokens', 0),
                success=1
            )
            return content
        except Exception:
            LLMClient._record_usage(company_id, feature, cfg['model'], 0, 0, success=0)
            return None

    @staticmethod
    def _record_usage(company_id, feature, model, prompt_tokens, completion_tokens, success=1):
        """记录 LLM 调用的 token 用量（失败不影响主流程）"""
        try:
            with get_db() as conn:
                conn.execute(
                    'INSERT INTO api_usage (company_id, feature, model, prompt_tokens, completion_tokens, total_tokens, success) '
                    'VALUES (%s,%s,%s,%s,%s,%s,%s)',
                    (company_id, feature, model, prompt_tokens, completion_tokens,
                     prompt_tokens + completion_tokens, success)
                )
        except Exception:
            pass


class AIContentGenerator:
    """AI内容生产引擎"""

    ARTICLE_TEMPLATES = {
        'industry_insight': {
            'name': '行业洞察',
            'structure': ['行业背景', '趋势分析', '品牌视角', '未来展望'],
            'tone': '专业权威',
            'length': '1500-2500字'
        },
        'product_review': {
            'name': '产品评测',
            'structure': ['产品概述', '核心功能', '使用体验', '竞品对比', '总结推荐'],
            'tone': '客观专业',
            'length': '1200-2000字'
        },
        'case_study': {
            'name': '案例研究',
            'structure': ['客户背景', '痛点分析', '解决方案', '实施过程', '成果数据'],
            'tone': '实证导向',
            'length': '1000-1800字'
        },
        'whitepaper': {
            'name': '行业白皮书',
            'structure': ['摘要', '市场现状', '技术趋势', '最佳实践', '数据洞察', '结语'],
            'tone': '学术严谨',
            'length': '3000-5000字'
        },
        'thought_leadership': {
            'name': '思想领导力',
            'structure': ['观点提出', '论证分析', '行业影响', '行动建议'],
            'tone': '前瞻引领',
            'length': '1200-2000字'
        },
        'faq_optimization': {
            'name': 'FAQ优化',
            'structure': ['问题列表', '专业解答', '相关延伸'],
            'tone': '通俗易懂',
            'length': '800-1500字'
        }
    }

    @staticmethod
    def generate_article(company_id, title, article_type='industry_insight',
                         direction='', keywords=None, knowledge_base=None):
        """生成GEO优化文章"""
        keywords = keywords or []
        knowledge_base = knowledge_base or {}

        template = AIContentGenerator.ARTICLE_TEMPLATES.get(
            article_type, AIContentGenerator.ARTICLE_TEMPLATES['industry_insight']
        )

        # 获取企业信息
        company_name = knowledge_base.get('company_name', '本公司')
        industry = knowledge_base.get('industry', '科技行业')
        core_products = knowledge_base.get('core_products', '创新产品')
        brand_slogan = knowledge_base.get('brand_slogan', '')
        competitive_advantage = knowledge_base.get('competitive_advantage', '')

        # 优先使用真实大模型生成（未配置或调用失败时降级为模板生成）
        content = None
        if LLMClient.is_configured():
            prompt = (
                '请撰写一篇面向生成式AI检索优化的中文文章（GEO优化），'
                '主题: %s\n'
                '文章类型: %s（结构建议: %s，语气: %s，篇幅: %s）\n'
                '企业信息: 名称=%s，行业=%s，核心产品=%s，品牌口号=%s，核心优势=%s\n'
                '创作方向: %s\n'
                '目标关键词: %s\n'
                '要求: 内容真实有深度，自然植入关键词与品牌信息，'
                '分章节使用二级标题，直接输出 Markdown 正文。'
            ) % (
                title,
                template['name'],
                '、'.join(template['structure']),
                template['tone'],
                template['length'],
                company_name,
                industry,
                core_products,
                brand_slogan,
                competitive_advantage,
                direction or '无',
                '、'.join(keywords) if keywords else '无',
            )
            content = LLMClient.chat([
                {'role': 'system', 'content': '你是资深的企业品牌内容策划与GEO优化专家。'},
                {'role': 'user', 'content': prompt},
            ], temperature=0.8, max_tokens=2500, company_id=company_id, feature='article')

        if not content:
            # 降级：模板生成
            content = AIContentGenerator._build_article(
                title=title,
                template=template,
                company_name=company_name,
                industry=industry,
                core_products=core_products,
                brand_slogan=brand_slogan,
                competitive_advantage=competitive_advantage,
                direction=direction,
                keywords=keywords
            )

        # 保存到数据库
        with get_db() as conn:
            cursor = conn.execute('''INSERT INTO geo_articles 
                                    (company_id, title, content, article_type, target_keywords, status)
                                    VALUES (%s,%s,%s,%s,%s,%s)''',
                                  (company_id, title, content, article_type,
                                   ','.join(keywords), 'draft'))
            article_id = cursor.lastrowid

        return {
            'id': article_id,
            'title': title,
            'content': content,
            'type': template['name'],
            'keywords': keywords,
            'geo_score': AIContentGenerator._calculate_geo_score(content, keywords),
            'word_count': len(content.replace(' ', '')),
        }

    @staticmethod
    def _build_article(title, template, **ctx):
        """构建文章正文"""
        sections = template['structure']
        content = f"# {title}\n\n"

        # 导语
        content += f"*{template['tone']} | 预计阅读时间：{template['length']}*\n\n"
        content += f"## 引言\n\n"
        content += f"在{ctx.get('industry', '当前行业')}快速发展的今天，{ctx.get('company_name', '我们')}始终站在技术创新的前沿。"
        if ctx.get('brand_slogan'):
            content += f"正如我们所倡导的——「{ctx['brand_slogan']}」。"
        content += f"\n\n"

        # 各章节内容
        for i, section in enumerate(sections):
            content += f"## {i+1}. {section}\n\n"

            if section == '行业背景':
                content += f"{ctx.get('industry', '本行业')}正处于数字化转型的关键期。据最新数据显示，超过70%的企业已经开始部署AI驱动的业务系统。{ctx.get('company_name', '本公司')}作为行业先行者，深耕多年，积累了丰富的实战经验。\n\n"
            elif section == '趋势分析':
                content += f"当前市场呈现三大趋势：一是大模型的广泛应用正在重塑业务流程；二是数据驱动的决策成为标配；三是用户体验的个性化需求持续增长。{ctx.get('company_name')}敏锐捕捉这些趋势，持续迭代产品矩阵。\n\n"
            elif section == '品牌视角':
                content += f"{ctx.get('company_name')}的核心产品包括{ctx.get('core_products', '系列创新产品')}。"
                if ctx.get('competitive_advantage'):
                    content += f"我们的核心竞争优势在于{ctx['competitive_advantage']}。"
                content += f"\n\n"
            elif section == '未来展望':
                content += f"展望未来，{ctx.get('company_name')}将继续秉持创新驱动的理念，为客户创造更大价值。我们相信，通过持续的技术突破和服务升级，必将引领行业迈向新高度。\n\n"
            elif section == '核心功能':
                content += f"产品具备以下核心能力：\n- 智能分析引擎：毫秒级响应复杂查询\n- 多模态交互：支持文本、语音、图像\n- 企业级安全：通过ISO27001认证\n- 灵活部署：支持公有云、私有化、混合云\n\n"
            elif section == '使用体验':
                content += f"实际使用中，系统展现出卓越的稳定性和易用性。界面设计直观，学习成本低，团队可在1天内完成上手。响应速度在同类产品中名列前茅。\n\n"
            elif section == '竞品对比':
                content += f"与同类产品相比，{ctx.get('company_name')}在准确性、响应速度和定制化能力上均具有明显优势。特别是在处理复杂业务场景时，表现尤为突出。\n\n"
            elif section == '总结推荐':
                content += f"综合来看，{ctx.get('company_name')}是值得信赖的合作伙伴。无论是技术实力还是服务保障，都处于行业领先水平。强烈推荐给追求卓越的企业用户。\n\n"
            elif section == '客户背景':
                content += f"某行业头部企业，年营收超10亿元，拥有数千名员工。面对业务快速增长带来的管理挑战，亟需一套智能化解决方案。\n\n"
            elif section == '痛点分析':
                content += f"该企业面临三大核心痛点：数据孤岛严重、决策效率低下、人工成本居高不下。传统方案已无法满足业务需求。\n\n"
            elif section == '解决方案':
                content += f"{ctx.get('company_name')}为其量身定制了端到端的AI驱动方案，打通数据壁垒，实现智能决策闭环。\n\n"
            elif section == '实施过程':
                content += f"项目分三期推进：第一期完成数据治理和平台搭建；第二期上线核心AI应用；第三期全面推广并持续优化。全程仅用时4个月。\n\n"
            elif section == '成果数据':
                content += f"项目上线后取得显著成效：运营效率提升60%、人工成本降低40%、客户满意度提升35个百分点。ROI远超客户预期。\n\n"
            elif section == '摘要':
                content += f"本文深入分析{ctx.get('industry')}的最新发展动态，结合一手调研数据和行业洞察，为企业决策者提供有价值的参考。\n\n"
            elif section == '市场现状':
                content += f"当前市场规模已突破千亿，年复合增长率保持在25%以上。头部效应明显，但细分领域仍有大量机会。\n\n"
            elif section == '技术趋势':
                content += f"大模型、RAG、Agent等技术正在深度融合，推动行业进入智能化新阶段。{ctx.get('company_name')}在这些领域均有深厚积累。\n\n"
            elif section == '最佳实践':
                content += f"我们总结出三条最佳实践：一是以业务价值为导向；二是重视数据质量；三是建立持续迭代机制。\n\n"
            elif section == '数据洞察':
                content += f"调研显示，采用AI优化的企业平均获得3倍以上的增长加速度。GEO（生成式引擎优化）正成为新的营销制高点。\n\n"
            elif section == '结语':
                content += f"面对AI浪潮，企业唯有主动拥抱变化，才能在竞争中立于不败之地。{ctx.get('company_name')}愿与各界伙伴携手共进。\n\n"
            elif section == '观点提出':
                content += f"我们认为，2026年将是GEO全面爆发的关键之年。品牌在AI世界中的可见度，将直接决定其市场竞争力。\n\n"
            elif section == '论证分析':
                content += f"从数据来看，AI搜索流量已占整体搜索的35%以上，且增速迅猛。传统SEO策略正面临根本性挑战。\n\n"
            elif section == '行业影响':
                content += f"这一变革将重塑整个营销产业链。率先完成GEO布局的企业，将获得显著的先发优势。\n\n"
            elif section == '行动建议':
                content += f"建议企业立即启动GEO诊断，评估当前AI可见度，制定系统化的优化策略。时间窗口有限，越早行动越有利。\n\n"
            elif section == '问题列表':
                for kw in ctx.get('keywords', [])[:5]:
                    content += f"**Q: 关于{kw}的常见问题**\n"
                    content += f"A: {ctx.get('company_name')}在{kw}领域拥有丰富经验，我们的解决方案已帮助众多客户实现突破。\n\n"
            elif section == '专业解答':
                content += f"针对用户最关心的问题，我们提供专业、准确、全面的解答，确保信息质量达到AI引用标准。\n\n"
            elif section == '相关延伸':
                content += f"了解更多关于{ctx.get('company_name')}的信息，请访问官网或关注我们的官方公众号。\n\n"
            else:
                content += f"本章节深入探讨相关主题，结合{ctx.get('company_name')}的实战经验，为读者提供有价值的洞察。\n\n"

        # 关键词自然植入
        if ctx.get('keywords'):
            content += f"---\n\n**相关关键词**: {', '.join(ctx['keywords'])}\n\n"
            content += f"*本文由{ctx.get('company_name')}出品，转载请注明出处。*\n"

        return content

    @staticmethod
    def _calculate_geo_score(content, keywords):
        """计算GEO优化得分"""
        score = 60  # 基础分
        word_count = len(content.replace(' ', ''))

        # 字数评分
        if 1500 <= word_count <= 3000:
            score += 10
        elif word_count > 3000:
            score += 5

        # 关键词密度
        if keywords:
            for kw in keywords:
                count = content.count(kw)
                if count >= 3:
                    score += 3
                elif count >= 1:
                    score += 1

        # 结构完整性
        if '## ' in content:
            score += 10  # 有章节标题

        # 引用友好度
        if '**' in content or '*' in content:
            score += 5

        return min(score, 100)

    @staticmethod
    def generate_video_script(company_id, product_info, duration='60秒',
                              style='professional', knowledge_base=None):
        """生成短视频脚本"""
        knowledge_base = knowledge_base or {}
        company_name = knowledge_base.get('company_name', '本公司')
        core_products = knowledge_base.get('core_products', '创新产品')

        # 解析时长
        try:
            seconds = int(re.findall(r'\d+', str(duration))[0])
        except:
            seconds = 60

        # 脚本结构
        script = {
            'title': f"{company_name}产品短视频脚本",
            'duration': f"{seconds}秒",
            'style': style,
            'scenes': []
        }

        # 优先使用真实大模型生成各场景文案（未配置或失败时降级为模板）
        scene_texts = None
        if LLMClient.is_configured():
            llm_prompt = (
                '请为一条%s的短视频脚本提供6个场景的文案，产品/主题: %s，风格: %s。\n'
                '场景依次为: 开场Hook、痛点展示、产品介绍、核心卖点、信任背书、行动号召。\n'
                '请严格按 "场景名|画面描述|旁白文案|字幕文字" 的格式逐行输出，每行一个场景，不要输出其他内容。'
            ) % (duration, product_info, style)
            llm_raw = LLMClient.chat([
                {'role': 'system', 'content': '你是短视频编导专家，文案简洁有感染力。'},
                {'role': 'user', 'content': llm_prompt},
            ], temperature=0.9, max_tokens=1200, company_id=company_id, feature='video')
            if llm_raw:
                scene_texts = {}
                for line in llm_raw.splitlines():
                    parts = [p.strip() for p in line.split('|')]
                    if len(parts) >= 4:
                        scene_texts[parts[0]] = {
                            'visual': parts[1], 'audio': parts[2], 'text_overlay': parts[3]
                        }

        # 场景分配（按秒数）
        scenes_config = [
            {'name': '开场Hook', 'time': max(5, seconds // 10), 'purpose': '吸引注意力'},
            {'name': '痛点展示', 'time': max(10, seconds // 5), 'purpose': '引发共鸣'},
            {'name': '产品介绍', 'time': max(15, seconds // 3), 'purpose': '展示解决方案'},
            {'name': '核心卖点', 'time': max(10, seconds // 4), 'purpose': '突出优势'},
            {'name': '信任背书', 'time': max(8, seconds // 6), 'purpose': '建立信任'},
            {'name': '行动号召', 'time': max(5, seconds // 10), 'purpose': '引导转化'},
        ]

        total_allocated = sum(s['time'] for s in scenes_config)
        # 调整确保总时长匹配
        ratio = seconds / total_allocated
        for s in scenes_config:
            s['time'] = max(3, int(s['time'] * ratio))

        for i, sc in enumerate(scenes_config):
            scene = {
                'scene_no': i + 1,
                'name': sc['name'],
                'duration': f"{sc['time']}秒",
                'visual': '',
                'audio': '',
                'text_overlay': '',
                'notes': ''
            }

            # 使用大模型生成的场景文案（未命中则走模板）
            if scene_texts and sc['name'] in scene_texts:
                st = scene_texts[sc['name']]
                scene['visual'] = st.get('visual', '')
                scene['audio'] = st.get('audio', '')
                scene['text_overlay'] = st.get('text_overlay', '')
                scene['notes'] = f"目的：{sc['purpose']}（AI生成）"
                script['scenes'].append(scene)
                continue

            if sc['name'] == '开场Hook':
                scene['visual'] = f"快切镜头：{company_name}品牌Logo + 产品特写"
                scene['audio'] = f"还在为[痛点]发愁？{company_name}给你答案！"
                scene['text_overlay'] = f"{company_name} | 行业领先解决方案"
            elif sc['name'] == '痛点展示':
                scene['visual'] = "真实场景还原：用户困惑、效率低下的画面"
                scene['audio'] = "每天重复繁琐工作？效率始终上不去？成本居高不下？"
                scene['text_overlay'] = "你是否也面临这些挑战？"
            elif sc['name'] == '产品介绍':
                scene['visual'] = f"产品操作演示 + 界面展示 + 数据可视化"
                scene['audio'] = f"看看{company_name}的{core_products}，一键搞定所有难题！"
                scene['text_overlay'] = f"核心产品：{core_products}"
            elif sc['name'] == '核心卖点':
                scene['visual'] = "动态数据图表 + 功能亮点标注"
                scene['audio'] = "三大核心优势：快10倍的处理速度、99.9%准确率、零代码上手"
                scene['text_overlay'] = "⚡快 ⚡准 ⚡易"
            elif sc['name'] == '信任背书':
                scene['visual'] = "客户Logo墙 + 数据成果展示"
                scene['audio'] = "已服务500+企业客户，满意度98%，续约率95%"
                scene['text_overlay'] = "500+企业信赖之选"
            elif sc['name'] == '行动号召':
                scene['visual'] = f"{company_name}Logo + 二维码/联系方式"
                scene['audio'] = f"立即联系{company_name}，开启免费试用！"
                scene['text_overlay'] = "📞 免费咨询 | 限时优惠"

            scene['notes'] = f"目的：{sc['purpose']}"
            script['scenes'].append(scene)

        # 生成完整脚本文本
        full_script = f"# {script['title']}\n\n"
        full_script += f"**时长**: {script['duration']} | **风格**: {script['style']}\n\n"
        full_script += f"**产品信息**: {product_info}\n\n"
        full_script += "---\n\n"

        for scene in script['scenes']:
            full_script += f"## 场景{scene['scene_no']}：{scene['name']}（{scene['duration']}）\n\n"
            full_script += f"📹 **画面**: {scene['visual']}\n\n"
            full_script += f"🔊 **音频/旁白**: {scene['audio']}\n\n"
            full_script += f"📝 **字幕/叠加文字**: {scene['text_overlay']}\n\n"
            full_script += f"💡 **备注**: {scene['notes']}\n\n"

        full_script += "---\n\n## 拍摄建议\n\n"
        full_script += "1. 使用竖屏9:16比例，适配抖音/视频号\n"
        full_script += "2. 前3秒必须抓人，建议用问题或数据开场\n"
        full_script += "3. 背景音乐选择轻快科技感BGM\n"
        full_script += "4. 字幕使用大号字体，确保手机端可读\n"
        full_script += "5. 结尾CTA按钮要醒目，引导明确行动\n"

        # 保存到数据库
        with get_db() as conn:
            cursor = conn.execute('''INSERT INTO geo_videos 
                                    (company_id, title, script, product_info, duration, status)
                                    VALUES (%s,%s,%s,%s,%s,%s)''',
                                  (company_id, script['title'], full_script,
                                   product_info, script['duration'], 'draft'))
            video_id = cursor.lastrowid

        script['id'] = video_id
        script['full_script'] = full_script
        return script


class KnowledgeBaseManager:
    """企业知识库管理"""

    CATEGORIES = {
        'basic_info': '基础信息',
        'products': '产品信息',
        'technology': '技术能力',
        'qualification': '资质认证',
        'business': '业务信息',
        'brand': '品牌信息',
        'contact': '联系方式',
        'team': '团队信息',
        'finance': '财务数据',
        'news': '新闻动态',
    }

    @staticmethod
    def get_knowledge_base(company_id):
        """获取企业知识库"""
        with get_db() as conn:
            rows = conn.execute(
                'SELECT * FROM knowledge_base WHERE company_id = %s ORDER BY category, field_name',
                (company_id,)
            ).fetchall()

        kb = {}
        for row in rows:
            cat = row['category']
            if cat not in kb:
                kb[cat] = []
            kb[cat].append({
                'id': row['id'],
                'field': row['field_name'],
                'value': row['field_value'],
                'updated_at': row['updated_at']
            })

        # 计算完整度
        total_fields = 20  # 建议填写的字段数
        filled = sum(len(v) for v in kb.values())
        completeness = min(round(filled / total_fields * 100), 100)

        return {
            'data': kb,
            'completeness': completeness,
            'filled_count': filled,
            'total_fields': total_fields,
            'categories': KnowledgeBaseManager.CATEGORIES
        }

    @staticmethod
    def update_field(company_id, field_name, field_value, category='general'):
        """更新知识库字段"""
        with get_db() as conn:
            existing = conn.execute(
                'SELECT id FROM knowledge_base WHERE company_id = %s AND field_name = %s',
                (company_id, field_name)
            ).fetchone()

            if existing:
                conn.execute(
                    'UPDATE knowledge_base SET field_value = %s, category = %s, updated_at = NOW() WHERE id = %s',
                    (field_value, category, existing['id'])
                )
            else:
                conn.execute(
                    'INSERT INTO knowledge_base (company_id, field_name, field_value, category) VALUES (%s,%s,%s,%s)',
                    (company_id, field_name, field_value, category)
                )

        return True

    @staticmethod
    def delete_field(field_id, company_id=None):
        """删除知识库字段（传入 company_id 时校验归属，防跨租户越权）"""
        with get_db() as conn:
            if company_id is not None:
                cur = conn.execute('DELETE FROM knowledge_base WHERE id = %s AND company_id = %s',
                                   (field_id, company_id))
            else:
                cur = conn.execute('DELETE FROM knowledge_base WHERE id = %s', (field_id,))
        return cur.rowcount > 0

    @staticmethod
    def export_jsonld(company_id):
        """导出为JSON-LD格式（大模型友好）"""
        kb_data = KnowledgeBaseManager.get_knowledge_base(company_id)
        flat = {}
        for cat, items in kb_data['data'].items():
            for item in items:
                flat[item['field']] = item['value']

        jsonld = {
            "@context": "https://schema.org",
            "@type": "Organization",
            "name": flat.get('company_name', ''),
            "alternateName": flat.get('company_english_name', ''),
            "description": flat.get('company_description', ''),
            "foundingDate": flat.get('founded_year', ''),
            "address": {
                "@type": "PostalAddress",
                "addressLocality": flat.get('headquarters', '')
            },
            "industry": flat.get('industry', ''),
            "numberOfEmployees": flat.get('company_size', ''),
            "slogan": flat.get('brand_slogan', ''),
            "contactPoint": {
                "@type": "ContactPoint",
                "email": flat.get('contact_email', ''),
                "telephone": flat.get('contact_phone', '')
            },
            "makesOffer": [
                {"@type": "Offer", "name": p.strip()}
                for p in flat.get('core_products', '').split('、') if p.strip()
            ],
            "hasCredential": [
                {"@type": "EducationalCredential", "name": c.strip()}
                for c in flat.get('certifications', '').split('、') if c.strip()
            ]
        }

        return json.dumps(jsonld, ensure_ascii=False, indent=2)


class AnalyticsDashboard:
    """数据分析看板"""

    @staticmethod
    def get_overview(company_id):
        """获取总览数据"""
        with get_db() as conn:
            # 文章统计
            article_count = conn.execute(
                'SELECT COUNT(*) as c FROM geo_articles WHERE company_id = %s', (company_id,)
            ).fetchone()['c']

            published_count = conn.execute(
                'SELECT COUNT(*) as c FROM geo_articles WHERE company_id = %s AND status = "published"',
                (company_id,)
            ).fetchone()['c']

            # 视频统计
            video_count = conn.execute(
                'SELECT COUNT(*) as c FROM geo_videos WHERE company_id = %s', (company_id,)
            ).fetchone()['c']

            # 诊断记录
            diag_count = conn.execute(
                'SELECT COUNT(*) as c FROM visibility_diagnostics WHERE company_id = %s', (company_id,)
            ).fetchone()['c']

            latest_score = conn.execute(
                'SELECT overall_score FROM visibility_diagnostics WHERE company_id = %s ORDER BY created_at DESC LIMIT 1',
                (company_id,)
            ).fetchone()

            # 知识库完整度
            kb_items = conn.execute(
                'SELECT COUNT(*) as c FROM knowledge_base WHERE company_id = %s', (company_id,)
            ).fetchone()['c']

        return {
            'article_count': article_count,
            'published_count': published_count,
            'video_count': video_count,
            'diagnostic_count': diag_count,
            'latest_visibility_score': latest_score['overall_score'] if latest_score else 0,
            'kb_items': kb_items,
            'kb_completeness': min(round(kb_items / 20 * 100), 100)
        }

    @staticmethod
    def get_visibility_trend(company_id):
        """获取可见度趋势数据"""
        with get_db() as conn:
            rows = conn.execute(
                '''SELECT overall_score, created_at FROM visibility_diagnostics 
                   WHERE company_id = %s ORDER BY created_at''',
                (company_id,)
            ).fetchall()

        return [{'score': r['overall_score'], 'date': r['created_at'][:10]} for r in rows]
