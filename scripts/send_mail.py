#!/usr/bin/env python3
"""可选：把当日日报通过 SMTP 邮件推送到指定邮箱。

环境变量（全部就绪才发送，缺失时跳过且 exit 0，不影响 CI）：
  PM_SMTP_HOST  SMTP 服务器，如 smtp.qq.com
  PM_SMTP_PORT  端口，默认 465（SSL）
  PM_SMTP_USER  发件账号
  PM_SMTP_PASS  密码/授权码（QQ 邮箱为授权码）
  PM_MAIL_TO    收件人，逗号分隔

用法: python scripts/send_mail.py [YYYY-MM-DD]
"""
import datetime
import html
import os
import smtplib
import sys
from email.header import Header
from email.mime.text import MIMEText

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from daily_digest import CST, DIGEST_DIR  # noqa: E402
from weekly_digest import parse_digest_file  # noqa: E402


def build_html(papers: list, date_str: str) -> str:
    rows = []
    for p in sorted(papers, key=lambda p: -p["rating"]):
        stars = "★" * p["rating"] + "☆" * (5 - p["rating"])
        rows.append(
            "<tr>"
            f"<td style='color:#f5c518;white-space:nowrap'>{stars}</td>"
            f"<td><a href='{p['url']}'>{html.escape(p['title_zh'])}</a>"
            f"<br><small style='color:#888'>{' · '.join(p['tags'])}</small></td>"
            f"<td>{html.escape(p['oneliner'])}</td>"
            "</tr>")
    return (
        f"<h2 style='font-family:sans-serif'>CV 异常检测论文日报 · {date_str}"
        f"（{len(papers)} 篇）</h2>"
        "<table cellpadding='6' style='font-family:sans-serif;"
        "border-collapse:collapse'>"
        "<tr><th align='left'>推荐</th><th align='left'>标题</th>"
        "<th align='left'>一句话解读</th></tr>"
        + "".join(rows) + "</table>"
        "<p style='color:#888;font-family:sans-serif'>由 "
        "<a href='https://github.com/zanezhao0708/PaperMindRAG'>PaperMind</a>"
        " 自动推送</p>")


def main():
    host = os.environ.get("PM_SMTP_HOST", "")
    user = os.environ.get("PM_SMTP_USER", "")
    password = os.environ.get("PM_SMTP_PASS", "")
    to = os.environ.get("PM_MAIL_TO", "")
    if not all([host, user, password, to]):
        print("[邮件] 未配置 SMTP 环境变量，跳过（配置说明见 README）")
        return 0

    date_str = sys.argv[1] if len(sys.argv) > 1 else \
        datetime.datetime.now(CST).strftime("%Y-%m-%d")
    path = os.path.join(DIGEST_DIR, f"{date_str}.md")
    if not os.path.exists(path):
        print(f"[邮件] 日报 {path} 不存在，跳过")
        return 0
    papers = parse_digest_file(path)
    if not papers:
        print("[邮件] 日报无条目，跳过")
        return 0

    port = int(os.environ.get("PM_SMTP_PORT", "465"))
    msg = MIMEText(build_html(papers, date_str), "html", "utf-8")
    msg["Subject"] = Header(f"PaperMind 论文日报 · {date_str}（{len(papers)} 篇）", "utf-8")
    msg["From"] = user
    msg["To"] = to
    try:
        with smtplib.SMTP_SSL(host, port, timeout=30) as s:
            s.login(user, password)
            s.sendmail(user, [t.strip() for t in to.split(",")], msg.as_string())
        print(f"[邮件] 已发送: {date_str} -> {to}")
    except Exception as e:  # 邮件失败不阻断日报流程
        print(f"[邮件] 发送失败: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
