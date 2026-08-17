#!/usr/bin/env python3
"""每日 arXiv 论文日报：抓取 -> LLM 中文解读 -> 生成 Markdown 日报。

配合 .github/workflows/daily.yml 由 GitHub Actions 每天自动运行，
把最新 CV 异常检测论文的中文解读提交到 digest/ 目录。

用法: python scripts/daily_digest.py
依赖: requests（GitHub Actions 无需装完整 RAG 依赖即可跑本脚本）
"""
import datetime
import json
import os
import re
import sys
import xml.etree.ElementTree as ET

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIGEST_DIR = os.path.join(ROOT, "digest")
SEEN_FILE = os.path.join(DIGEST_DIR, "seen.json")
# 订阅入口（GitHub Pages 托管浏览页；RSS 为 raw 直链）
PAGES_URL = "https://zanezhao0708.github.io/PaperMindRAG/"
RSS_RAW = ("https://raw.githubusercontent.com/zanezhao0708/"
           "PaperMindRAG/main/digest/feed.xml")

# ---------- 配置 ----------
ARXIV_API = "https://export.arxiv.org/api/query"
ARXIV_QUERY = 'cat:cs.CV AND abs:"anomaly detection"'  # CV类别+摘要关键词
MAX_RESULTS = 50          # 单次抓取条数
DAYS_BACK = 2             # 时间窗口（arXiv 隔日更新，取2天防漏）
LLM_TIMEOUT = 60
CST = datetime.timezone(datetime.timedelta(hours=8))  # 日报按北京时间命名


def load_api_key() -> str:
    """优先环境变量，其次项目根 .env（本地运行用）。"""
    key = os.environ.get("PM_API_KEY", "")
    if key:
        return key
    env_path = os.path.join(ROOT, ".env")
    if os.path.exists(env_path):
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                if line.startswith("PM_API_KEY="):
                    return line.partition("=")[2].strip()
    return ""


API_KEY = load_api_key()


# ================= 一、抓取 arXiv =================
def fetch_papers() -> list:
    """调用 arXiv API，返回最近 DAYS_BACK 天内的论文列表。"""
    resp = requests.get(ARXIV_API, params={
        "search_query": ARXIV_QUERY,
        "sortBy": "submittedDate", "sortOrder": "descending",
        "max_results": MAX_RESULTS,
    }, headers={"User-Agent": "PaperMind-digest/1.0 (research tracker)"},
        timeout=60)
    resp.raise_for_status()

    ns = {"a": "http://www.w3.org/2005/Atom"}
    root = ET.fromstring(resp.text)
    papers = []
    for e in root.findall("a:entry", ns):
        raw_id = e.find("a:id", ns).text.split("/abs/")[-1]
        pid = re.sub(r"v\d+$", "", raw_id)  # 去掉版本号
        published = datetime.datetime.fromisoformat(
            e.find("a:published", ns).text.replace("Z", "+00:00"))
        authors = [a.find("a:name", ns).text
                   for a in e.findall("a:author", ns)]
        papers.append({
            "id": pid,
            "url": f"https://arxiv.org/abs/{pid}",
            "title": re.sub(r"\s+", " ", e.find("a:title", ns).text).strip(),
            "authors": authors,
            "abstract": re.sub(r"\s+", " ", e.find("a:summary", ns).text).strip(),
            "date": published.astimezone(CST).strftime("%Y-%m-%d"),
            "_dt": published,
        })
    return papers


def load_seen() -> set:
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE, encoding="utf-8") as f:
            return set(json.load(f))
    return set()


# ================= 二、LLM 中文解读 =================
def interpret(paper: dict) -> dict:
    """DeepSeek 解读单篇论文；单篇瞬时失败则降级为原题（重跑时自动重试）。"""
    prompt = (
        "你是CV论文解读助手。根据标题和摘要输出JSON（不要多余文字）：\n"
        '{"title_zh":"中文标题(意译)","oneliner":"一句话说清做了什么(完整句子,<=120字)",'
        '"highlights":"方法亮点/创新点(<=90字)","tags":"从[视频,工业,医学,联邦学习,'
        '基准,多模态,少样本,3D]选1-2个最相关标签,逗号分隔",'
        '"rating":1到5整数'
        '(5=方法/结果重磅突破,4=明显创新,3=扎实增量,2=常规改进,1=价值有限)}\n\n'
        f"标题: {paper['title']}\n摘要: {paper['abstract']}"
    )
    fallback = {"title_zh": paper["title"], "oneliner": paper["abstract"][:120] + "…",
                "highlights": "（LLM 解读失败，见原文摘要）", "rating": 3,
                "tags": detect_topics(paper["title"], paper["abstract"])}
    import time as _time
    for attempt in range(3):  # 代理/网络抖动重试
        try:
            r = requests.post(
                "https://api.deepseek.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {API_KEY}"},
                json={"model": "deepseek-chat", "temperature": 0.2,
                      "messages": [{"role": "user", "content": prompt}]},
                timeout=LLM_TIMEOUT)
            r.raise_for_status()
            content = r.json()["choices"][0]["message"]["content"].strip()
            content = re.sub(r"^```(json)?|```$", "", content, flags=re.M).strip()
            data = json.loads(content)
            rating = int(data.get("rating", 3))
            return {"title_zh": data["title_zh"][:60],
                    "oneliner": data["oneliner"][:120],
                    "highlights": data["highlights"][:180],
                    "tags": detect_topics(paper["title"], paper["abstract"],
                                          data.get("tags", "")),
                    "rating": max(1, min(5, rating))}
        except Exception as e:
            if attempt == 2:
                print(f"[解读失败] {paper['id']}: {e}")
                return fallback
            _time.sleep(2 * (attempt + 1))


