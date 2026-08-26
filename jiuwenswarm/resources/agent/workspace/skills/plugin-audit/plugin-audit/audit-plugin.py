#!/usr/bin/env python3
"""
Plugin Code Auditor — 插件代码安全审计

Usage:
    ./audit-plugin.py <plugin-source-dir> [--name <plugin-name>] [--source <source-type>]
    ./audit-plugin.py --repo <git-url>
    ./audit-plugin.py --npm <package-name>

审计插件源码目录，基于规则 JSON 进行静态扫描，
输出结构化审计报告供 agent 展示给用户。
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# 规则目录
RULES_DIR = Path(__file__).parent / "patterns"

# 已知安全的开源项目（白名单）——仅供辅助参考
SAFE_PREFIXES = [
    "openclaw-",
    "@openclaw/",
    "clawhUb-",
    "claude-",
]


def load_patterns():
    """加载所有规则"""
    rules = {"critical": [], "suspicious": []}
    high_risk_file = RULES_DIR / "high-risk.json"
    medium_risk_file = RULES_DIR / "medium-risk.json"

    if high_risk_file.exists():
        with open(high_risk_file) as f:
            data = json.load(f)
            if data and "patterns" in data:
                rules["critical"] = data["patterns"]

    if medium_risk_file.exists():
        with open(medium_risk_file) as f:
            data = json.load(f)
            if isinstance(data, list):
                rules["suspicious"] = data

    return rules


def get_all_source_files(source_dir):
    """递归获取所有源码文件（排除 node_modules 和 .git）"""
    source_files = []
    excluded_dirs = {"node_modules", ".git", ".hg", ".svn", "dist", "build", ".next"}
    excluded_exts = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".woff", ".woff2",
                     ".ttf", ".eot", ".otf", ".mp3", ".mp4", ".mov", ".zip", ".gz", ".tar",
                     ".lock", ".sum"}

    for root, dirs, files in os.walk(source_dir):
        # 排除目录
        dirs[:] = [d for d in dirs if d not in excluded_dirs and not d.startswith(".")]
        for f in files:
            ext = os.path.splitext(f)[1].lower()
            if ext in excluded_exts:
                continue
            if f.startswith("."):
                continue
            source_files.append(os.path.join(root, f))

    return source_files


def scan_file(file_path, patterns):
    """扫描单个文件，返回所有命中"""
    findings = []
    try:
        with open(file_path, "r", errors="replace") as f:
            content = f.read()
            lines = content.split("\n")
    except Exception:
        return []

    rel_path = os.path.relpath(file_path)

    for severity, pattern_list in patterns.items():
        for rule in pattern_list:
            rule_patterns = rule.get("rules", [])
            require_both = rule.get("require_both", False)
            require_all_envs = rule.get("require_all_envs", False)

            for line_no, line in enumerate(lines, 1):
                stripped = line.strip()
                if not stripped or stripped.startswith("//") or stripped.startswith("#") or stripped.startswith("/*"):
                    continue

                # 匹配所有规则
                matched_rules = []
                for rp in rule_patterns:
                    if rp and re.search(rp, stripped, re.IGNORECASE):
                        matched_rules.append(rp)

                if len(matched_rules) == 0:
                    continue

                if require_both:
                    # 需要同时匹配所有规则
                    if len(matched_rules) < len(rule_patterns):
                        continue

                findings.append({
                    "rule_id": rule.get("id", "UNKNOWN"),
                    "rule_name": rule.get("name", "未知规则"),
                    "severity": severity,
                    "file": rel_path,
                    "line": line_no,
                    "code": stripped[:200],
                    "description": rule.get("description", ""),
                })

    return findings


def scan_package_json(source_dir):
    """检查 package.json 中的依赖"""
    findings = []
    pkg_path = os.path.join(source_dir, "package.json")
    if not os.path.exists(pkg_path):
        return findings

    try:
        with open(pkg_path) as f:
            pkg = json.load(f)
    except Exception:
        return findings

    # 统计依赖
    all_deps = {}
    for key in ("dependencies", "devDependencies", "peerDependencies"):
        deps = pkg.get(key, {})
        for name, ver in deps.items():
            all_deps[name] = ver

    # 检查是否有 postinstall 脚本（可能用于恶意行为）
    scripts = pkg.get("scripts", {})
    for script_name, script_content in scripts.items():
        suspicious_scripts = ("postinstall", "preinstall", "postupdate", "preuninstall")
        if script_name.lower() in suspicious_scripts:
            # 检测脚本中是否有可疑操作
            suspicious_ops = ["curl", "wget", "fetch", "download", "exec", "eval",
                              "bash", "sh ", "./", "&&", "|", ";"]
            score = sum(1 for op in suspicious_ops if op in script_content.lower())
            if score >= 2:
                findings.append({
                    "rule_id": "PKG-001",
                    "rule_name": "安装脚本含可疑命令",
                    "severity": "suspicious",
                    "file": "package.json",
                    "line": 1,
                    "code": f"{script_name}: {script_content[:200]}",
                    "description": f"安装脚本 '{script_name}' 包含多条可疑命令",
                })

    return findings


def scan_entry_points(source_dir):
    """扫描插件入口文件，检查是否存在入口文件混淆"""
    findings = []
    pkg_path = os.path.join(source_dir, "package.json")
    if not os.path.exists(pkg_path):
        return findings

    try:
        with open(pkg_path) as f:
            pkg = json.load(f)
    except Exception:
        return findings

    main = pkg.get("main", "")
    if not main:
        main = "index.js"

    main_path = os.path.join(source_dir, main)
    if os.path.exists(main_path):
        with open(main_path, "r", errors="replace") as f:
            content = f.read()
        # 检查入口文件是否极小但引用了其他混淆文件
        if len(content) < 100 and "require" in content:
            findings.append({
                "rule_id": "PKG-002",
                "name": "入口文件过小",
                "severity": "suspicious",
                "file": main,
                "line": 1,
                "code": content[:200],
                "description": "入口文件过于短小，可能是跳板文件",
            })

    return findings


def resolve_plugin_source(source_type, source_value):
    """将插件来源解析为本地目录"""
    tmp_dir = tempfile.mkdtemp(prefix="plugin-audit-")

    if source_type == "dir":
        return source_value  # 直接使用

    elif source_type == "npm":
        result = subprocess.run(
            ["npm", "pack", "--pack-destination", tmp_dir, source_value],
            capture_output=True, text=True, timeout=60
        )
        if result.returncode != 0:
            raise RuntimeError(f"npm pack 失败: {result.stderr}")
        tarball = os.path.join(tmp_dir, result.stdout.strip())
        extract_dir = os.path.join(tmp_dir, "package")
        os.makedirs(extract_dir, exist_ok=True)
        shutil.unpack_archive(tarball, extract_dir, "gztar")
        return extract_dir

    elif source_type == "git":
        repo_dir = os.path.join(tmp_dir, "repo")
        result = subprocess.run(
            ["git", "clone", "--depth=1", source_value, repo_dir],
            capture_output=True, text=True, timeout=60
        )
        if result.returncode != 0:
            raise RuntimeError(f"git clone 失败: {result.stderr}")
        return repo_dir

    elif source_type == "archive":
        extract_dir = os.path.join(tmp_dir, "extracted")
        os.makedirs(extract_dir, exist_ok=True)
        shutil.unpack_archive(source_value, extract_dir)
        # 如果解压后只有一个目录，使用该目录
        contents = os.listdir(extract_dir)
        if len(contents) == 1 and os.path.isdir(os.path.join(extract_dir, contents[0])):
            return os.path.join(extract_dir, contents[0])
        return extract_dir

    else:
        raise ValueError(f"不支持的来源类型: {source_type}")


def main():
    parser = argparse.ArgumentParser(description="插件代码安全审计")
    parser.add_argument("source", nargs="?", help="插件源码目录")
    parser.add_argument("--name", help="插件名称")
    parser.add_argument("--dir", help="插件目录")
    parser.add_argument("--npm", help="npm 包名")
    parser.add_argument("--git", help="Git 仓库 URL")
    parser.add_argument("--archive", help="压缩包路径")
    parser.add_argument("--json", action="store_true", help="以 JSON 格式输出")
    args = parser.parse_args()

    # 确定扫描目录
    scan_dir = None
    source_type = "dir"
    source_value = args.name or "unknown"
    cleanup = False

    try:
        if args.dir:
            scan_dir = args.dir
            source_value = args.name or os.path.basename(args.dir)
        elif args.npm:
            scan_dir = resolve_plugin_source("npm", args.npm)
            source_value = args.npm
            cleanup = True
        elif args.git:
            scan_dir = resolve_plugin_source("git", args.git)
            source_value = args.name or args.git
            cleanup = True
        elif args.archive:
            scan_dir = resolve_plugin_source("archive", args.archive)
            source_value = args.name or os.path.basename(args.archive)
            cleanup = True
        elif args.source:
            scan_dir = args.source
            source_value = args.name or os.path.basename(args.source)
        else:
            parser.print_help()
            sys.exit(1)

        if not os.path.isdir(scan_dir):
            sys.exit(f"❌ 目录不存在: {scan_dir}")

        # 加载规则
        patterns = load_patterns()

        # 获取源码文件
        source_files = get_all_source_files(scan_dir)
        total_files = len(source_files)

        # 执行扫描
        all_findings = []
        for sf in source_files:
            findings = scan_file(sf, patterns)
            all_findings.extend(findings)

        # 扫描 package.json
        pkg_findings = scan_package_json(scan_dir)
        all_findings.extend(pkg_findings)

        # 扫描入口文件
        entry_findings = scan_entry_points(scan_dir)
        all_findings.extend(entry_findings)

        # 去重（同一文件同一行同一规则只保留一条）
        seen = set()
        unique_findings = []
        for f in all_findings:
            key = (f["file"], f["line"], f["rule_id"])
            if key not in seen:
                seen.add(key)
                unique_findings.append(f)

        # 评级
        critical_count = len([x for x in unique_findings if x["severity"] == "critical"])
        suspicious_count = len([x for x in unique_findings if x["severity"] == "suspicious"])

        if critical_count > 0:
            risk_level = "critical"
            risk_label = "🔴 高危"
        elif suspicious_count > 0:
            risk_level = "suspicious"
            risk_label = "🟡 可疑"
        else:
            risk_level = "safe"
            risk_label = "🟢 安全"

        # 构建报告
        report = {
            "plugin_name": source_value,
            "source_dir": scan_dir,
            "files_scanned": total_files,
            "risk_level": risk_level,
            "risk_label": risk_label,
            "critical_count": critical_count,
            "suspicious_count": suspicious_count,
            "findings": [
                {
                    "rule_id": f["rule_id"],
                    "severity": f["severity"],
                    "file": f["file"],
                    "line": f["line"],
                    "code": f.get("code", ""),
                    "description": f.get("description", ""),
                }
                for f in unique_findings
            ],
        }

        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            # 友好输出
            border = "═" * 55
            print(f"\n  {border}")
            print(f"    🔍 插件安全审计报告")
            print(f"  {border}")
            print(f"  插件名称: {source_value}")
            print(f"  扫描文件: {total_files} 个")
            print(f"  风险评级: {risk_label}")
            print(f"  {border}")

            if unique_findings:
                # 按严重级别分组
                criticals = [f for f in unique_findings if f["severity"] == "critical"]
                suspicions = [f for f in unique_findings if f["severity"] == "suspicious"]
                all_grouped = criticals + suspicions

                print(f"  发现 {len(unique_findings)} 项问题:\n")
                for f in all_grouped:
                    sev_icon = "🔴" if f["severity"] == "critical" else "⚠️"
                    print(f"  {sev_icon} [{f['rule_id']}] {f.get('rule_name', f['description'])}")
                    print(f"     文件: {f['file']}:{f['line']}")
                    print(f"    代码: {f.get('code', '')[:120]}")
                    print()
            else:
                print("  ✅ 未发现可疑模式\n")

            print(f"  {border}")
            if risk_level == "critical":
                print("  ❌ 检测到高危风险，强烈建议不要安装此插件！")
            elif risk_level == "suspicious":
                print("  ❓ 发现可疑项，请确认是否继续安装。")
            else:
                print("  ✅ 审计通过，可正常安装。")
            print(f"  {border}\n")

    finally:
        # 清理临时目录
        if cleanup and scan_dir and os.path.exists(scan_dir):
            shutil.rmtree(os.path.dirname(scan_dir), ignore_errors=True)


if __name__ == "__main__":
    main()
