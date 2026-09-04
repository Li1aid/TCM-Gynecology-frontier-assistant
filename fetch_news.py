#!/usr/bin/env python3
"""
妇科学术动态抓取(基于 REQUIREMENTS.md 第四、十、十一章)

输出每条动态的类型分类(用于看板顶部「本周重点动态」):
- guideline    指南更新
- consensus    共识发布
- rename       疾病命名变化
- diagnostic   诊断/分型/分期/筛查标准更新
- statement    学会声明 / position statement / practice bulletin
- major_trial  可能改变治疗策略的大型临床研究/RCT/真实世界/Meta
- safety       临床安全性重要警示
- breakthrough 顶刊妇科突破
- general      一般行业新闻(进入次要展示或归档)

重大动态(MAJOR=true): guideline/consensus/rename/diagnostic/statement/major_trial/safety/breakthrough
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
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
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

ROOT = Path(__file__).parent
_railway_data = Path("/data")
DATA_DIR = Path(os.environ.get("DATA_DIR", str(_railway_data if _railway_data.exists() else ROOT)))
OUTPUT_FILE = DATA_DIR / "news.json"
CACHE_FILE = DATA_DIR / ".news_cache.json"
MANUAL_FILE = ROOT / "manual_news.json"

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 GynecologyDashboard/2.0"

# RSS 源(优先学术新闻媒体 > 政策学会)
RSS_SOURCES = [
    {"name": "STAT News", "url": "https://www.statnews.com/feed/", "tag": "STAT", "priority": "academic"},
    {"name": "Healio Women's Health", "url": "https://www.healio.com/sws/feed/news/womens-health-ob-gyn", "tag": "Healio 妇产", "priority": "academic"},
    {"name": "Healio Endocrinology", "url": "https://www.healio.com/sws/feed/news/endocrinology", "tag": "Healio 内分泌", "priority": "academic"},
    {"name": "ScienceDaily Gynecology", "url": "https://www.sciencedaily.com/rss/health_medicine/gynecology.xml", "tag": "ScienceDaily 妇科", "priority": "academic"},
    {"name": "ScienceDaily Fertility", "url": "https://www.sciencedaily.com/rss/health_medicine/fertility.xml", "tag": "ScienceDaily 生殖", "priority": "academic"},
    {"name": "ScienceDaily Pregnancy", "url": "https://www.sciencedaily.com/rss/health_medicine/pregnancy_and_childbirth.xml", "tag": "ScienceDaily 孕产", "priority": "academic"},
    {"name": "ScienceDaily Menopause", "url": "https://www.sciencedaily.com/rss/health_medicine/menopause.xml", "tag": "ScienceDaily 围绝经", "priority": "academic"},
    {"name": "ScienceDaily Cervical Cancer", "url": "https://www.sciencedaily.com/rss/health_medicine/cervical_cancer.xml", "tag": "ScienceDaily 宫颈癌", "priority": "academic"},
    {"name": "ScienceDaily Ovarian Cancer", "url": "https://www.sciencedaily.com/rss/health_medicine/ovarian_cancer.xml", "tag": "ScienceDaily 卵巢癌", "priority": "academic"},
    {"name": "ScienceDaily Hormone Disorders", "url": "https://www.sciencedaily.com/rss/health_medicine/hormone_disorders.xml", "tag": "ScienceDaily 内分泌", "priority": "academic"},
    {"name": "ScienceDaily Sexual Health", "url": "https://www.sciencedaily.com/rss/health_medicine/sexual_health.xml", "tag": "ScienceDaily 性健康", "priority": "academic"},
    {"name": "News Medical Women's Health", "url": "https://www.news-medical.net/category/feed/Womens-Health-News.aspx", "tag": "News Medical", "priority": "academic"},
    {"name": "Scientific American", "url": "https://www.scientificamerican.com/platform/syndication/rss/", "tag": "Sci Am", "priority": "academic"},
    {"name": "Live Science", "url": "https://www.livescience.com/feeds.xml", "tag": "Live Science", "priority": "academic"},
    {"name": "Nature Reviews Disease Primers", "url": "https://www.nature.com/nrdp.rss", "tag": "Nat Rev Disease Primers", "priority": "academic"},
    {"name": "SGO", "url": "https://www.sgo.org/feed/", "tag": "SGO", "priority": "policy"},
    {"name": "FDA Press", "url": "https://www.fda.gov/about-fda/contact-fda/stay-informed/rss-feeds/press-releases/rss.xml", "tag": "FDA", "priority": "policy"},
    {"name": "WHO News", "url": "https://www.who.int/rss-feeds/news-english.xml", "tag": "WHO", "priority": "policy"},
]

SITEMAP_SOURCES = [
    {"name": "FIGO", "url": "https://www.figo.org/sitemap.xml", "tag": "FIGO", "url_filter": "/news"},
    {"name": "ACOG", "url": "https://www.acog.org/sitemap.xml", "tag": "ACOG",
     "url_filter": ["/clinical/clinical-guidance/", "/news-and-advocacy/news-articles/",
                    "/clinical/clinical-guidance/committee-statement/", "/clinical/clinical-guidance/practice-advisory/"]},
    {"name": "ASRM", "url": "https://www.asrm.org/sitemap.xml", "tag": "ASRM", "url_filter": "press-releasesbulletins"},
    {"name": "NICE", "url": "https://www.nice.org.uk/sitemap-storyblok.xml", "tag": "NICE 指南", "url_filter": ["/news/", "/guidance/"]},
    {"name": "ESGO Guidelines", "url": "https://guidelines.esgo.org/page-sitemap.xml", "tag": "ESGO 妇瘤指南",
     "url_filter": ["ovarian", "cervical", "endometrial", "vulvar", "vaginal", "uterine", "gestational", "trophoblastic", "fertility"]},
    {"name": "ESMO Guidelines", "url": "https://www.esmo.org/__sitemap__/en-US.xml", "tag": "ESMO 指南",
     "url_filter": ["gynaecological", "ovarian", "cervical", "endometrial", "breast"]},
]


def http_open(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    return urllib.request.urlopen(req, timeout=timeout, context=SSL_CTX)


def text_of(el):
    if el is None:
        return ""
    return "".join(el.itertext()).strip()


def strip_html(s: str) -> str:
    s = re.sub(r"<[^>]+>", " ", s or "")
    s = re.sub(r"&nbsp;|&#160;", " ", s)
    s = re.sub(r"&amp;", "&", s)
    s = re.sub(r"&lt;", "<", s)
    s = re.sub(r"&gt;", ">", s)
    s = re.sub(r"&quot;", '"', s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def parse_date(s: str):
    if not s:
        return None
    try:
        return parsedate_to_datetime(s)
    except Exception:
        pass
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d", "%Y-%m-%dT%H:%M:%S.%fZ"):
        try:
            dt = datetime.strptime(s, fmt)
            return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
        except Exception:
            continue
    return None


def fetch_rss(src, days):
    items = []
    try:
        with http_open(src["url"]) as resp:
            xml = resp.read()
        root = ET.fromstring(xml)
    except Exception as e:
        print(f"[rss-fail] {src['name']}: {e}", flush=True)
        return items
    for el in root.iter():
        if "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    entries = root.findall(".//item") + root.findall(".//entry")
    for it in entries:
        title = text_of(it.find("title"))
        link_el = it.find("link")
        link = ""
        if link_el is not None:
            link = (link_el.attrib.get("href") or link_el.text or "").strip()
        if not link:
            for l in it.findall("link"):
                href = l.attrib.get("href")
                if href:
                    link = href
                    break
        date_raw = text_of(it.find("pubDate")) or text_of(it.find("updated")) or text_of(it.find("published")) or text_of(it.find("date"))
        dt = parse_date(date_raw)
        if dt and dt < cutoff:
            continue
        desc = text_of(it.find("description")) or text_of(it.find("summary")) or text_of(it.find("content"))
        items.append({
            "source": src["name"], "tag": src["tag"], "title": strip_html(title),
            "url": link, "date": dt.isoformat() if dt else "",
            "summary_raw": strip_html(desc)[:1000],
        })
    print(f"[rss] {src['name']:<28} -> {len(items)} 条", flush=True)
    return items


HTML_SOURCES = [
    {"name": "ESHRE Guidelines",
     "url": "https://www.eshre.eu/Guidelines-and-Legal/Guidelines",
     "tag": "ESHRE 指南",
     "link_pattern": r'href="(/en/Guidelines-and-Legal/Guidelines/[^"#?]+)"',
     "base": "https://www.eshre.eu"},
    {"name": "Endocrine Society Guidelines",
     "url": "https://www.endocrine.org/clinical-practice-guidelines/female-reproductive-endocrinology",
     "tag": "Endocrine Society 指南",
     "link_pattern": r'href="(/clinical-practice-guidelines/[^"#?]+)"',
     "base": "https://www.endocrine.org"},
]


def fetch_html_list(src, max_links=30):
    """简单 HTML 链接抽取(对那些没有 RSS/sitemap 但页面结构稳定的指南列表)"""
    items = []
    try:
        with http_open(src["url"]) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
    except Exception as e:
        print(f"[html-fail] {src['name']}: {e}", flush=True)
        return items
    seen = set()
    for m in re.finditer(src["link_pattern"], html):
        path = m.group(1)
        if path in seen:
            continue
        seen.add(path)
        if len(items) >= max_links:
            break
        loc = src["base"] + path
        slug = path.rstrip("/").rsplit("/", 1)[-1].replace("-", " ").replace("_", " ")
        items.append({
            "source": src["name"], "tag": src["tag"],
            "title": slug.title()[:200], "url": loc, "date": "", "summary_raw": "",
        })
    print(f"[html] {src['name']:<28} -> {len(items)} 条", flush=True)
    return items


def fetch_sitemap(src, days, top_n=40):
    items = []
    try:
        with http_open(src["url"]) as resp:
            xml = resp.read()
        root = ET.fromstring(xml)
    except Exception as e:
        print(f"[sitemap-fail] {src['name']}: {e}", flush=True)
        return items
    for el in root.iter():
        if "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    urls = []
    for u in root.findall(".//url"):
        loc = text_of(u.find("loc"))
        lastmod = text_of(u.find("lastmod"))
        dt = parse_date(lastmod)
        flt = src.get("url_filter")
        if flt:
            patterns = [flt] if isinstance(flt, str) else flt
            if not any(p.lower() in loc.lower() for p in patterns):
                continue
        if dt and dt < cutoff:
            continue
        urls.append((loc, dt))
    urls.sort(key=lambda x: x[1] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    urls = urls[:top_n]
    for loc, dt in urls:
        slug = loc.rstrip("/").rsplit("/", 1)[-1].replace("-", " ").replace("_", " ")
        slug = re.sub(r"\.html?$", "", slug)
        title = slug[:200].title() if slug else loc
        items.append({
            "source": src["name"], "tag": src["tag"], "title": title,
            "url": loc, "date": dt.isoformat() if dt else "", "summary_raw": "",
        })
    print(f"[sitemap] {src['name']:<26} -> {len(items)} 条", flush=True)
    return items


def keyword_prefilter(items):
    KW = re.compile(
        r"\b(gyneco|gynaeco|obstetric|pregnan|prenatal|maternal|fetal|fetus|"
        r"endometri|ovari|cervi|uter|vulva|vagin|menstru|menopaus|hormon|"
        r"hpv|pcos|pmos|polycystic|ivf|fertilit|infertilit|reproduct|contracep|"
        r"breast|placent|preterm|preeclamps|eclamps|postpartum|abortion|"
        r"miscarriage|stillbirth|midwif|women|woman|female|mother|maternit|"
        r"hrt|hormone replacement|estrogen|progester|oocyt|endocrin|"
        r"birth control|sex hormone|gestational|fibroid|adenomyosis)\b", re.I)
    DOMAIN_WL = {"SGO", "ACOG", "FIGO", "ASRM", "Healio Women's Health",
                 "ScienceDaily Gynecology", "ScienceDaily Fertility",
                 "ScienceDaily Pregnancy", "ScienceDaily Menopause",
                 "ScienceDaily Cervical Cancer", "ScienceDaily Ovarian Cancer",
                 "ScienceDaily Hormone Disorders", "ScienceDaily Sexual Health",
                 "News Medical Women's Health",
                 "ESGO Guidelines", "ESMO Guidelines",
                 "ESHRE Guidelines", "Endocrine Society Guidelines"}
    BROAD = {"STAT News", "Scientific American", "Live Science", "FDA Press",
             "Healio Endocrinology", "Nature Reviews Disease Primers",
             "WHO News"}
    out = []
    for it in items:
        hay = it["title"] + " " + (it.get("summary_raw") or "") + " " + it.get("url", "")
        if it["source"] in BROAD:
            if KW.search(hay):
                out.append(it)
            continue
        if it["source"] in DOMAIN_WL:
            out.append(it)
            continue
        if KW.search(hay):
            out.append(it)
    return out


SOURCE_RANK = {
    "STAT News": 10, "Scientific American": 9, "Nature Reviews Disease Primers": 9,
    "Healio Women's Health": 8, "Healio Endocrinology": 8, "News Medical Women's Health": 7,
    "ScienceDaily Women's Health": 6, "ScienceDaily Gynecology": 6,
    "ScienceDaily Fertility": 6, "ScienceDaily Pregnancy": 6, "Live Science": 5,
    "SGO": 8, "FDA Press": 9, "ACOG": 9, "FIGO": 7, "ASRM": 8, "NICE": 9,
    "WHO News": 9, "ESGO Guidelines": 9, "ESMO Guidelines": 9,
    "ESHRE Guidelines": 9, "Endocrine Society Guidelines": 8,
}


def keyword_set(text: str):
    STOP_EN = {"the","a","an","of","in","on","for","to","and","or","with","by","from",
               "is","are","was","were","be","been","as","at","new","study","research",
               "could","may","might","show","shows","found","finds","that","this","these",
               "those","small","large","after","before","year","years","month","long",
               "short","more","less","very","much","letter","change","process","required",
               "scientific","effort","gets","got","just","now","here","what","know","into",
               "have","has","will","would","can","should","first","next","last"}
    STOP_ZH = set("的了和及与或者也都还又是为对在以及对于关于了一种以下其本一些这那此即可能进而其中之间多个等等。、,:;()()「」「」<>《》".replace(" ", ""))
    if not text:
        return set()
    text = text.lower()
    tokens = set()
    for w in re.findall(r"[a-z][a-z0-9-]{3,}", text):
        if w not in STOP_EN:
            tokens.add(w)
    cn = re.sub(r"[a-z0-9\s\W_]+", "", text)
    cn = "".join(c for c in cn if c not in STOP_ZH)
    for i in range(len(cn) - 1):
        bigram = cn[i:i+2]
        if len(bigram) == 2:
            tokens.add(bigram)
    return tokens


def jaccard(a, b):
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


EVENT_TOKENS = {"rename","renamed","renaming","更名","改名","approve","approved",
                "approval","批准","consensus","共识","breakthrough","突破",
                "guideline","guidelines","指南","共识","声明"}


def dedupe_semantic(items, threshold=0.22):
    items_sorted = sorted(items, key=lambda x: (
        x.get("importance", 0), SOURCE_RANK.get(x.get("source"), 0)), reverse=True)
    groups = []
    for it in items_sorted:
        kws = keyword_set(it.get("headline_zh", "")) | keyword_set(it.get("title", ""))
        events = kws & EVENT_TOKENS
        if not kws:
            groups.append({"rep": it, "members": [it], "kws": kws, "events": events})
            continue
        best_group, best_sim = None, 0
        for g in groups:
            sim = jaccard(kws, g["kws"])
            shared_events = events & g["events"]
            shared_kws = kws & g["kws"]
            if shared_events and len(shared_kws - EVENT_TOKENS) >= 2:
                sim = max(sim, 0.5)
            if sim > best_sim:
                best_sim, best_group = sim, g
        if best_group and best_sim >= threshold:
            best_group["members"].append(it)
            best_group["kws"] |= kws
            best_group["events"] |= events
        else:
            groups.append({"rep": it, "members": [it], "kws": kws, "events": events})
    out = []
    for g in groups:
        rep = max(g["members"], key=lambda x: (
            x.get("importance", 0), SOURCE_RANK.get(x.get("source"), 0),
            len(x.get("summary_raw", "") or "")))
        also = [{"source": m["source"], "tag": m.get("tag", ""),
                 "url": m["url"], "title": m.get("title", "")}
                for m in g["members"] if m is not rep]
        rep_copy = dict(rep)
        rep_copy["also_reported"] = also
        rep_copy["report_count"] = len(g["members"])
        out.append(rep_copy)
    return out


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


def claude_summarize(item, api_key, model="claude-opus-4-7", client=None):
    """对单条新闻评分 + 分类 + 中文标题(XML 标签输出)"""
    prompt = f"""你是妇科领域科研顾问,正在为一位中医妇科研究者筛选**学术新闻**(不是政策行政/招聘/活动通知)。

