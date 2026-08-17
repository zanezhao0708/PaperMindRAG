#!/usr/bin/env python3
"""构建论文浏览页与 RSS 订阅源（无需 LLM Key）：
- digest/index.html  自包含静态页：主题筛选 / 评分过滤 / 关键词搜索 / 近 4 周趋势
- digest/feed.xml    RSS 2.0，最近 20 篇，供 RSS 阅读器订阅

用法: python scripts/build_site.py
"""
import datetime
import json
import os
import re
import sys
from email.utils import format_datetime
from xml.sax.saxutils import escape

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from daily_digest import CST, DIGEST_DIR, RSS_RAW  # noqa: E402
from weekly_digest import parse_digest_file, topic_counts  # noqa: E402

REPO_URL = "https://github.com/zanezhao0708/PaperMindRAG"
SITE_FILE = os.path.join(DIGEST_DIR, "index.html")
FEED_FILE = os.path.join(DIGEST_DIR, "feed.xml")

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<link rel="alternate" type="application/rss+xml"
      title="PaperMind 论文日报 RSS" href="feed.xml">
<title>PaperMind · CV 异常检测论文追踪</title>
<style>
  :root { --bg:#0f1117; --card:#171a23; --line:#2a2f3d; --fg:#e8eaf0;
          --muted:#9aa3b5; --accent:#6c8cff; }
  * { box-sizing:border-box; margin:0; padding:0; }
  body { background:var(--bg); color:var(--fg);
         font-family:"PingFang SC","Microsoft YaHei",system-ui,sans-serif; }
  .wrap { max-width:1080px; margin:0 auto; padding:28px 18px 60px; }
  h1 { font-size:24px; letter-spacing:.5px; } h1 span { color:var(--accent); }
  .sub { color:var(--muted); font-size:13px; margin:6px 0 18px; }
  .sub a { color:var(--accent); text-decoration:none; }
  .btn { display:inline-block; background:var(--accent); color:#fff;
         padding:6px 14px; border-radius:8px; font-size:13px;
         font-weight:600; text-decoration:none; margin-left:6px; }
  .btn:hover { opacity:.88; }
  .toolbar { display:flex; flex-wrap:wrap; gap:8px; margin-bottom:14px;
             align-items:center; }
  .chips { display:flex; flex-wrap:wrap; gap:6px; }
  .chip { background:#1e2330; border:1px solid var(--line); border-radius:999px;
          padding:5px 13px; font-size:13px; color:var(--muted); cursor:pointer;
          text-decoration:none; }
  .chip.on { border-color:var(--accent); color:var(--accent); }
  input, select { background:#11141c; color:var(--fg); border:1px solid var(--line);
          border-radius:8px; padding:7px 12px; font-size:13px; outline:none; }
  input:focus { border-color:var(--accent); }
  .weeks { display:flex; gap:10px; flex-wrap:wrap; margin-bottom:18px; }
  .wcard { background:var(--card); border:1px solid var(--line); border-radius:10px;
           padding:12px 16px; min-width:210px; flex:1; }
  .wcard .d { color:var(--muted); font-size:12px; }
  .wcard .n { font-size:20px; margin:4px 0; }
  .wcard .t { font-size:12px; color:var(--accent); }
  table { width:100%; border-collapse:collapse; background:var(--card);
          border:1px solid var(--line); border-radius:10px; overflow:hidden; }
  th, td { padding:10px 12px; font-size:13.5px; text-align:left;
           border-bottom:1px solid var(--line); line-height:1.6;
           vertical-align:top; }
  th { color:var(--muted); font-weight:600; font-size:12.5px; }
  tr:last-child td { border-bottom:none; }
  td a { color:var(--fg); text-decoration:none; font-weight:600; }
  td a:hover { color:var(--accent); }
  .tag { display:inline-block; background:#1e2330; border:1px solid var(--line);
         border-radius:4px; padding:1px 7px; margin-right:4px; font-size:11.5px;
         color:var(--muted); }
  .stars { color:#f5c518; letter-spacing:1px; white-space:nowrap; }
  .date { color:var(--muted); font-size:12.5px; white-space:nowrap; }
  .empty { text-align:center; color:var(--muted); padding:30px; display:none; }
  .wl { color:var(--muted); font-size:12.5px; margin-top:16px; }
</style>
</head>
<body>
<div class="wrap">
  <h1>Paper<span>Mind</span> 论文追踪</h1>
  <div class="sub">__TOTAL__ 篇 · 更新于 __GENERATED__ ·
    <a href="__REPO__/blob/main/digest/README.md">日报目录</a> ·
    <a class="btn" href="feed.xml"
       title="__RSS_RAW__">订阅 RSS</a></div>
  <div class="toolbar">
    <div class="chips" id="chips"></div>
    <select id="rating">
      <option value="0">全部评分</option>
      <option value="4">★★★★ 以上</option>
      <option value="5">★★★★★</option>
    </select>
    <input id="q" placeholder="搜索标题 / 解读 / 原题…">
  </div>
  <div class="weeks" id="weeks"></div>
  <table>
    <thead><tr><th>日期</th><th>推荐</th><th>标题</th><th>主题</th>
      <th>一句话解读</th></tr></thead>
    <tbody id="rows"></tbody>
  </table>
  <div class="empty" id="empty">没有匹配的论文</div>
  <div class="wl" id="weeklyLinks"></div>
</div>
<script>
const DATA = __DATA__;
let activeTopic = "全部", minRating = 0, query = "";
const starStr = n => "★".repeat(n) + "☆".repeat(5 - n);
const esc = s => String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;");

function renderChips(){
  const counts = {};
  DATA.papers.forEach(p => p.tags.forEach(t => counts[t] = (counts[t]||0)+1));
  const topics = Object.entries(counts).sort((a,b) => b[1]-a[1]);
  document.getElementById("chips").innerHTML =
    [["全部", DATA.papers.length], ...topics]
      .map(([t,c]) => `<span class="chip${t===activeTopic?' on':''}"
        data-t="${t}">${t} ${c}</span>`)
      .join("");
  document.querySelectorAll(".chip").forEach(el =>
    el.onclick = () => { activeTopic = el.dataset.t; renderChips(); renderRows(); });
}
function renderWeeks(){
  document.getElementById("weeks").innerHTML = DATA.weeks.map(w => `
    <div class="wcard"><div class="d">${w.label}</div>
    <div class="n">${w.count} 篇</div><div class="t">${w.top}</div></div>`).join("");
}
function renderRows(){
  const q = query.trim().toLowerCase();
  const list = DATA.papers.filter(p =>
    (activeTopic === "全部" || p.tags.includes(activeTopic)) &&
    p.rating >= minRating &&
    (!q || (p.title_zh + p.oneliner + p.title).toLowerCase().includes(q)));
  document.getElementById("rows").innerHTML = list.map(p => `
    <tr><td class="date">${p.digest_date.slice(5)}</td>
    <td class="stars">${starStr(p.rating)}</td>
    <td><a href="${p.url}" target="_blank" rel="noopener">${esc(p.title_zh)}</a></td>
    <td>${p.tags.map(t => `<span class="tag">${t}</span>`).join("")}</td>
    <td>${esc(p.oneliner)}</td></tr>`).join("");
  document.getElementById("empty").style.display = list.length ? "none" : "block";
}
renderChips(); renderWeeks(); renderRows();
document.getElementById("rating").onchange = e => { minRating = +e.target.value; renderRows(); };
document.getElementById("q").oninput = e => { query = e.target.value; renderRows(); };
const wl = DATA.weeklies.map(w =>
  `<a class="chip" href="weekly/${w}">${w.replace(".md","")}</a>`).join(" ");
document.getElementById("weeklyLinks").innerHTML =
  wl ? "周报归档：" + wl : "";
</script>
</body>
</html>
"""


def load_papers() -> list:
    """全部日报论文，按日报日期倒序、同日按评分倒序。"""
    papers = []
    for fn in os.listdir(DIGEST_DIR):
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}\.md", fn):
            papers += parse_digest_file(os.path.join(DIGEST_DIR, fn))
    return sorted(papers, key=lambda p: (p["digest_date"], p["rating"]), reverse=True)


def week_stats(papers: list, n_weeks: int = 4) -> list:
    """近 n_weeks 个 ISO 周的统计：篇数 + 头部主题 + 环比。"""
    by_week = {}
    for p in papers:
        iso = datetime.date.fromisoformat(p["digest_date"]).isocalendar()
        by_week.setdefault(f"{iso[0]}-W{iso[1]:02d}", []).append(p)
    keys = sorted(by_week)[-n_weeks:]
    stats = []
    for i, key in enumerate(keys):
        ps = by_week[key]
        first = min(p["digest_date"] for p in ps)[5:]
        last = max(p["digest_date"] for p in ps)[5:]
        top = "、".join(f"{t} {n}" for t, n in list(topic_counts(ps).items())[:2]) or "—"
        delta = ""
        if i:
            diff = len(ps) - len(by_week[keys[i - 1]])
            word = "增" if diff > 0 else "减" if diff < 0 else "平"
            delta = f"，环比{word}{abs(diff)}"
        stats.append({"label": f"{key}（{first}~{last}）",
                      "count": len(ps), "top": top + delta})
    return stats


def build_feed(papers: list) -> str:
    """RSS 2.0，最近 20 篇。"""
    now = format_datetime(datetime.datetime.now(CST))
    items = []
    for p in papers[:20]:
        dt = datetime.datetime.fromisoformat(p["date"] + "T08:00:00").replace(tzinfo=CST)
        items.append(
            "  <item>\n"
            f"    <title>{escape(p['title_zh'])}</title>\n"
            f"    <link>{escape(p['url'])}</link>\n"
            f"    <description>{escape(p['oneliner'])}</description>\n"
            f"    <pubDate>{format_datetime(dt)}</pubDate>\n"
            f"    <guid>{escape(p['url'])}</guid>\n"
            "  </item>")
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<rss version=\"2.0\">\n<channel>\n"
        "    <title>PaperMind · CV 异常检测论文日报</title>\n"
        f"    <link>{REPO_URL}/blob/main/digest/README.md</link>\n"
        "    <description>每日 arXiv CV 异常检测论文中文解读</description>\n"
        f"    <lastBuildDate>{now}</lastBuildDate>\n"
        + "\n".join(items) + "\n</channel>\n</rss>\n")


def main():
    papers = load_papers()
    weeklies = []
    wdir = os.path.join(DIGEST_DIR, "weekly")
    if os.path.isdir(wdir):
        weeklies = sorted((f for f in os.listdir(wdir) if f.endswith(".md")),
                          reverse=True)
    data = {
        "generated": datetime.datetime.now(CST).strftime("%Y-%m-%d %H:%M"),
        "total": len(papers),
        "papers": papers,
        "weeks": week_stats(papers),
        "weeklies": weeklies,
    }
    # 防 </script> 提前闭合标签
    data_json = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    html_out = (HTML_TEMPLATE
                .replace("__DATA__", data_json)
                .replace("__TOTAL__", str(len(papers)))
                .replace("__GENERATED__", data["generated"])
                .replace("__REPO__", REPO_URL)
                .replace("__RSS_RAW__", RSS_RAW))
    with open(SITE_FILE, "w", encoding="utf-8") as f:
        f.write(html_out)
    with open(FEED_FILE, "w", encoding="utf-8") as f:
        f.write(build_feed(papers))
    print(f"[站点] index.html ({len(papers)} 篇) + feed.xml 已生成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
