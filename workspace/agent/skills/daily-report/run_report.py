#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
日报/周报/月报生成入口脚本（独立版）

使用方式：
    python run_report.py daily [date]           # 生成日报
    python run_report.py weekly [end_date]      # 生成周报
    python run_report.py monthly [year] [month] # 生成月报
"""

import argparse
import io
import os
import re
import sys
import subprocess
from datetime import datetime
from pathlib import Path

# 修复 Windows 编码问题 - 必须在所有输出之前
os.environ["PYTHONIOENCODING"] = "utf-8"
if sys.platform == "win32":
    # 强制设置 stdout/stderr 为 UTF-8
    if hasattr(sys.stdout, 'buffer'):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, 'buffer'):
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

try:
    import imaplib
    import email
    from email.header import decode_header
    IMAP_AVAILABLE = True
    # 注册ID命令（163邮箱需要）
    imaplib.Commands['ID'] = ('NONAUTH', 'AUTH', 'SELECTED')
except ImportError:
    IMAP_AVAILABLE = False
    imaplib = None

# 获取脚本所在目录
SKILL_DIR = Path(__file__).parent
PROJECT_ROOT = SKILL_DIR.parent.parent.parent.parent


def collect_git_stats(date: str = None) -> dict:
    """采集 Git 提交统计"""
    if date is None:
        date = datetime.now().strftime("%Y-%m-%d")

    try:
        result = subprocess.run(
            ["git", "-C", str(PROJECT_ROOT), "log",
             f"--since={date} 00:00:00",
             f"--until={date} 23:59:59",
             "--format=%H|%s|%an|%ai",
             "--numstat"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30
        )

        commits = []
        total_insertions = 0
        total_deletions = 0

        if result.stdout:
            current_commit = None
            for line in result.stdout.strip().split("\n"):
                if "|" in line and len(line.split("|")) >= 4:
                    parts = line.split("|")
                    if current_commit:
                        commits.append(current_commit)
                    current_commit = {
                        "hash": parts[0][:8],
                        "message": parts[1],
                        "author": parts[2],
                        "insertions": 0,
                        "deletions": 0
                    }
                elif current_commit and "\t" in line:
                    stat_parts = line.split("\t")
                    if len(stat_parts) >= 2:
                        try:
                            ins = int(stat_parts[0]) if stat_parts[0] != "-" else 0
                            dels = int(stat_parts[1]) if stat_parts[1] != "-" else 0
                            current_commit["insertions"] += ins
                            current_commit["deletions"] += dels
                            total_insertions += ins
                            total_deletions += dels
                        except ValueError:
                            pass

            if current_commit:
                commits.append(current_commit)

        return {
            "total_commits": len(commits),
            "total_insertions": total_insertions,
            "total_deletions": total_deletions,
            "commits": commits
        }
    except Exception as e:
        return {"error": str(e)}


def collect_email_stats(date: str = None) -> dict:
    """采集邮箱统计"""
    if not IMAP_AVAILABLE:
        return {"error": "IMAP module not available"}

    # 直接从 .env 文件读取配置
    env_file = PROJECT_ROOT / ".env"
    email_address = ""
    email_token = ""
    email_provider = "163"

    if env_file.exists():
        with open(env_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    key, value = line.split("=", 1)
                    if key == "EMAIL_ADDRESS":
                        email_address = value.strip('"')
                    elif key == "EMAIL_TOKEN":
                        email_token = value.strip('"')
                    elif key == "EMAIL_PROVIDER":
                        email_provider = value.strip('"')

    # 也尝试从环境变量获取（作为备用）
    if not email_address:
        email_address = os.environ.get("EMAIL_ADDRESS", "")
    if not email_token:
        email_token = os.environ.get("EMAIL_TOKEN", "")
    if not email_provider:
        email_provider = os.environ.get("EMAIL_PROVIDER", "163")

    if not email_address or not email_token:
        return {"error": "Email credentials not configured"}

    # 网易邮箱 IMAP 服务器
    IMAP_SERVERS = {
        "163": "imap.163.com",
        "126": "imap.126.com",
        "yeah": "imap.yeah.net",
    }

    server = IMAP_SERVERS.get(email_provider, "imap.163.com")

    try:
        mail = imaplib.IMAP4_SSL(server, 993)
        mail.login(email_address, email_token)

        # 163邮箱需要在登录后发送ID信息
        try:
            args = '("name" "python-imap" "version" "1.0" "vendor" "python")'
            mail._simple_command("ID", args)
        except:
            pass

        # 使用 STATUS 命令获取邮件统计（绕过 SELECT 的 Unsafe Login 限制）
        total_emails = 0
        unread = 0

        try:
            status, data = mail.status("INBOX", "(MESSAGES UNSEEN)")
            if status == "OK" and data:
                # 解析 STATUS 响应: b'"INBOX" (MESSAGES 39 UNSEEN 32)'
                import re
                response = data[0].decode() if isinstance(data[0], bytes) else str(data[0])
                messages_match = re.search(r'MESSAGES\s+(\d+)', response)
                unseen_match = re.search(r'UNSEEN\s+(\d+)', response)
                if messages_match:
                    total_emails = int(messages_match.group(1))
                if unseen_match:
                    unread = int(unseen_match.group(1))
        except Exception as e:
            pass

        mail.logout()

        return {
            "received_today": total_emails,
            "unread": unread,
            "date": date if date else datetime.now().strftime("%Y-%m-%d")
        }
    except Exception as e:
        # 返回默认值而不是错误
        return {
            "received_today": 0,
            "unread": 0,
            "date": date if date else datetime.now().strftime("%Y-%m-%d"),
            "error": str(e)[:50]  # 截断错误信息
        }


def collect_email_content(limit: int = 20, days: int = 30) -> list:
    """读取邮箱中的邮件内容

    Args:
        limit: 最多读取邮件数量
        days: 只读取最近N天内的邮件

    Returns:
        邮件列表，每个元素包含 subject, from, date, body_preview
    """
    if not IMAP_AVAILABLE:
        return []

    # 直接从 .env 文件读取配置
    env_file = PROJECT_ROOT / ".env"
    email_address = ""
    email_token = ""
    email_provider = "163"

    if env_file.exists():
        with open(env_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    key, value = line.split("=", 1)
                    if key == "EMAIL_ADDRESS":
                        email_address = value.strip('"')
                    elif key == "EMAIL_TOKEN":
                        email_token = value.strip('"')
                    elif key == "EMAIL_PROVIDER":
                        email_provider = value.strip('"')

    if not email_address or not email_token:
        return []

    IMAP_SERVERS = {
        "163": "imap.163.com",
        "126": "imap.126.com",
        "yeah": "imap.yeah.net",
    }

    server = IMAP_SERVERS.get(email_provider, "imap.163.com")
    emails = []

    try:
        mail = imaplib.IMAP4_SSL(server, 993)
        mail.login(email_address, email_token)

        # 发送ID命令（163邮箱必须）
        args = '("name" "python" "version" "1.0" "vendor" "python-imap")'
        mail._simple_command("ID", args)

        # 选择收件箱
        typ, dat = mail.select("INBOX")
        if typ != "OK":
            mail.logout()
            return []

        # 搜索最近N天的邮件
        from datetime import timedelta
        since_date = (datetime.now() - timedelta(days=days)).strftime("%d-%b-%Y")
        typ, msg_ids = mail.search(None, f'(SINCE {since_date})')

        if typ != "OK" or not msg_ids[0]:
            mail.logout()
            return []

        ids = msg_ids[0].split()[-limit:]  # 获取最新的N封

        for msg_id in reversed(ids):  # 从最新开始
            try:
                typ, msg_data = mail.fetch(msg_id, "(RFC822)")
                if typ != "OK":
                    continue

                raw_email = msg_data[0][1]
                msg = email.message_from_bytes(raw_email)

                # 解码主题
                subject = msg["Subject"] or "(无主题)"
                if subject:
                    decoded = decode_header(subject)
                    subject = ""
                    for part, encoding in decoded:
                        if isinstance(part, bytes):
                            subject += part.decode(encoding or "utf-8", errors="ignore")
                        else:
                            subject += part

                # 解码发件人
                from_addr = msg.get("From", "")
                if from_addr:
                    decoded = decode_header(from_addr)
                    from_addr = ""
                    for part, encoding in decoded:
                        if isinstance(part, bytes):
                            from_addr += part.decode(encoding or "utf-8", errors="ignore")
                        else:
                            from_addr += part

                # 日期
                date_str = msg.get("Date", "")

                # 提取正文
                body = ""
                if msg.is_multipart():
                    for part in msg.walk():
                        content_type = part.get_content_type()
                        if content_type == "text/plain":
                            payload = part.get_payload(decode=True)
                            charset = part.get_content_charset() or "utf-8"
                            body = payload.decode(charset, errors="ignore")
                            break
                        elif content_type == "text/html" and not body:
                            payload = part.get_payload(decode=True)
                            charset = part.get_content_charset() or "utf-8"
                            html_body = payload.decode(charset, errors="ignore")
                            # 简单清理HTML标签
                            import re
                            body = re.sub(r'<[^>]+>', ' ', html_body)
                            body = re.sub(r'\s+', ' ', body).strip()
                else:
                    payload = msg.get_payload(decode=True)
                    charset = msg.get_content_charset() or "utf-8"
                    body = payload.decode(charset, errors="ignore") if payload else ""

                emails.append({
                    "subject": subject[:100],
                    "from": from_addr[:80],
                    "date": date_str,
                    "body_preview": body[:500] if body else ""
                })

            except Exception:
                continue

        mail.logout()

    except Exception:
        pass

    return emails
    """生成日报"""
    if date is None:
        date = datetime.now().strftime("%Y-%m-%d")

    # 采集 Git 数据
    git_stats = collect_git_stats(date)

    # 采集邮箱数据
    email_stats = collect_email_stats(date)

    # 读取记忆文件
    memory_file = PROJECT_ROOT / "workspace" / "agent" / "memory" / f"{date}.md"
    memory_content = ""
    work_items = []

    if memory_file.exists():
        memory_content = memory_file.read_text(encoding="utf-8")
        for line in memory_content.split("\n"):
            stripped = line.strip()
            if stripped.startswith("-") or stripped.startswith("*"):
                item = stripped.lstrip("-* ").strip()
                if item and not item.startswith("<!--"):
                    work_items.append(item)

    # 查找 todo 文件
    todo_file = None
    session_dir = PROJECT_ROOT / "workspace" / "session"
    if session_dir.exists():
        todo_files = list(session_dir.rglob("todo.md"))
        if todo_files:
            todo_files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
            todo_file = todo_files[0]

    # 解析 todo
    completed_tasks = []
    pending_tasks = []

    if todo_file and todo_file.exists():
        todo_content = todo_file.read_text(encoding="utf-8")
        for line in todo_content.split("\n"):
            stripped = line.strip()
            # Checkbox 格式
            match = re.match(r"-\s*\[([xX ])\]\s*(.+)", stripped)
            if match:
                checked = match.group(1).lower() == "x"
                task = match.group(2).strip()
                if checked:
                    completed_tasks.append(task)
                else:
                    pending_tasks.append(task)

    # 生成报告
    lines = [
        f"# 📋 工作日报 - {date}",
        "",
        "## 📊 今日概览",
        "",
        "| 指标 | 数值 |",
        "|------|------|",
        f"| 代码提交 | {git_stats.get('total_commits', 0)} 次 |",
        f"| 代码变更 | +{git_stats.get('total_insertions', 0)}/-{git_stats.get('total_deletions', 0)} |",
        f"| 已完成任务 | {len(completed_tasks)} 项 |",
        f"| 进行中 | {len(pending_tasks)} 项 |",
    ]

    # 添加邮箱统计（如果采集成功）
    if "error" not in email_stats:
        lines.extend([
            f"| 邮件收件 | {email_stats.get('received_today', 0)} 封 |",
            f"| 未读邮件 | {email_stats.get('unread', 0)} 封 |",
        ])

    lines.append("")

    # 已完成任务
    if completed_tasks:
        lines.extend(["## ✅ 已完成任务", ""])
        for task in completed_tasks[:10]:
            lines.append(f"- {task}")
        lines.append("")

    # 代码提交
    if git_stats.get("commits"):
        lines.extend([
            "## 💻 代码提交",
            "",
            "| 时间 | 提交信息 | 变更 |",
            "|------|----------|------|",
        ])
        for commit in git_stats["commits"][:10]:
            lines.append(
                f"| {commit.get('hash', '-')} | {commit.get('message', '-')[:40]} | "
                f"+{commit.get('insertions', 0)}/-{commit.get('deletions', 0)} |"
            )
        lines.append("")

    # 工作记录
    if work_items:
        lines.extend(["## 📝 今日工作记录", ""])
        for item in work_items[:10]:
            lines.append(f"- {item}")
        lines.append("")

    # 明日计划
    lines.extend(["## 🔜 明日计划", ""])
    if pending_tasks:
        for task in pending_tasks[:5]:
            lines.append(f"- {task}")
    else:
        lines.append("- 待补充")
    lines.append("")

    return "\n".join(lines)


def generate_monthly_report(year: int = None, month: int = None) -> str:
    """生成月报"""
    now = datetime.now()
    if year is None:
        year = now.year
    if month is None:
        month = now.month

    import calendar
    _, days_in_month = calendar.monthrange(year, month)

    # 采集整月数据
    total_commits = 0
    total_insertions = 0
    total_deletions = 0
    active_days = 0
    total_emails_received = 0
    total_unread = 0
    email_collection_days = 0
    email_errors = []

    for day in range(1, days_in_month + 1):
        date = f"{year:04d}-{month:02d}-{day:02d}"

        # 采集 Git 数据
        stats = collect_git_stats(date)
        commits = stats.get("total_commits", 0)
        total_commits += commits
        total_insertions += stats.get("total_insertions", 0)
        total_deletions += stats.get("total_deletions", 0)
        if commits > 0:
            active_days += 1

        # 采集邮箱数据
        email_stats = collect_email_stats(date)
        if "error" not in email_stats:
            total_emails_received += email_stats.get("received_today", 0)
            email_collection_days += 1
        else:
            # 只记录一次错误，避免重复
            if len(email_errors) == 0:
                email_errors.append(email_stats.get("error", "Unknown error"))

    # 获取当前未读邮件数
    current_email_stats = collect_email_stats()
    current_unread = current_email_stats.get("unread", 0) if "error" not in current_email_stats else 0

    # 生成报告
    lines = [
        f"# 📋 工作月报 - {year}年{month}月",
        "",
        "## 📊 本月概览",
        "",
        "| 指标 | 数值 |",
        "|------|------|",
        f"| 活跃天数 | {active_days}/{days_in_month} 天 |",
        f"| 代码提交 | {total_commits} 次 |",
        f"| 代码变更 | +{total_insertions}/-{total_deletions} |",
    ]

    # 添加邮箱统计
    if email_collection_days > 0:
        lines.extend([
            f"| 邮件收件 | {total_emails_received} 封 |",
            f"| 当前未读 | {current_unread} 封 |",
        ])
    elif email_errors:
        lines.append(f"| 邮箱状态 | 采集失败: {email_errors[0][:30]}... |")

    lines.append("")

    # 工作总结
    lines.extend([
        "## 📝 工作总结",
        "",
        f"本月共完成 {total_commits} 次代码提交，",
        f"净增代码 {total_insertions - total_deletions} 行。",
    ])

    if email_collection_days > 0:
        lines.extend([
            "",
            f"邮箱方面，本月共收到 {total_emails_received} 封邮件，",
            f"当前有 {current_unread} 封未读邮件。",
        ])

    # 添加近期邮件摘要
    lines.extend([
        "",
        "## 📧 近期邮件摘要",
        "",
    ])

    # 读取最近30天的邮件
    recent_emails = collect_email_content(limit=15, days=30)
    if recent_emails:
        for em in recent_emails:
            lines.append(f"### {em['subject'][:50]}")
            lines.append(f"**发件人**: {em['from']}")
            lines.append(f"**时间**: {em['date']}")
            if em['body_preview']:
                lines.append(f"**内容预览**: {em['body_preview'][:200]}...")
            lines.append("")
    else:
        lines.append("暂无邮件数据")
        lines.append("")

    lines.extend([
        "",
        "## 🔜 下月计划",
        "",
        "- 继续完善项目功能",
        "",
    ])

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="日报/周报/月报生成器")
    parser.add_argument(
        "type",
        choices=["daily", "weekly", "monthly"],
        help="报告类型: daily(日报), weekly(周报), monthly(月报)"
    )
    parser.add_argument("--date", "-d", help="日期 (YYYY-MM-DD)")
    parser.add_argument("--year", "-y", type=int, help="年份")
    parser.add_argument("--month", "-m", type=int, help="月份")
    parser.add_argument("--save", "-s", action="store_true", default=True, help="保存到文件(默认开启)")
    parser.add_argument("--no-save", action="store_true", help="不保存文件，直接输出")
    parser.add_argument("--output-file", "-o", help="输出文件路径")

    args = parser.parse_args()

    try:
        if args.type == "daily":
            date = args.date or datetime.now().strftime("%Y-%m-%d")
            content = generate_daily_report(date)
            date_str = date

        elif args.type == "weekly":
            date = args.date or datetime.now().strftime("%Y-%m-%d")
            # 周报暂时用日报代替
            content = generate_daily_report(date)
            date_str = date

        elif args.type == "monthly":
            now = datetime.now()
            year = args.year or now.year
            month = args.month or now.month
            content = generate_monthly_report(year, month)
            date_str = f"{year:04d}-{month:02d}"

        # 保存文件（默认行为）
        if not args.no_save:
            if args.output_file:
                filepath = Path(args.output_file)
            else:
                reports_dir = PROJECT_ROOT / "workspace" / "agent" / "reports"
                reports_dir.mkdir(parents=True, exist_ok=True)
                filepath = reports_dir / f"{args.type}-{date_str}.md"
            filepath.write_text(content, encoding="utf-8")
            # 只输出文件路径，方便 Agent 读取
            print(f"REPORT_FILE:{filepath}")
        else:
            # 直接输出内容
            print(content)

    except Exception as e:
        print(f"ERROR:{e}", file=sys.stderr)
        import traceback
        traceback.print_exc(file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