# ================= 三、日报生成 =================
def stars(n: int) -> str:
    return "★" * n + "☆" * (5 - n)


TOPIC_RULES = [
    ("视频", ["video", "vad", "surveillance", "temporal"]),
    ("工业", ["industrial", "manufacturing", "defect", "inspection", "electrode"]),
    ("医学", ["alzheimer", "medical", "mri", "clinical", "neuro"]),
    ("联邦学习", ["federated"]),
    ("基准", ["benchmark", "dataset"]),
    ("多模态", ["multimodal", "multi-modal", "x-ray"]),
    ("少样本", ["few-shot", "few shot"]),
    ("3D", ["3d", "pose", "sparse view", "reconstruction"]),
]


def detect_topics(title: str, abstract: str, llm_tags: str = "") -> str:
    """优先用 LLM 给的标签（限定在已知词表内），否则按关键词规则兜底。"""
    valid = {name for name, _ in TOPIC_RULES}
    tags = [t.strip() for t in re.split(r"[,，/、\s]+", llm_tags) if t.strip() in valid]
    if not tags:
        low = f"{title} {abstract}".lower()
        tags = [name for name, kws in TOPIC_RULES if any(k in low for k in kws)]
    return "、".join(tags[:2])


def summarize(papers: list) -> str:
    """生成「今日概览」：主题分布 + 高分论文。"""
    counts = {}
    for p in papers:
        for t in p["interp"]["tags"].split("、"):
            if t:
                counts[t] = counts.get(t, 0) + 1
    top = sorted(counts.items(), key=lambda kv: -kv[1])[:3]
    hot = [p for p in papers if p["interp"]["rating"] >= 4]
    parts = [f"本期 {len(papers)} 篇"]
    if top:
        parts.append("主题集中在" + "、".join(f"{t}（{n} 篇）" for t, n in top))
    if hot:
        parts.append("推荐：" + "；".join(p["interp"]["title_zh"] for p in hot))
    return "，".join(parts) + "。"


ENTRY_RE = re.compile(
    r"^### \d+\. (?P<title_zh>.+?) (?P<stars>[★☆]{5})\n"
    r"\n"
    r"\*\*原题\*\*: (?P<title>.+?)  \n"
    r"\*\*作者\*\*: (?P<authors>.+?)  \n"
    r"\*\*提交\*\*: (?P<date>\d{4}-\d{2}-\d{2}) · \[论文链接\]\((?P<url>https://arxiv\.org/abs/(?P<id>[\d.]+))\)\n"
    r"(?:\*\*主题\*\*: (?P<tags>.+?)  \n)?"
    r"\n"
    r"- \*\*做了什么\*\*: (?P<oneliner>.*)\n"
    r"- \*\*亮点\*\*: (?P<highlights>.*)\n"
    r"- \*\*原文摘要\*\*: (?P<abstract>.*)\n",
    re.M,
)


def is_failed(paper: dict) -> bool:
    """判断条目是否为 LLM 解读失败的降级内容。"""
    return "LLM 解读失败" in paper["interp"]["highlights"]


def parse_digest(path: str) -> list:
    """读回当天已有日报的条目，用于重跑时保留成功解读、重试失败条目。"""
    with open(path, encoding="utf-8") as f:
        text = f.read()
    papers = []
    for m in ENTRY_RE.finditer(text):
        tags = m.group("tags") or detect_topics(m.group("title"), m.group("abstract"))
        papers.append({
            "id": m.group("id"),
            "url": m.group("url"),
            "title": m.group("title"),
            "authors": [],
            "authors_display": m.group("authors"),
            "abstract": m.group("abstract").rstrip("…"),
            "date": m.group("date"),
            "interp": {
                "title_zh": m.group("title_zh"),
                "oneliner": m.group("oneliner"),
                "highlights": m.group("highlights"),
                "tags": tags,
                "rating": m.group("stars").count("★"),
            },
        })
    return papers


