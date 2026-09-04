# 🌸 妇科前沿研究助手

> 个人定制的妇科前沿文献与学术动态追踪工具。基于 [REQUIREMENTS.md](REQUIREMENTS.md) 设计。

## 一键使用

```bash
git clone https://github.com/Li1aid/TCM-Gynecology-frontier-assistant.git
cd TCM-Gynecology-frontier-assistant
./run.sh
```

会自动:① 拉论文 ② 拉学术动态 ③ AI 评级 + 10 字段卡片 ④ 启动本地服务器 ⑤ 浏览器打开看板。

## 项目文件

| 文件 | 作用 |
|---|---|
| `dashboard.html` | 看板前端(4 个 Tab:论文/动态/本周精选/收藏) |
| `fetch_papers.py` | PubMed 论文抓取 + AI 评级(A/B/C)+ 10 字段结构化卡片 |
| `fetch_news.py` | 学术动态抓取 + 8 类分类(指南/共识/改名/诊断/声明/大临床/安全/突破) |
| `journals.json` | 期刊白名单(top/obgyn/crosscut 三类) |
| `manual_news.json` | **手动补录条目**(中文学会等无法自动抓取的来源) |
| `run.sh` | 一键启动 |
| `.env` | API key 等配置 |
| `REQUIREMENTS.md` | 需求文档基线 |
| `com.gyndashboard.daily.plist` | macOS 每日自动调度配置 |

## 刷新为什么变快了(2026-09)

- 新闻 26 个来源**并发抓取**(原来串行 + sleep,慢源会拖到 1 分钟以上)
- 新闻先用 **Haiku 做相关性初筛**(几个 token,1–2 秒),只有相关的才进 Sonnet/Opus 完整评分 —— Sci Am / Live Science 这种综合源每天 90% 都能在初筛被拦下
- Haiku 初筛默认 **20 条一批**，冷缓存时不再为上百条新闻逐条等待网络往返
- PubMed 已解析文章**按 PMID 缓存**(`.pubmed_cache.json`,保留 60 天),每天重复出现的几百篇不再重新 efetch
- Claude 请求改用官方 SDK 的共享连接池，同一轮并发分析可复用 HTTP/TLS 连接
- 论文卡片保留全部字段与关键数字，但限制重复和冗长表述，减少约四成生成等待
- 服务器 `/refresh` **并行**跑论文和动态,总耗时取较慢者
- `./run.sh` 首次启动也并行更新论文和动态
- Claude 并发从 5 提到 10(可用 `AI_CONCURRENCY` 调)
- 前端每 2 秒获取一次完成状态，完成后只重载数据，不再整页刷新
- 刷新按钮旁会按缓存状态显示预计耗时区间，结束后显示实际耗时
- HTML/JSON 支持 gzip 与 ETag，远程打开时首包更小、数据未变化时直接返回 304

## 数据源

### 论文(PubMed 双路检索)
- **主路**:近 N 天妇科相关期刊文章
- **指南路**:近 N×4 天 PubMed `[pt] guideline / consensus / practice bulletin / position statement`(无论期刊在不在白名单都纳入,实现需求"非 Q1 破例")

期刊白名单分三类:
- **top**(顶级综合,只取妇科相关):Lancet/NEJM/JAMA/BMJ/Nature Medicine/Nature/Science/Cell
- **obgyn**(妇产生殖核心,日常重点):Human Reproduction/Fertility & Sterility/Am J OB/GYN/Obstetrics & Gynecology/BJOG 等
- **crosscut**(机制交叉,条件纳入):Nature Aging/Cell Metabolism/Endocrine Reviews 等

### 学术动态(RSS + sitemap + HTML)
| 源 | 类型 | 备注 |
|---|---|---|
| STAT News, Sci Am, Live Science, Healio×2, ScienceDaily×4, News Medical, Nature Reviews Disease Primers | RSS | 学术新闻媒体(首发改名/突破等大事) |
| WHO News | RSS | 全球妇产 |
| FIGO, ACOG, ASRM, NICE | sitemap | 国际学会 |
| ESGO Guidelines, ESMO Guidelines | sitemap | 妇瘤指南专项 |
| ESHRE Guidelines, Endocrine Society Guidelines | HTML | 静态列表抓取 |
| SGO, FDA Press | RSS | 政策类补充 |
| **manual_news.json** | 手动 | **中文学会、个人发现的重要内容** |