来源: {item['source']} ({item['tag']})
标题: {item['title']}
日期: {item.get('date', '')}
摘要: {item.get('summary_raw', '')[:600]}
链接: {item['url']}

# 输出格式
严格按下列 XML 标签输出,中文。标签内是纯文本,无需转义。

<is_relevant>true 或 false (是否与妇科/生殖/孕产/相关癌种/女性激素相关)</is_relevant>

<news_type>从下列选一个最贴切的:
- guideline (指南更新/新发布)
- consensus (共识发布)
- rename (疾病命名/术语变化,如 PCOS→PMOS)
- diagnostic (诊断/分型/分期/筛查标准更新)
- statement (学会声明 / position statement / practice bulletin / recommendation)
- major_trial (可能改变治疗策略的大型临床研究/RCT/真实世界/高质量Meta)
- safety (临床安全性重要警示,药物风险/并发症/筛查风险/孕产安全)
- breakthrough (顶刊/重大突破性研究、机制颠覆)
- general (一般学术新闻、综述、中等研究)
- nonrelevant (无关或几乎无价值)
</news_type>

<importance>1-5 整数。判断:
- 5: rename / guideline 大改 / consensus 首发或大修 / 顶刊突破 / 重大 safety
- 4: 学会重要 statement / 重要 major_trial / FDA 批准 / 重要诊断标准更新
- 3: 一般 major_trial / 重要综述 / 重要机制研究
- 2: 学会会议、捐赠、人事、活动通知、奖项
- 1: 完全无关
</importance>

