#!/usr/bin/env python3
"""
妇科前沿文献抓取(基于 REQUIREMENTS.md v1)

流程:
1. PubMed 拉取近期妇科论文
2. 期刊白名单匹配(三类: top / obgyn / crosscut)
3. 基础筛选: top 类要求妇科相关 + 高质量;obgyn 类直接通过;crosscut 类要求妇科相关
4. Claude API 一次性返回 10 字段结构化阅读卡片(含 A/B/C 推荐等级 + 中医迁移分析)
5. AI 判定不合格的(等级=过滤)直接不进入前台
6. 输出 data.json
"""
import json
import os
import re
import ssl
import sys
import time
import argparse
import threading
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path
from xml.etree import ElementTree as ET

from anthropic import Anthropic

try:
    import certifi
    SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except Exception:
    SSL_CTX = ssl.create_default_context()
    if os.environ.get("INSECURE_SSL") == "1":
        SSL_CTX.check_hostname = False
        SSL_CTX.verify_mode = ssl.CERT_NONE


def http_open(url, data=None, headers=None, timeout=60):
    req = urllib.request.Request(url, data=data, headers=headers or {})
    return urllib.request.urlopen(req, timeout=timeout, context=SSL_CTX)


ROOT = Path(__file__).parent
_railway_data = Path("/data")
DATA_DIR = Path(os.environ.get("DATA_DIR", str(_railway_data if _railway_data.exists() else ROOT)))
JOURNALS_FILE = ROOT / "journals.json"
OUTPUT_FILE = DATA_DIR / "data.json"
CACHE_FILE = DATA_DIR / ".extension_cache.json"
PUBMED_CACHE_FILE = DATA_DIR / ".pubmed_cache.json"   # pmid -> 已解析文章,避免每天重新 efetch 几百篇

PUBMED_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"

# 妇科相关性关键词(用于 top/crosscut 类的内容过滤)
GYN_KW = re.compile(
    r"\b(gyneco|gynaeco|obstetric|pregnan|prenatal|maternal|fetal|fetus|"
    r"endometri|ovari|cervi|uter|vulva|vagin|menstru|menopaus|hormon|"
    r"hpv|pcos|pmos|polycystic|ivf|fertilit|infertilit|reproduct|contracep|"
    r"breast|placent|preterm|preeclamps|eclamps|postpartum|abortion|"
    r"miscarriage|stillbirth|midwif|gestational|fibroid|adenomyosis|"
    r"oocyt|embryo|estrogen|progester|hrt|hormone replacement|estrobolome|"
    r"sex hormone|female reproductive|women's health|womens health|"
    r"sterility|amenorrhea|dysmenorrhea|menorrhagia|salping|vulvovaginal)\b",
    re.I,
)

GYN_SUBJECT = (
    '("gynecology"[MeSH Terms] OR "gynaecology"[All Fields] OR "gynecology"[All Fields] OR '
    '"gynecologic"[All Fields] OR "obstetrics"[MeSH Terms] OR "obstetrics"[All Fields] OR '
    '"reproductive medicine"[All Fields] OR "endometriosis"[All Fields] OR '
    '"polycystic ovary syndrome"[All Fields] OR "polyendocrine metabolic ovarian syndrome"[All Fields] OR '
    '"ovarian cancer"[All Fields] OR "cervical cancer"[All Fields] OR "endometrial cancer"[All Fields] OR '
    '"menopause"[All Fields] OR "infertility"[All Fields] OR "uterine fibroid"[All Fields] OR '
    '"adenomyosis"[All Fields] OR "female infertility"[All Fields] OR "ovarian aging"[All Fields] OR '
    '"premature ovarian insufficiency"[All Fields] OR "pelvic floor"[All Fields])'
)
SEARCH_TERM = GYN_SUBJECT + ' AND ("journal article"[Publication Type])'

