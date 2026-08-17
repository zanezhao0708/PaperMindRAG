#!/usr/bin/env python3
"""每周论文汇总：聚合近 7 天日报，与上一个 7 天对比，生成趋势周报。

配合 .github/workflows/weekly.yml 由 GitHub Actions 每周一自动运行。
无需 LLM Key，只聚合已有日报 Markdown。

用法: python scripts/weekly_digest.py
"""
import datetime
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from daily_digest import CST, DIGEST_DIR, ENTRY_RE, detect_topics  # noqa: E402

WEEKLY_DIR = os.path.join(DIGEST_DIR, "weekly")


def parse_digest_file(path: str) -> list:
    """解析单份日报 Markdown，返回论文条目列表。

    digest_date = 日报文件日期（论文出现在哪天的日报）；
    date = 论文在 arXiv 的提交日期。周报/站点按前者聚合。
    """
    digest_date = os.path.basename(path)[:10]
    with open(path, encoding="utf-8") as f:
        text = f.read()
    papers = []
    for m in ENTRY_RE.finditer(text):
        tags = m.group("tags") or detect_topics(m.group("title"), m.group("abstract"))
        papers.append({
            "date": m.group("date"),
            "digest_date": digest_date,
            "title_zh": m.group("title_zh"),
            "title": m.group("title"),
            "url": m.group("url"),
            "oneliner": m.group("oneliner"),
            "rating": m.group("stars").count("★"),
            "tags": [t for t in tags.split("、") if t],
        })
    return papers


def load_all_papers() -> list:
    """读取 digest/ 下全部日报的论文条目。"""
    papers = []
    for fn in os.listdir(DIGEST_DIR):
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}\.md", fn):
            papers += parse_digest_file(os.path.join(DIGEST_DIR, fn))
    return papers


def window(papers: list, start: datetime.date, end: datetime.date) -> list:
    return [p for p in papers
            if start <= datetime.date.fromisoformat(p["digest_date"]) <= end]


def topic_counts(papers: list) -> dict:
    """主题 -> 篇数，按篇数降序。"""
    counts = {}
    for p in papers:
        for t in p["tags"]:
            counts[t] = counts.get(t, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: -kv[1]))


def arrow(delta: int) -> str:
    if delta > 0:
        return f"↑{delta}"
    if delta < 0:
        return f"↓{-delta}"
    return "—"


def render_weekly(cur: list, prev: list, start, end) -> str:
    cc, pc = topic_counts(cur), topic_counts(prev)
    topics = list(dict.fromkeys(list(cc) + list(pc)))
    lines = [
        f"# CV 异常检测论文周报 · {start} ~ {end}",
        "",
        f"> 聚合近 7 天日报 **{len(cur)} 篇**（上期 {len(prev)} 篇，环比 "
        f"{arrow(len(cur) - len(prev))}），平均推荐 "
        f"{sum(p['rating'] for p in cur) / len(cur):.1f} 星。"
        "  [日报目录](../README.md) · [论文浏览页](../index.html)",
        "",
        "## 趋势对比",
        "",
        "| 主题 | 本期 | 上期 | 环比 |",
        "|---|---|---|---|",
    ]
    for t in topics:
        lines.append(f"| {t} | {cc.get(t, 0)} | {pc.get(t, 0)} "
                     f"| {arrow(cc.get(t, 0) - pc.get(t, 0))} |")
    lines += [
        f"| **合计** | **{len(cur)}** | **{len(prev)}** "
        f"| **{arrow(len(cur) - len(prev))}** |",
        "",
        "## 本周必读（★★★★ 以上）",
        "",
    ]
    hot = sorted((p for p in cur if p["rating"] >= 4), key=lambda p: -p["rating"])
    if hot:
        for p in hot:
            lines.append(f"- {'★' * p['rating']}{'☆' * (5 - p['rating'])} "
                         f"[{p['title_zh']}]({p['url']}) — {p['oneliner']}")
    else:
        lines.append("- 本周暂无 4 星以上论文")
    lines += ["", "## 全部论文", "",
              "| 日期 | 推荐 | 标题 | 主题 |", "|---|---|---|---|"]
    for p in sorted(cur, key=lambda p: (-p["rating"], p["date"])):
        lines.append(f"| {p['date']} | {'★' * p['rating']}{'☆' * (5 - p['rating'])} "
                     f"| [{p['title_zh']}]({p['url']}) | {'、'.join(p['tags'])} |")
    lines.append("")
    return "\n".join(lines)


def main():
    today = datetime.datetime.now(CST).date()
    start, end = today - datetime.timedelta(days=6), today
    prev_end = start - datetime.timedelta(days=1)
    prev_start = prev_end - datetime.timedelta(days=6)

    papers = load_all_papers()
    cur = window(papers, start, end)
    prev = window(papers, prev_start, prev_end)
    if not cur:
        print(f"[周报] {start} ~ {end} 无日报数据，跳过")
        return 0

    iso = end.isocalendar()
    os.makedirs(WEEKLY_DIR, exist_ok=True)
    out = os.path.join(WEEKLY_DIR, f"{iso[0]}-W{iso[1]:02d}.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write(render_weekly(cur, prev, start, end))
    print(f"[周报] 已生成: digest/weekly/{os.path.basename(out)} "
          f"(本期 {len(cur)} 篇 / 上期 {len(prev)} 篇)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