<headline_zh>中文一句话标题,不超过40字,点明事件具体内容(避免"妇科新进展"等空话)</headline_zh>

<why_matters>对中医妇科研究者的具体意义,30字内</why_matters>

注意:
- "庆祝XX""新主席""捐赠""会议注册""人事任命" → importance 最多 2
- 含 rename/new name/reclassify/consensus/first/breakthrough/approves → 优先 4-5
- "Morning Rounds"/"Health News"等综合多事件汇总标题,headline_zh 必须按 title 的主事件写,不要从 summary 抓相关内容生成
"""
    try:
        txt = _anthropic_text(
            client, api_key, 60,
            model=model,
            max_tokens=600,
            messages=[{"role": "user", "content": prompt}],
        )

        def extract(tag, default=""):
            m = re.search(rf"<{tag}>(.*?)</{tag}>", txt, re.S)
            return m.group(1).strip() if m else default

        is_rel_s = extract("is_relevant", "true").lower()
        try:
            imp = int(re.search(r"\d", extract("importance", "2")).group())
        except Exception:
            imp = 2
        return {
            "is_relevant": is_rel_s.startswith("t"),
            "news_type": extract("news_type", "general"),
            "importance": imp,
            "headline_zh": extract("headline_zh", item["title"][:60]),
            "why_matters": extract("why_matters", ""),
        }
    except Exception as e:
        return {"is_relevant": True, "news_type": "general", "importance": 2,
                "headline_zh": item["title"][:60], "why_matters": f"(AI 失败: {e})"}


def claude_triage(item, api_key, model, client=None):
    """兼容单条调用；实际刷新使用批量初筛以减少网络往返。"""
    return claude_triage_batch([item], api_key, model, client=client)[0]


def _parse_triage_response(text, count):
    """解析批量初筛结果。模型漏答的条目默认放行，避免误删重要新闻。"""
    answers = {}
    for idx, answer in re.findall(
            r'<result\s+id=["\']?(\d+)["\']?\s*>\s*(yes|no)\s*</result>',
            text, re.I):
        i = int(idx)
        if 0 <= i < count:
            answers[i] = answer.lower() == "yes"
    if not answers:
        raise ValueError("无法解析批量初筛结果")
    return [answers.get(i, True) for i in range(count)]


def claude_triage_batch(items, api_key, model, client=None):
    """一次初筛多条新闻，把几十到上百次 API 往返压缩成少量批次。"""
    if not items:
        return []
    rows = []
    for i, item in enumerate(items):
        rows.append(
            f"[{i}] 来源: {item['source']}\n"
            f"标题: {item['title']}\n"
            f"摘要: {item.get('summary_raw', '')[:300]}"
        )
    prompt = """判断下面每条新闻是否与妇科、生殖、孕产、女性相关癌种或女性激素有关。