def render_md(papers: list, date_str: str) -> str:
    """按评分降序生成当日日报 Markdown。"""
    papers = sorted(papers, key=lambda p: -p["interp"]["rating"])
    lines = [
        f"# CV 异常检测论文日报 · {date_str}",
        "",
        f"> 自动抓取 arXiv cs.CV 近 {DAYS_BACK} 天 "
        f"`anomaly detection` 相关论文 {len(papers)} 篇，DeepSeek 中文解读，"
        "按推荐度排序。[订阅历史](./README.md)",
        "",
        f"**今日概览**：{summarize(papers)}",
        "",
        "| 推荐 | 中文标题 | 主题 | 一句话解读 | 链接 |",
        "|---|---|---|---|---|",
    ]
    for p in papers:
        it = p["interp"]
        one = it["oneliner"].replace("|", "\\|")
        lines.append(f"| {stars(it['rating'])} | **{it['title_zh']}** "
                     f"| {it['tags']} | {one} | [abs]({p['url']}) |")
    lines.append("\n## 论文详情\n")
    for i, p in enumerate(papers, 1):
        it = p["interp"]
        if p.get("authors_display"):  # 重跑时从已有日报读回的作者行
            authors_line = p["authors_display"]
        else:
            authors_line = ', '.join(p['authors'][:6]) \
                + (f" 等 {len(p['authors'])} 人" if len(p['authors']) > 6 else "")
        lines += [
            f"### {i}. {it['title_zh']} {stars(it['rating'])}",
            "",
            f"**原题**: {p['title']}  ",
            f"**作者**: {authors_line}  ",
            f"**提交**: {p['date']} · [论文链接]({p['url']})",
            f"**主题**: {it['tags']}  ",
            "",
            f"- **做了什么**: {it['oneliner']}",
            f"- **亮点**: {it['highlights']}",
            f"- **原文摘要**: {p['abstract'][:600]}…",
            "",
        ]
    return "\n".join(lines)


def update_index():
    """重建 digest/README.md 日报目录（倒序，最新在前）。"""
    files = sorted((f for f in os.listdir(DIGEST_DIR)
                    if re.fullmatch(r"\d{4}-\d{2}-\d{2}\.md", f)), reverse=True)
    lines = ["# 每日论文日报（自动更新）", "",
             "GitHub Actions 每天自动抓取 arXiv CV 异常检测方向新论文并生成中文解读。", "",
             "## 订阅方式", "",
             f"- **[论文浏览页（在线）]({PAGES_URL})** 主题筛选 / 搜索 / 趋势",
             f"- **[RSS 订阅]({RSS_RAW})** 复制链接加到任意 RSS 阅读器"
             "（浏览页已埋自动发现标签，阅读器可直接识别）",
             "",
             "## 周报", ""]
    weekly_dir = os.path.join(DIGEST_DIR, "weekly")
    if os.path.isdir(weekly_dir):
        lines += [f"- [{w[:-3]}](./weekly/{w})"
                  for w in sorted(os.listdir(weekly_dir), reverse=True)
                  if w.endswith(".md")] or ["- 暂无周报（每周一自动生成）"]
    else:
        lines.append("- 暂无周报（每周一自动生成）")
    lines += ["", "## 日报", ""]
    lines += [f"- [{d[:-3]}]({d})" for d in files] or ["暂无日报"]
    with open(os.path.join(DIGEST_DIR, "README.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def main():
    if not API_KEY:
        print("[错误] 未配置 PM_API_KEY（环境变量或项目根 .env）。"
              "缺少 key 时全部解读会降级为英文内容，已停止生成，不产出坏日报。")
        return 1

    os.makedirs(DIGEST_DIR, exist_ok=True)
    papers = fetch_papers()
    print(f"[抓取] 命中 {len(papers)} 篇（按提交时间倒序）")

    date_str = datetime.datetime.now(CST).strftime("%Y-%m-%d")
    out = os.path.join(DIGEST_DIR, f"{date_str}.md")

    seen = load_seen()
    fresh = [p for p in papers if p["id"] not in seen]
    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=DAYS_BACK)
    recent = [p for p in fresh if p["_dt"] >= cutoff]
    # 周末/节假日 arXiv 不更新时，回退取最新未推送论文，保证日报有内容
    target = recent or fresh[:10]

    # 当天重跑：已有日报中解读完好的条目原样保留，失败降级条目重新解读
    existing = parse_digest(out) if os.path.exists(out) else []
    ok = [p for p in existing if not is_failed(p)]
    ok_ids = {p["id"] for p in ok}
    redo = [p for p in existing if p["id"] not in ok_ids]
    target = [p for p in target if p["id"] not in ok_ids]

    todo = target + redo
    print(f"[筛选] 未推送 {len(fresh)} 篇，其中近{DAYS_BACK}天 {len(recent)} 篇；"
          f"保留完好条目 {len(ok)} 篇，重试失败条目 {len(redo)} 篇")
    if not todo:
        print("[完成] 无新增论文且已有条目解读完好，跳过")
        return 0

    for p in todo:  # 逐篇 LLM 解读（单篇失败自动降级，重跑时可修复）
        p["interp"] = interpret(p)
        print(f"[解读] {stars(p['interp']['rating'])} {p['interp']['title_zh'][:40]}")

    final = ok + todo
    with open(out, "w", encoding="utf-8") as f:
        f.write(render_md(final, date_str))
    seen |= {p["id"] for p in final}

    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(seen), f, ensure_ascii=False, indent=0)
    update_index()
    print(f"[完成] 日报已生成: digest/{date_str}.md ({len(final)} 篇)")


if __name__ == "__main__":
    sys.exit(main())