# 指南/共识专项检索(REQUIREMENTS 第五章"非 Q1 破例"+ 第七章指南白名单兜底)
# 用 PubMed [pt] 过滤,无论期刊是否在白名单,只要属指南/共识/practice bulletin/position statement 都纳入
GUIDELINE_SEARCH = GYN_SUBJECT + (
    ' AND ('
    '"guideline"[Publication Type] OR "practice guideline"[Publication Type] OR '
    '"consensus development conference"[Publication Type] OR '
    '"consensus development conference, nih"[Publication Type] OR '
    'practice bulletin[Title/Abstract] OR position statement[Title/Abstract] OR '
    'committee opinion[Title/Abstract] OR consensus statement[Title/Abstract]'
    ')'
)


def load_journals():
    with open(JOURNALS_FILE, encoding="utf-8") as f:
        data = json.load(f)
    return {k.lower().strip(): v for k, v in data["journals"].items()}


def normalize_journal(name: str) -> str:
    s = name.lower().strip()
    s = re.sub(r"[\.\,]", "", s)
    s = re.sub(r"\s+", " ", s)
    return s


def match_journal(journal_name: str, db: dict):
    if not journal_name:
        return None
    norm = normalize_journal(journal_name)
    if norm in db:
        return db[norm]
    stripped = re.sub(r"\s*\([^)]*\)\s*$", "", norm).strip()
    if stripped in db:
        return db[stripped]
    return None


def pubmed_search(days: int, retmax: int = 500, search_term=None, label="search"):
    end = datetime.now().date()
    start = end - timedelta(days=days)
    date_range = f'AND ("{start.strftime("%Y/%m/%d")}"[Date - Publication] : "{end.strftime("%Y/%m/%d")}"[Date - Publication])'
    query = (search_term or SEARCH_TERM) + " " + date_range
    params = {
        "db": "pubmed", "term": query, "retmax": str(retmax),
        "retmode": "json", "sort": "pub_date",
    }
    url = f"{PUBMED_BASE}/esearch.fcgi?" + urllib.parse.urlencode(params)
    print(f"[{label}] {start} -> {end}", flush=True)
    with http_open(url, timeout=30) as resp:
        body = json.loads(resp.read())
    return body.get("esearchresult", {}).get("idlist", [])