**无法自动抓取的来源**(反爬/DNS 不通):ASCO/NCCN/IMS、中华医学会妇产/生殖/抗癌协会等 — 通过 `manual_news.json` 手动补录。但 PubMed 指南路其实可以兜底覆盖大部分(指南文章通常会发表在主流期刊上)。

## 添加手动条目(中文学会等)

打开 `manual_news.json`,在 `items` 数组里加:

```json
{
  "items": [
    {
      "title": "PCOS命名更新与PMOS共识专家解读",
      "headline_zh": "中华妇产分会发布PMOS共识中文解读",
      "source": "中华医学会妇产科学分会",
      "tag": "中华妇产",
      "url": "https://www.chinaobgyn.net/news/xxx.html",
      "date": "2026-05-13",
      "news_type": "consensus",
      "importance": 5,
      "why_matters": "国内权威解读,临床落地参考"
    }
  ]
}
```

下次 `./run.sh` 会自动并入看板。

## 推荐等级(REQUIREMENTS 第八章)

- **A 级**:必读 — 改变疾病认识/诊断/治疗策略;指南、共识、重大临床、Top 顶刊妇科突破
- **B 级**:建议精读 — Q1 妇科相关、设计规范、有新观点
- **C 级**:可泛读 — 主题相关、补充验证
- **FILTER**:不进前台 — AI 判定低质量/低相关

## 看板功能

- **4 个 Tab**:📚 最新论文 / 📰 学术动态 / 📊 本周精选 / ⭐ 收藏
- **A/B/C 等级徽章**:卡片左侧粉/橙/蓝四色条
- **10 字段结构化卡片**:展开后看核心问题/研究设计/主要结果/作者结论/创新点/局限性/推荐理由/下一步研究方向/🌿 中医学迁移分析
- **核心价值黄条**:一句话本文意义
- **收藏 + 标签**:10 个预设标签(精读/写论文可用/选题灵感/中医迁移可思考 等)
- **GitHub Gist 跨设备同步**:点右上角 `Gist…` 配置 token,收藏自动跨设备
- **重大动态红色 Banner**:5 星 + 改名/指南/共识等顶部弹横幅
- **本周精选 Tab**:A 级论文 + 重大动态合并视图

## 每天自动跑(macOS launchd)

**安装**:
```bash
sed "s|__PROJECT_DIR__|$PWD|g" com.gyndashboard.daily.plist \
  > ~/Library/LaunchAgents/com.gyndashboard.daily.plist
launchctl bootstrap "gui/$(id -u)" ~/Library/LaunchAgents/com.gyndashboard.daily.plist
```

每天**早上 8 点**自动跑,只拉最近 1 天的内容。看板任何时候打开 `http://localhost:8765/dashboard.html` 都是最新的。

**修改时间**:编辑 plist 的 `<key>Hour</key>` 和 `<key>Minute</key>`。

**查看日志**:`tail -f daily.log`

**卸载**:
```bash
launchctl bootout "gui/$(id -u)" ~/Library/LaunchAgents/com.gyndashboard.daily.plist
```

**测试一次**:
```bash
launchctl kickstart "gui/$(id -u)/com.gyndashboard.daily"
```

## 配置 GitHub Gist 收藏同步

1. https://github.com/settings/tokens/new 生成 **classic token**,只勾 `gist` 权限
2. 看板右上角点 `⏳ Gist…` 状态条,粘贴 token
3. Gist ID 留空 → 系统自动创建私人 Gist
4. 状态条变 `Gist 已同步 ✓ (N 条)` 即配好

任何设备的看板配同一个 token,收藏列表自动同步。

## 常用命令

```bash
# 拉最近 14 天
DAYS=14 ./run.sh

# 跳过 AI(只看筛选结果)
./run.sh --no-ai

# 单独跑某一路
python3 fetch_papers.py --days 7 --max-ai 50
python3 fetch_news.py --days 7 --top 40

# 清缓存重跑(改了 AI prompt 后用)
rm .extension_cache.json .news_cache.json
./run.sh
```

## 故障排查

| 现象 | 原因 | 解决 |
|---|---|---|
| Failed to fetch data.json | 用了 `file://` 打开 | 必须 `http://localhost:8765/`,启动 `./run.sh` |
| PubMed 502 | 偶发,重试即可 | 直接再跑一次 |
| AI 字段为空 | 旧缓存格式不兼容 | 清 `.extension_cache.json` 重跑 |
| Gist 同步失败 | token 失效或权限不够 | 重新生成 classic token,只勾 `gist` |
| 看板没看到 PCOS 改名 | 缓存未更新 | 强制刷新 `Cmd+Shift+R` |