必须逐条输出且只输出 XML 行，格式为 <result id="0">yes</result> 或 <result id="0">no</result>。

""" + "\n\n".join(rows)
    try:
        txt = _anthropic_text(
            client, api_key, 30,
            model=model,
            max_tokens=max(64, len(items) * 12),
            messages=[{"role": "user", "content": prompt}],
        )
        return _parse_triage_response(txt, len(items))
    except Exception as e:
        print(f"[triage-fail] {len(items)} 条批量初筛失败: {e}", flush=True)
        return [True] * len(items)   # 初筛失败就全部放行,交给完整评分


MAJOR_TYPES = {"guideline", "consensus", "rename", "diagnostic", "statement",
               "major_trial", "safety", "breakthrough"}


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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--no-ai", action="store_true")
    ap.add_argument("--top", type=int, default=40)
    ap.add_argument("--model", default=os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6"))
    args = ap.parse_args()

    # 所有来源并发抓取(原来串行 24 个源 + sleep,慢源如 FIGO/NICE sitemap 会把整体拖到 1 分钟以上)
    jobs = ([(fetch_rss, (src, args.days)) for src in RSS_SOURCES]
            + [(fetch_sitemap, (src, args.days)) for src in SITEMAP_SOURCES]
            + [(fetch_html_list, (src,)) for src in HTML_SOURCES])
    all_items = []
    fetch_workers = int(os.environ.get("FETCH_CONCURRENCY", "8"))
    with ThreadPoolExecutor(max_workers=fetch_workers) as ex:
        futs = [ex.submit(fn, *a) for fn, a in jobs]
        for f in as_completed(futs):
            try:
                all_items.extend(f.result() or [])
            except Exception as e:
                print(f"[fetch-fail] {e}", flush=True)

    print(f"[total] {len(all_items)} 条原始", flush=True)
    items = keyword_prefilter(all_items)
    print(f"[prefilter] {len(items)} 条", flush=True)

    seen = set()
    uniq = []
    for it in items:
        key = it["url"] or it["title"]
        if key not in seen:
            seen.add(key)
            uniq.append(it)
    items = uniq
    print(f"[dedupe-url] {len(items)} 条", flush=True)

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    cache = load_cache()
    if not args.no_ai and api_key:
        # 先把缓存命中的处理掉,只对未缓存的并发调 Claude
        to_score = []
        for it in items:
            key = it["url"] or it["title"]
            if key in cache:
                it.update(cache[key])
            else:
                to_score.append(it)
        workers = int(os.environ.get("AI_CONCURRENCY", "10"))
        triage_model = os.environ.get("AI_TRIAGE_MODEL", "claude-haiku-4-5-20251001")
        triage_batch_size = max(1, int(os.environ.get("AI_TRIAGE_BATCH_SIZE", "20")))
        triage_workers = max(1, int(os.environ.get("AI_TRIAGE_CONCURRENCY", "5")))
        if to_score:
            ai_client = Anthropic(api_key=api_key, timeout=120, max_retries=1)
            relevant_to_score = list(to_score)
            if triage_model:
                batches = [to_score[i:i + triage_batch_size]
                           for i in range(0, len(to_score), triage_batch_size)]
                relevant_to_score = []
                print(f"[triage] {len(to_score)} 条分 {len(batches)} 批,并发 {min(triage_workers, len(batches))}",
                      flush=True)
                with ThreadPoolExecutor(max_workers=triage_workers) as ex:
                    future_batches = {
                        ex.submit(claude_triage_batch, batch, api_key, triage_model, ai_client): batch
                        for batch in batches
                    }
                    for future in as_completed(future_batches):
                        batch = future_batches[future]
                        flags = future.result()
                        for it, is_relevant in zip(batch, flags):
                            if is_relevant:
                                relevant_to_score.append(it)
                            else:
                                judg = {
                                    "is_relevant": False, "news_type": "nonrelevant",
                                    "importance": 1, "headline_zh": it["title"][:60],
                                    "why_matters": "", "triaged_out": True,
                                }
                                it.update(judg)
                                cache[it["url"] or it["title"]] = judg
                save_cache(cache)
                print(f"[triage] 放行 {len(relevant_to_score)}/{len(to_score)} 条进入完整评分", flush=True)

            counter_lock = threading.Lock()
            counter = {"n": 0}

            def _score_one(it):
                with counter_lock:
                    counter["n"] += 1
                    idx = counter["n"]
                print(f"[ai] {idx}/{len(relevant_to_score)} {it['title'][:60]}...", flush=True)
                judg = claude_summarize(it, api_key, model=args.model, client=ai_client)
                it.update(judg)
                return it["url"] or it["title"], judg

            saved_since_checkpoint = 0
            print(f"[ai] 完整评分 {len(relevant_to_score)} 条,并发 {workers}", flush=True)
            with ThreadPoolExecutor(max_workers=workers) as ex:
                for future in as_completed([ex.submit(_score_one, it) for it in relevant_to_score]):
                    key, judg = future.result()
                    # 网络失败结果不缓存，下次刷新会自动重试。
                    if not str(judg.get("why_matters", "")).startswith("(AI 失败:"):
                        cache[key] = judg
                        saved_since_checkpoint += 1
                        if saved_since_checkpoint >= 10:
                            save_cache(cache)
                            saved_since_checkpoint = 0
            if saved_since_checkpoint:
                save_cache(cache)
            ai_client.close()
        items = [x for x in items if x.get("is_relevant", True)]
        before = len(items)
        items = dedupe_semantic(items)
        print(f"[dedupe-semantic] {before} -> {len(items)} 条", flush=True)
        items.sort(key=lambda x: (x.get("importance", 0), x.get("date", "")), reverse=True)
        items = items[: args.top]
    else:
        for it in items:
            it.setdefault("headline_zh", it["title"])
            it.setdefault("importance", 3)
            it.setdefault("why_matters", "")
            it.setdefault("is_relevant", True)
            it.setdefault("news_type", "general")
        items.sort(key=lambda x: x.get("date", ""), reverse=True)
        items = items[: args.top]

    # 合并手动条目(中文学会等无法自动抓取的来源)
    if MANUAL_FILE.exists():
        try:
            manual = json.load(open(MANUAL_FILE, encoding="utf-8")).get("items", [])
            for it in manual:
                it.setdefault("summary_raw", "")
                it.setdefault("tag", it.get("source", ""))
                it.setdefault("is_relevant", True)
                it.setdefault("also_reported", [])
                it.setdefault("report_count", 1)
                it["_manual"] = True
            items = manual + items
            print(f"[manual] 加入手动条目 {len(manual)} 条", flush=True)
        except Exception as e:
            print(f"[manual-fail] {e}", flush=True)

    major = [x for x in items if x.get("news_type") in MAJOR_TYPES and x.get("importance", 0) >= 4]

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "params": {"days": args.days, "model": args.model if not args.no_ai else None},
        "count": len(items),
        "major_count": len(major),
        "news": items,
    }
    json.dump(payload, open(OUTPUT_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"[done] {len(items)} 条写入(重大动态 {len(major)} 条)", flush=True)


if __name__ == "__main__":
    sys.exit(main() or 0)