def _load_pubmed_cache():
    if PUBMED_CACHE_FILE.exists():
        try:
            return json.load(open(PUBMED_CACHE_FILE, encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_pubmed_cache(c):
    # 只保留最近 60 天抓到的条目,文件不会无限长大
    cutoff = time.time() - 60 * 86400
    c = {k: v for k, v in c.items() if v.get("_cached_at", 0) >= cutoff}
    tmp = PUBMED_CACHE_FILE.with_name(f"{PUBMED_CACHE_FILE.name}.{os.getpid()}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(c, f, ensure_ascii=False)
        os.replace(tmp, PUBMED_CACHE_FILE)
    finally:
        if tmp.exists():
            tmp.unlink()


def pubmed_fetch(pmids):
    """只对没见过的 PMID 调 efetch;每天重复出现的几百篇直接走本地缓存"""
    if not pmids:
        return []
    cache = _load_pubmed_cache()
    out = [dict(cache[p]) for p in pmids if p in cache]
    todo = [p for p in pmids if p not in cache]
    print(f"[fetch] 缓存命中 {len(out)} 篇,需要下载 {len(todo)} 篇", flush=True)
    now = time.time()
    for i in range(0, len(todo), 100):
        chunk = todo[i : i + 100]
        params = {"db": "pubmed", "id": ",".join(chunk), "retmode": "xml"}
        url = f"{PUBMED_BASE}/efetch.fcgi?" + urllib.parse.urlencode(params)
        with http_open(url, timeout=60) as resp:
            xml = resp.read()
        root = ET.fromstring(xml)
        for art in root.findall(".//PubmedArticle"):
            a = parse_article(art)
            out.append(a)
            if a.get("pmid"):
                cache[a["pmid"]] = dict(a, _cached_at=now)
        if i + 100 < len(todo):
            time.sleep(0.4)
    if todo:
        _save_pubmed_cache(cache)
    for a in out:
        a.pop("_cached_at", None)
    return out


def text_of(el):
    if el is None:
        return ""
    return "".join(el.itertext()).strip()


def parse_article(art):
    pmid = text_of(art.find(".//PMID"))
    title = text_of(art.find(".//ArticleTitle"))
    journal = text_of(art.find(".//Journal/Title")) or text_of(art.find(".//ISOAbbreviation"))
    iso = text_of(art.find(".//ISOAbbreviation"))
    abstract_parts = []
    for ab in art.findall(".//Abstract/AbstractText"):
        label = ab.attrib.get("Label", "")
        txt = text_of(ab)
        abstract_parts.append(f"{label}: {txt}" if label else txt)
    abstract = "\n".join(abstract_parts)

    authors = []
    for a in art.findall(".//Author")[:6]:
        last = text_of(a.find("LastName"))
        first = text_of(a.find("ForeName"))
        if last:
            authors.append(f"{last} {first}".strip())
    pub_date = ""
    pd = art.find(".//PubDate")
    if pd is not None:
        y = text_of(pd.find("Year"))
        m = text_of(pd.find("Month"))
        d = text_of(pd.find("Day"))
        pub_date = " ".join(x for x in [y, m, d] if x)

    doi = ""
    for aid in art.findall(".//ArticleId"):
        if aid.attrib.get("IdType") == "doi":
            doi = text_of(aid)
            break

    pub_types = [text_of(pt) for pt in art.findall(".//PublicationType")]
    keywords = [text_of(k) for k in art.findall(".//Keyword")]

    return {
        "pmid": pmid,
        "title": title,
        "journal": journal,
        "journal_iso": iso,
        "authors": authors,
        "pub_date": pub_date,
        "abstract": abstract,
        "doi": doi,
        "pub_types": pub_types,
        "keywords": keywords,
        "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
    }


def classify_article_type(pub_types, abstract):
    """识别文章类型,影响后续判断"""
    pts = " ".join(pub_types).lower()
    if "guideline" in pts or "practice guideline" in pts:
        return "指南"
    if "consensus" in pts:
        return "共识"
    if "meta-analysis" in pts:
        return "Meta 分析"
    if "systematic review" in pts:
        return "系统综述"
    if "randomized controlled trial" in pts:
        return "随机对照试验"
    if "review" in pts:
        return "综述"
    if "clinical trial" in pts:
        return "临床研究"
    if "case reports" in pts:
        return "病例报告"
    if "letter" in pts or "editorial" in pts or "comment" in pts:
        return "评论/社论"
    return "原始研究"


def base_filter(articles, journals_db, allow_non_whitelist_guideline=False):
    """基础筛选:期刊白名单 + 妇科相关性(对 top/crosscut 类)
    allow_non_whitelist_guideline=True 时,即使期刊不在白名单,但属于指南/共识的也保留(非Q1破例)
    """
    out = []
    for a in articles:
        article_type = classify_article_type(a.get("pub_types", []), a.get("abstract", ""))
        if article_type in {"评论/社论", "病例报告"}:
            continue

        meta = match_journal(a["journal"], journals_db) or match_journal(a.get("journal_iso", ""), journals_db)

        # 破例: 非白名单期刊但属指南/共识 -> 标记为 'guideline' tier,使用占位 IF
        is_guideline_type = article_type in {"指南", "共识"} or any(
            kw in (a.get("title", "") + " " + (a.get("abstract") or "")).lower()
            for kw in ["practice bulletin", "position statement", "committee opinion", "consensus statement"]
        )

        if not meta:
            if allow_non_whitelist_guideline and is_guideline_type:
                # 用 -1 占位 IF,看板侧会显示为"指南"
                a["impact_factor"] = 0
                a["jcr_quartile"] = "—"
                a["jcr_category"] = "Guideline/Consensus"
                a["journal_tier"] = "guideline"
                a["article_type"] = article_type if article_type in {"指南", "共识"} else "共识/声明"
                out.append(a)
            continue

        tier = meta.get("tier", "obgyn")
        text = a["title"] + " " + (a.get("abstract") or "") + " " + " ".join(a.get("keywords") or [])
        if tier in {"top", "crosscut"} and not GYN_KW.search(text):
            continue

        a["impact_factor"] = meta["if"]
        a["jcr_quartile"] = meta["quartile"]
        a["jcr_category"] = meta.get("category", "")
        a["journal_tier"] = tier
        a["article_type"] = article_type
        out.append(a)

    tier_rank = {"guideline": 0, "top": 1, "obgyn": 2, "crosscut": 3}
    out.sort(key=lambda x: (tier_rank.get(x["journal_tier"], 9), -float(x.get("impact_factor", 0))))
    return out


def load_cache():
    if CACHE_FILE.exists():
        try:
            return json.load(open(CACHE_FILE, encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_cache(c):
    tmp = CACHE_FILE.with_name(f"{CACHE_FILE.name}.{os.getpid()}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(c, f, ensure_ascii=False, indent=2)
        os.replace(tmp, CACHE_FILE)
    finally:
        if tmp.exists():
            tmp.unlink()


GUIDELINE_TYPE_KEYWORDS = (
    "guideline", "consensus", "practice bulletin",
    "committee opinion", "statement", "position paper",
)


def _is_guideline_like(article: dict) -> bool:
    """判断是否属于指南/共识/学会声明这类权威推荐类文献。
    这类文章不做"中医迁移分析",改做"与历史版本对比"。"""
    at = (article.get("article_type") or "").lower()
    if any(k in at for k in GUIDELINE_TYPE_KEYWORDS):
        return True
    # 标题里命中也算(PubMed article_type 有时缺失)
    title = (article.get("title") or "").lower()
    title_zh_kw = ("指南", "共识", "声明", "委员会意见", "立场文件")
    if any(k in (article.get("title") or "") for k in title_zh_kw):
        return True
    if any(k in title for k in (
        "guideline", "consensus statement", "position statement",
        "practice bulletin", "committee opinion",
    )):
        return True
    return False


TCM_BLOCK = """<tcm_transfer>
中医学迁移分析(重要,不可省略)。判断:
1. 本文疾病认识是否能启发中医病机理解?
2. 文章中的病理机制是否可与中医证候/治法/方药机制研究产生联系?
3. 是否能为中西医结合研究提供切入点?具体如何切入?

特别注意:慢性低度炎症 / 代谢紊乱 / 神经内分泌失衡 / 卵巢微环境 / 纤维化 / 氧化应激 / 免疫异常 等内容更有迁移价值,可对应中医气虚血瘀/痰湿/肝郁/肾虚等证型与活血化瘀/补肾/疏肝/化痰等治法。

如果迁移价值有限,直接说"暂无明显迁移切入点"并简述原因(1 句)。
不要泛泛而谈,要具体到证型/治法/方药/可能的临床或基础研究方向。总计不超过240字。
</tcm_transfer>"""

GUIDELINE_BLOCK = """<guideline_update>
本文属于指南/共识/学会声明。请从下面 4 个角度分析,**不要做中医迁移**:
1. 与既往版本对比:相对上一版或同领域既有指南,关键改动有哪些?
   (诊断标准 / 分级 / 治疗一线方案 / 药物选择 / 筛查阈值 / 推荐强度 等具体改动)
2. 改动背后的循证支撑:基于哪些新证据(大型 RCT / Meta / 真实世界数据 / 新机制理解)?
3. 对临床实践的实际影响:哪些操作需要调整、哪些既有共识被推翻或修正?
4. 对中医妇科研究者的提示:新版是否影响中西医结合研究的对照基准 / 方案设计 / 疗效评价标准?

如果你确实不掌握历史版本的具体内容,在第 1 点直说"无具体历史版本对比信息",然后基于当前版本本身分析 2-4 点。
要具体,不要套话。每点不超过80字,总计不超过360字。
</guideline_update>"""


def _anthropic_text(client, api_key, timeout, **request):
    """复用 SDK 的 HTTP 连接池；独立调用时自动创建并关闭客户端。"""
    owned_client = None
    if client is None:
        owned_client = Anthropic(api_key=api_key, timeout=timeout, max_retries=1)
        client = owned_client
    try:
        message = client.with_options(timeout=timeout).messages.create(**request)
        return "".join(block.text for block in message.content if block.type == "text")
    finally:
        if owned_client is not None:
            owned_client.close()


def claude_card(article, api_key, model="claude-opus-4-7", client=None):
    """生成 10 字段结构化阅读卡片(XML 标签输出,稳定)"""
    is_guideline = _is_guideline_like(article)
    final_block = GUIDELINE_BLOCK if is_guideline else TCM_BLOCK
    prompt = f"""你是资深妇科与中医妇科科研顾问。基于下面这篇论文,生成结构化阅读卡片,服务于中医妇科研究者。

# 论文原始信息
PMID: {article['pmid']}
标题: {article['title']}
期刊: {article['journal']} (IF={article.get('impact_factor', 'N/A')}, {article.get('jcr_quartile', '')}, {article.get('jcr_category', '')})
文章类型(初判): {article.get('article_type', '')}
期刊层级: {article.get('journal_tier', '')} (top=顶级综合 / obgyn=妇产生殖核心 / crosscut=机制交叉)
发表时间: {article['pub_date']}
摘要原文:
{article['abstract'][:4000] if article['abstract'] else '(无摘要)'}

# 输出格式
严格按以下 XML 标签输出,**所有内容用中文**。每个标签内是纯文本/markdown,无需任何转义。
在保留关键数字、结论和研究限制的前提下高度凝练。除 abstract_zh 外,其余文字字段合计不超过1200字。

<title_zh>论文标题的准确中文翻译,保留专业术语,可在括号内保留关键英文缩写</title_zh>

<abstract_zh>
摘要的准确精炼中文翻译,不超过450字。保留关键样本量、效应值、置信区间和 P 值;保留 Background/Methods/Results/Conclusions 等结构标签(译为"背景/方法/结果/结论")。术语规范,不要意译过头。如原文无摘要,基于标题简要说明本文可能内容。
</abstract_zh>

<core_question>用1句话说明本文主要想解决什么科学问题或临床问题,不超过60字</core_question>

<study_design>
说明研究类型(临床研究 / 基础研究 / 机制研究 / 综述 / Meta 分析 / 指南 / 共识 / RCT / 真实世界研究 / 队列研究)。
- 临床研究:补充研究对象、样本量、主要观察指标
- 基础研究:补充模型(细胞/动物)、关键通路或靶点
- Meta/综述:补充纳入研究数、PICO
1-2句,不超过120字。
</study_design>

<main_results>概括最重要发现,保留关键数字,客观不夸大,不超过180字</main_results>

<author_conclusion>作者自己的结论,1句,不外推,不超过80字</author_conclusion>

<innovation>
说明文章新在哪里。从下列角度选择最贴切的 1-3 条:
- 研究对象/疾病认识/机制/方法/临床结论新
- 提出新诊断或新治疗思路
- 对既往研究有明显推进
如果创新性平庸,直接说"创新性有限"。不超过120字。
</innovation>

<limitations>
从下列角度选择本文最主要的 2-3 条局限性:
样本量不足 / 单中心 / 随访时间短 / 观察性研究不能证因果 / 动物外推到临床有限 / 机制验证不完整 / 人群适用性有限 / 缺乏对照 / 异质性高 / 发表偏倚等。合计不超过120字。
</limitations>

<recommendation_grade>A 或 B 或 C 或 FILTER。判断标准(必须严格执行):

- **A 必读**:JCR Q1,IF 优先 ≥10;妇科前沿热点;可能改变疾病认识/诊断/治疗策略/机制理解;或属指南/共识/重大临床研究/高质量综述/Meta/重要机制研究;对科研选题有明显启发。

- **B 建议精读**:JCR Q1,IF ≥5,妇科相关性强;研究设计规范;有一定新观点/新机制/新方法/临床参考价值;但不足以改变领域整体认识。

- **C 可泛读**:主题相关、参考意义一般、创新性一般、可能是补充验证或小样本观察、适合背景资料/写作素材/趋势了解。

- **FILTER 不进入前台**:与妇科相关性弱;研究设计一般;结论重复;创新性不足;样本量小且无新意;对临床/科研/机制理解帮助有限;IF 低且无特殊价值;来源不权威的普通专家意见。

注意:不要机械按 IF 给分,综合判断。指南/共识/疾病改名等学术动态即使 IF 不高也可给 A。
</recommendation_grade>

<grade_reason>用1句话说明给这个等级的理由,不超过60字</grade_reason>

<core_value>用一句话(20字内)概括本文对中医妇科研究者的核心价值,作为列表展示</core_value>

<next_steps>
下一步研究方向(沿本文继续发散),给出3条具体可执行方向,每条不超过45字。可从下列角度任选:
- 换疾病场景 / 换研究人群
- 机制验证 / 动物实验 / 临床研究
- 多组学分析 / 单细胞测序 / 类器官
- 综述选题 / 课题申报方向 / 科普转化
要具体,不要空话。
</next_steps>

{final_block}
"""
    try:
        txt = _anthropic_text(
            client, api_key, 120,
            model=model,
            max_tokens=2600,
            messages=[{"role": "user", "content": prompt}],
        )

        def extract(tag, default=""):
            m = re.search(rf"<{tag}>(.*?)</{tag}>", txt, re.S)
            return m.group(1).strip() if m else default

        return {
            "title_zh": extract("title_zh"),
            "abstract_zh": extract("abstract_zh"),
            "core_question": extract("core_question"),
            "study_design": extract("study_design"),
            "main_results": extract("main_results"),
            "author_conclusion": extract("author_conclusion"),
            "innovation": extract("innovation"),
            "limitations": extract("limitations"),
            "recommendation_grade": extract("recommendation_grade", "C").upper().strip(),
            "grade_reason": extract("grade_reason"),
            "core_value": extract("core_value"),
            "next_steps": extract("next_steps"),
            "tcm_transfer": extract("tcm_transfer"),
            "guideline_update": extract("guideline_update"),
        }
    except Exception as e:
        return {
            "title_zh": "", "abstract_zh": "", "core_question": "",
            "study_design": "", "main_results": "", "author_conclusion": "",
            "innovation": "", "limitations": "",
            "recommendation_grade": "C",
            "grade_reason": f"[AI 生成失败: {e}]",
            "core_value": "", "next_steps": "", "tcm_transfer": "",
        }


GRADE_ORDER = {"A": 0, "B": 1, "C": 2, "FILTER": 99}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--max", type=int, default=500)
    ap.add_argument("--no-ai", action="store_true")
    ap.add_argument("--model", default=os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6"))
    ap.add_argument("--max-ai", type=int, default=50, help="单次最多对多少篇调用 AI(费用控制)")
    args = ap.parse_args()

    journals_db = load_journals()

    # 两路检索互不依赖，并发请求 PubMed。
    with ThreadPoolExecutor(max_workers=2) as ex:
        main_future = ex.submit(
            pubmed_search, args.days, args.max, None, "search-main")
        guide_future = ex.submit(
            pubmed_search, max(args.days * 4, 30), 200,
            GUIDELINE_SEARCH, "search-guideline")
        pmids_main = main_future.result()
        pmids_guide = guide_future.result()
    pmids = list(dict.fromkeys(pmids_main + pmids_guide))
    print(f"[search] 主路 {len(pmids_main)} + 指南路 {len(pmids_guide)} = 合并去重 {len(pmids)} 篇", flush=True)

    articles = pubmed_fetch(pmids)
    print(f"[fetch] 解析 {len(articles)} 篇", flush=True)
    filtered = base_filter(articles, journals_db, allow_non_whitelist_guideline=True)
    print(f"[base-filter] 期刊白名单 + 指南破例 -> {len(filtered)} 篇", flush=True)

    if len(filtered) > args.max_ai:
        print(f"[trim] 限制最多 {args.max_ai} 篇调用 AI(按 tier+IF 排序后取头部)", flush=True)
        filtered = filtered[: args.max_ai]

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not args.no_ai and api_key:
        cache = load_cache()
        # 先把缓存命中的处理掉,只对未缓存的并发调 Claude
        to_score = []
        for a in filtered:
            key = a["pmid"]
            if key in cache and isinstance(cache[key], dict) and cache[key].get("title_zh"):
                a.update(cache[key])
            else:
                to_score.append(a)
        cache_lock = threading.Lock()
        counter = {"n": 0}

        def _score_one(a):
            with cache_lock:
                counter["n"] += 1
                idx = counter["n"]
            print(f"[ai] {idx}/{len(to_score)} [{a['journal_tier']}] {a['title'][:60]}...", flush=True)
            try:
                started = time.monotonic()
                res = claude_card(a, api_key, model=args.model, client=ai_client)
                chars = sum(len(str(v)) for v in res.values())
                print(f"[ai-done] pmid={a['pmid']} {time.monotonic()-started:.1f}s {chars}字", flush=True)
            except Exception as e:
                print(f"[ai-fail] pmid={a['pmid']}: {e}", flush=True)
                return None
            a.update(res)
            return a["pmid"], res

        workers = int(os.environ.get("AI_CONCURRENCY", "10"))
        if to_score:
            print(f"[ai] 待评分 {len(to_score)} 篇,并发 {workers}", flush=True)
            saved_since_checkpoint = 0
            ai_client = Anthropic(api_key=api_key, timeout=120, max_retries=1)
            try:
                with ThreadPoolExecutor(max_workers=workers) as ex:
                    for future in as_completed([ex.submit(_score_one, a) for a in to_score]):
                        result = future.result()
                        if not result:
                            continue
                        pmid, res = result
                        # title_zh 为空通常表示临时 API 失败，不污染长期缓存。
                        if res.get("title_zh"):
                            cache[pmid] = res
                            saved_since_checkpoint += 1
                            if saved_since_checkpoint >= 10:
                                save_cache(cache)
                                saved_since_checkpoint = 0
            finally:
                ai_client.close()
            if saved_since_checkpoint:
                save_cache(cache)

        before = len(filtered)
        filtered = [a for a in filtered if a.get("recommendation_grade", "C") in {"A", "B", "C"}]
        print(f"[ai-filter] AI 评级过滤掉 FILTER 后: {before} -> {len(filtered)} 篇", flush=True)
        filtered.sort(key=lambda x: (
            GRADE_ORDER.get(x.get("recommendation_grade", "C"), 99),
            -float(x.get("impact_factor", 0)),
        ))
    else:
        if not api_key and not args.no_ai:
            print("[warn] 未检测到 ANTHROPIC_API_KEY", flush=True)
        for a in filtered:
            for k in ("title_zh", "abstract_zh", "core_question", "study_design",
                      "main_results", "author_conclusion", "innovation", "limitations",
                      "grade_reason", "core_value", "next_steps", "tcm_transfer",
                      "guideline_update"):
                a.setdefault(k, "")
            a.setdefault("recommendation_grade", "C")

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "params": {"days": args.days, "model": args.model if not args.no_ai else None},
        "count": len(filtered),
        "grade_counts": {
            "A": sum(1 for a in filtered if a.get("recommendation_grade") == "A"),
            "B": sum(1 for a in filtered if a.get("recommendation_grade") == "B"),
            "C": sum(1 for a in filtered if a.get("recommendation_grade") == "C"),
        },
        "articles": filtered,
    }
    json.dump(payload, open(OUTPUT_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"[done] {len(filtered)} 篇写入 {OUTPUT_FILE} (A:{payload['grade_counts']['A']} B:{payload['grade_counts']['B']} C:{payload['grade_counts']['C']})", flush=True)


if __name__ == "__main__":
    sys.exit(main() or 0)
