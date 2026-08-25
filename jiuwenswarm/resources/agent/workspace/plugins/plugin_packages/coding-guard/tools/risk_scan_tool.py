# -*- coding: utf-8 -*-
"""Coding Guard — shared policy engine and the risk_scan Tool adapter.

Tool 与 Rail 共用本模块的扫描与聚合函数，保证主动评估与运行时拦截决策一致。
"""

from __future__ import annotations

import ast
import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from typing import Iterable

from openjiuwen.core.foundation.tool import Tool
from openjiuwen.core.foundation.tool import ToolCard

SUPPORTED_LANGUAGES = ("shell", "python", "javascript", "powershell", "generic")
DECISION_ORDER = ("allow", "warn", "confirm", "block")
_SEVERITY_ORDER = ("none", "low", "medium", "high", "critical")
RAIL_PRIORITY = 95
_MAX_FILE_SCAN_BYTES = 512 * 1024

_CREDENTIAL_FILENAME_RE = re.compile(
    r"(^|/|\\)(id_rsa|id_ecdsa|id_ed25519|id_dsa|credentials\.json|"
    r"\.env$|\.pem$|\.key$|\.p12$|\.pfx$|\.kube/config$)",
    re.IGNORECASE,
)
_SECRET_VAR_RE = re.compile(
    r"^(?:api[_-]?key|access[_-]?key|auth[_-]?token|token|password|passwd|secret|"
    r"client[_-]?secret|private[_-]?key)$",
    re.IGNORECASE,
)
_COMMAND_WRAPPERS = {"sudo", "doas", "env", "command", "nohup", "time"}
_HOME_TOKENS = frozenset({"~", "~/", "~/*", "$home", "$home/*", "${home}", "${home}/*"})
_UNIX_ROOT_TOKENS = frozenset(
    {
        "/",
        "/*",
        "/.",
        "/etc",
        "/etc/*",
        "/boot",
        "/boot/*",
        "/usr",
        "/usr/*",
        "/var",
        "/var/*",
        "/srv",
        "/lib",
        "/lib64",
        "/bin",
        "/sbin",
        "/opt",
        "/proc",
        "/sys",
        "/dev",
        "/home",
        "/home/*",
        "/root",
        "/root/*",
    }
)
_WIN_ROOT_TOKENS = frozenset(
    {
        "c:\\*",
        "c:\\windows",
        "c:\\windows\\*",
        "c:\\windows\\system32",
        "c:\\windows\\system32\\*",
        "c:\\program files",
        "c:\\program files\\*",
        "c:\\program files (x86)",
        "c:\\program files (x86)\\*",
        "c:\\programdata",
        "c:\\programdata\\*",
        "c:\\users\\*",
        "c:\\$recycle.bin",
        "d:\\*",
    }
)
_SYSTEM_DIR_PREFIXES = (
    "/etc",
    "/boot",
    "/usr",
    "/bin",
    "/sbin",
    "/dev",
    "/proc",
    "/sys",
    "/var",
    "/lib",
    "c:/windows",
    "c:/program files",
    "c:/programdata",
    "c:/system volume information",
)


@dataclass(frozen=True)
class Rule:
    """一条内置风险规则。"""

    id: str
    category: str
    languages: frozenset[str]
    severity: str
    action: str
    message: str
    recommendation: str
    pattern: re.Pattern[str] | None = None
    outside_workspace: bool | None = None
    loop_escalate: int = 0
    repeat_escalate: int = 0


@dataclass
class Finding:
    """一条风险命中，Tool 与 Rail 共用。"""

    rule_id: str
    category: str
    severity: str
    action: str
    message: str
    snippet: str
    recommendation: str = ""
    line: int | None = None
    location: str = ""
    in_loop: bool = False
    overridden: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "category": self.category,
            "severity": self.severity,
            "action": self.action,
            "message": self.message,
            "snippet": self.snippet,
            "recommendation": self.recommendation,
            "line": self.line,
            "location": self.location,
            "in_loop": self.in_loop,
            "overridden": self.overridden,
        }


@dataclass
class ScanResult:
    """一次扫描的固定契约。"""

    risk_level: str
    decision: str
    executable: bool
    findings: list[Finding]
    recommendation: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "risk_level": self.risk_level,
            "decision": self.decision,
            "executable": self.executable,
            "finding_count": len(self.findings),
            "findings": [item.to_dict() for item in self.findings],
            "recommendation": self.recommendation,
        }


def decision_rank(action: str) -> int:
    try:
        return DECISION_ORDER.index(action)
    except ValueError:
        return DECISION_ORDER.index("warn")


def severity_rank(severity: str) -> int:
    try:
        return _SEVERITY_ORDER.index(severity)
    except ValueError:
        return _SEVERITY_ORDER.index("none")


def _escalate(severity: str, action: str) -> tuple[str, str]:
    next_sev = _SEVERITY_ORDER[
        min(severity_rank(severity) + 1, len(_SEVERITY_ORDER) - 1)
    ]
    next_act = action
    if action != "block":
        next_act = DECISION_ORDER[
            min(decision_rank(action) + 1, len(DECISION_ORDER) - 1)
        ]
    return next_sev, next_act


def _norm_languages(raw: Any) -> frozenset[str]:
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, (list, tuple, set)):
        return frozenset({"all"})
    lowered = {str(item).strip().lower() for item in raw if str(item).strip()}
    if not lowered or "all" in lowered or "generic" in lowered:
        return frozenset({"all"})
    return frozenset(lowered)


@dataclass(frozen=True)
class _RuleRow:
    """Built-in rule table row (G.FNM.03)."""

    rule_id: str
    category: str
    langs: str
    severity: str
    action: str
    message: str
    recommendation: str = ""
    pattern: str | None = None
    outside: bool = False
    loop: int = 0
    repeat: int = 0

    def as_rule(self) -> Rule:
        return Rule(
            id=self.rule_id,
            category=self.category,
            languages=_norm_languages(self.langs.split(",")),
            severity=self.severity,
            action=self.action,
            message=self.message,
            recommendation=self.recommendation,
            pattern=re.compile(self.pattern) if self.pattern else None,
            outside_workspace=True if self.outside else None,
            loop_escalate=self.loop,
            repeat_escalate=self.repeat,
        )


# fmt: off
_BUILTIN_RULE_ROWS: tuple[_RuleRow, ...] = (
    _RuleRow("rm-rf-filesystem-root", "command", "shell,powershell", "critical", "block",
       "递归强制删除系统根/家目录/盘符根，文件不可恢复",
       "核对删除目标，工作区内的清理请改用精确的相对路径", outside=True, repeat=3),
    _RuleRow("win-bulk-delete", "command", "shell,powershell", "critical", "block",
       "Windows 批量删除危险目标（盘根/系统目录）",
       "确认目标盘符与目录后再执行，建议先列出文件再删除", outside=True),
    _RuleRow("rmdir-root", "command", "shell", "critical", "block",
       "递归删除危险目录（根/家目录）",
       "核对目标目录，避免删除不可恢复的系统或用户数据", outside=True),
    _RuleRow("pipe-download-exec", "command", "shell,powershell", "high", "block",
       "将下载内容通过管道直接交给解释器执行（curl|sh 等）",
       "先下载到本地检查内容，确认来源可信后再执行"),
    _RuleRow("fork-bomb", "command", "shell", "critical", "block",
       "检测到 fork 炸弹脚本，可能耗尽系统资源",
       "不要执行自复制脚本；若确有必要请先确认资源限制"),
    _RuleRow("format-filesystem", "command", "shell", "high", "warn",
       "格式化文件系统：mkfs 系列命令",
       "格式化将销毁分区数据，确认目标设备与备份后再操作"),
    _RuleRow("format-drive", "command", "shell,powershell", "high", "warn",
       "Windows format 格式化磁盘分区",
       "格式化将销毁分区数据，确认目标分区与备份后再操作"),
    _RuleRow("dd-write-device", "command", "shell", "high", "warn",
       "dd 直接向块设备/系统分区写入数据",
       "确认 of/if 目标正确，避免覆写系统盘"),
    _RuleRow("recursive-permission-change", "command", "shell", "high", "warn",
       "递归修改系统范围权限/属主",
       "缩小到具体的项目目录范围，避免破坏系统安全属性"),
    _RuleRow("system-control", "command", "shell,powershell", "high", "warn",
       "系统级控制命令（shutdown/reboot/poweroff/Stop-Computer 等）",
       "系统级操作影响面大，确认无未保存工作与服务依赖"),
    _RuleRow("py-shell-true", "code", "python", "medium", "warn",
       "subprocess/命令执行启用了 shell=True，若拼接不可信输入可能引发命令注入",
       "改用参数列表形式调用，避免 shell 拼接"),
    _RuleRow("py-os-system", "code", "python", "medium", "warn",
       "os.system 直接执行系统命令", "改用 subprocess 参数化调用"),
    _RuleRow("py-eval-exec", "code", "python", "medium", "warn",
       "eval/exec 动态执行代码字符串", "避免执行不可信来源内容；确有必要时严格白名单校验"),
    _RuleRow("py-unsafe-deserialization", "code", "python", "medium", "warn",
       "不安全的反序列化（pickle / yaml.load）",
       "处理不可信数据时改用安全格式（JSON 等）或受信来源限制"),
    _RuleRow("py-rmtree-unprotected", "code", "python", "medium", "warn",
       "shutil.rmtree 删除目录树",
       "确认目标为工作区内可重建目录；循环批量删除时先列清单", loop=1),
    _RuleRow("js-eval-function", "code", "javascript", "medium", "warn",
       "eval / new Function 动态执行代码字符串",
       "避免执行不可信来源内容；改用手写解析或白名单映射"),
    _RuleRow("js-child-process-exec", "code", "javascript", "medium", "warn",
       "child_process 执行命令（exec/execSync/spawn）",
       "使用参数数组形式，避免命令拼接注入"),
    _RuleRow("js-fs-rm-force", "code", "javascript", "medium", "warn",
       "fs 删除文件/目录（rmSync/rm/unlinkSync）",
       "确认删除目标，循环内批量删除先列清单", loop=1),
    _RuleRow("js-inner-html", "code", "javascript", "medium", "warn",
       "innerHTML / document.write 注入 HTML 内容",
       "使用 textContent 或转义用户输入，避免 XSS"),
    _RuleRow("secret-assignment-generic", "code", "python,javascript", "medium", "warn",
       "疑似硬编码密钥/口令（api_key/token/password/secret 等）",
       "改用环境变量或密钥管理服务注入"),
    _RuleRow("secret-aws-access-key", "secret", "all", "high", "confirm",
       "检测到 AWS Access Key ID（AKIA...）格式内容",
       "不要将真实密钥写入文件或代码，改用环境变量/IAM 角色",
       pattern=r"\bAKIA[0-9A-Z]{16}\b"),
    _RuleRow("secret-github-pat", "secret", "all", "high", "confirm",
       "检测到 GitHub Personal Access Token（ghp_...）格式内容",
       "泄露的 PAT 应立即在 GitHub 撤销并轮换，改用 secret 注入",
       pattern=r"\bghp_[A-Za-z0-9]{36}\b"),
    _RuleRow("secret-openai-key", "secret", "all", "high", "confirm",
       "检测到 OpenAI API Key（sk-...）格式内容",
       "改用环境变量注入，避免密钥进入版本控制",
       pattern=r"\bsk-[A-Za-z0-9]{20,}\b"),
    _RuleRow("secret-private-key-content", "secret", "all", "high", "confirm",
       "检测到私钥块内容（BEGIN ... PRIVATE KEY）",
       "私钥绝不应写入普通文件，确认用途与存放位置后再操作",
       pattern=r"-----BEGIN (?:RSA|DSA|EC|OPENSSH|PGP) PRIVATE KEY(?: BLOCK)?"),
    _RuleRow("credential-file-delete", "boundary", "all", "high", "block",
       "删除密钥/凭据文件（id_rsa / *.pem / *.key / .env / .kube/config 等）",
       "确认密钥已备份或可重生成后再操作，避免不可恢复的凭据丢失"),
    _RuleRow("credential-file-write", "boundary", "all", "medium", "confirm",
       "写入密钥/凭据文件到工作区（id_rsa / *.pem / *.key / .env 等）",
       "确认该文件用途；凭据类文件建议加入 .gitignore 并限制权限"),
    _RuleRow("boundary-filesystem-root", "boundary", "all", "high", "block",
       "操作目标为系统根/家目录/盘符根",
       "工作区内的操作请使用精确相对路径", outside=True),
    _RuleRow("boundary-system-dir", "boundary", "all", "high", "confirm",
       "操作目标位于系统关键目录", "系统目录改动影响面大，确认后谨慎执行"),
    _RuleRow("delete-batch-escalation", "boundary", "all", "medium", "confirm",
       "本会话已累计多次删除操作，需确认后继续",
       "批量清理请先列出目标文件清单，确认无重要数据后再执行"),
)


def _build_builtin_rules(rows: tuple[_RuleRow, ...]) -> tuple[Rule, ...]:
    rules: list[Rule] = []
    for row in rows:
        rules.append(row.as_rule())
    return tuple(rules)


_BUILTIN_RULES: tuple[Rule, ...] = _build_builtin_rules(_BUILTIN_RULE_ROWS)
# fmt: on


RULES: dict[str, Rule] = {rule.id: rule for rule in _BUILTIN_RULES}


def normalize_path(path: str) -> str:
    return path.strip().strip("'\"").strip().replace("\\", "/").lower()


def _expand_user_path(path: str, workspace_root: str | None) -> str:
    value = path.strip().strip("'\"")
    lower = value.lower()
    if lower in {"~", "~/", "$home", "$home/"}:
        value = str(Path.home())
    elif lower.startswith("$home/"):
        value = str(Path.home() / value[len("$home/"):].lstrip("/\\"))
    elif lower.startswith("~/"):
        value = str(Path.home() / value[2:].lstrip("/\\"))
    if workspace_root and not Path(value).expanduser().is_absolute():
        value = str(Path(workspace_root) / value)
    return normalize_path(value)


def inside_workspace(path: str, workspace_root: str | None) -> bool:
    if not workspace_root:
        return not re.match(r"^[a-z]:/|^/|^~", path)
    if not Path(path).is_absolute() and ":/" not in path and not path.startswith("/"):
        return True
    workspace = normalize_path(workspace_root).rstrip("/")
    return path == workspace or path.startswith(workspace + "/")


def is_credential_filename(path: str) -> bool:
    return bool(_CREDENTIAL_FILENAME_RE.search(normalize_path(path)))


def _tokenize_command(segment: str) -> list[str]:
    try:
        return shlex.split(segment, posix=True)
    except ValueError:
        return re.findall(r"\S+", segment)


def _command_head(segment: str) -> tuple[str, list[str]]:
    tokens = _tokenize_command(segment)
    if not tokens:
        return "", []
    head = tokens[0].strip("'\"").lower().split("/")[-1]
    if head not in _COMMAND_WRAPPERS:
        return head, tokens[1:]
    index = 1
    while index < len(tokens):
        token = tokens[index]
        if token.startswith("-"):
            index += 2 if index + 1 < len(tokens) else 1
            continue
        break
    if index >= len(tokens):
        return head, []
    real = tokens[index].strip("'\"").lower().split("/")[-1]
    return real, tokens[index + 1:]


def _rm_is_recursive_force(tokens: list[str]) -> tuple[bool, bool]:
    recursive = False
    force = False
    for tok in tokens:
        lowered = tok.lower()
        if lowered == "--recursive":
            recursive = True
        elif lowered == "--force":
            force = True
        elif lowered.startswith("-") and not lowered.startswith("--"):
            letters = lowered[1:]
            recursive = recursive or "r" in letters
            force = force or "f" in letters
    return recursive, force


def _collect_targets(tokens: list[str]) -> list[str]:
    targets: list[str] = []
    for tok in tokens:
        lowered = tok.lower()
        if lowered in {"--", "--force", "--recursive", "-r", "-f", "-rf", "-fr"}:
            continue
        if (
            lowered.startswith("-")
            or lowered.startswith("of=")
            or lowered.startswith("if=")
        ):
            continue
        targets.append(tok)
    return targets


def _is_root_target(token: str) -> bool:
    lowered = normalize_path(token)
    if re.match(r"^[a-z]:/?$", lowered):
        return True
    return lowered in _UNIX_ROOT_TOKENS or lowered in _WIN_ROOT_TOKENS


def _is_home_root(token: str) -> bool:
    lowered = token.strip().lower().strip("'\"")
    if lowered in _HOME_TOKENS:
        return True
    return any(
        lowered.startswith(prefix)
        for prefix in ("~/", "$home/", "${home}/", "$env:userprofile", "%userprofile%")
    )


def _is_system_dir(token: str) -> bool:
    lowered = normalize_path(token).rstrip("/")
    if not lowered:
        return False
    return any(
        lowered == prefix or lowered.startswith(prefix + "/")
        for prefix in _SYSTEM_DIR_PREFIXES
    )


def _in_shell_loop(text: str, segment: str) -> bool:
    idx = text.find(segment)
    if idx < 0:
        return False
    return bool(
        re.search(r"\b(for|while)\s+", text[max(0, idx - 400):idx], re.IGNORECASE)
    )


def _snippet(text: str, start: int, length: int = 160) -> str:
    start = max(0, start)
    return text[start:start + length]


def _line_of(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def _finding_from_rule(
    rule_id: str,
    *,
    snippet: str,
    language: str,
    default: tuple[str, str] = ("medium", "warn"),
    target_path: str = "",
    workspace_root: str | None = None,
    in_loop: bool = False,
    line: int | None = None,
) -> Finding:
    rule = RULES.get(rule_id)
    severity = rule.severity if rule else default[0]
    action = rule.action if rule else default[1]
    if rule and rule.outside_workspace and target_path:
        if inside_workspace(
            _expand_user_path(target_path, workspace_root), workspace_root
        ):
            action = "allow"
    if rule and rule.loop_escalate and in_loop:
        severity, action = _escalate(severity, action)
    return Finding(
        rule_id=rule_id,
        category=rule.category if rule else "command",
        severity=severity,
        action=action,
        message=rule.message if rule and rule.message else f"命中风险规则 {rule_id}",
        snippet=(snippet or "")[:200],
        recommendation=rule.recommendation if rule else "",
        location=language,
        in_loop=in_loop,
        line=line,
    )


def _check_command_text(
    text: str,
    language: str,
    workspace_root: str | None,
) -> list[Finding]:
    findings: list[Finding] = []
    pipe_match = re.search(
        r"\b(curl|wget|iwr|invoke-webrequest|fetch|powershell)\b[^\n|]*\|\s*"
        r"(sudo\s+)?(sh|bash|zsh|ksh|dash|iex|powershell)\b",
        text,
        re.IGNORECASE,
    )
    if pipe_match:
        findings.append(
            _finding_from_rule(
                "pipe-download-exec",
                snippet=_snippet(text, pipe_match.start()),
                language=language,
            )
        )
    if re.search(r":\s*\(\s*\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;", text):
        findings.append(
            _finding_from_rule("fork-bomb", snippet=text[:80], language=language)
        )

    for seg in [item for item in re.split(r"[;&\n]", text) if item.strip()]:
        head, rest = _command_head(seg)
        if not head:
            continue
        in_loop = _in_shell_loop(text, seg)
        recursive, force = _rm_is_recursive_force(rest)
        if head in {"rm", "unlink"} and recursive and force:
            for target in _collect_targets(rest):
                if _is_root_target(target) or _is_home_root(target):
                    findings.append(
                        _finding_from_rule(
                            "rm-rf-filesystem-root",
                            snippet=_snippet(seg, 0),
                            language=language,
                            target_path=target,
                            workspace_root=workspace_root,
                            in_loop=in_loop,
                        )
                    )
        recurse_flags = {tok.lower() for tok in rest}
        if head in {"del", "erase"} and recurse_flags & {"/s", "-s", "--recursive"}:
            for target in _collect_targets(rest):
                if _is_root_target(target):
                    findings.append(
                        _finding_from_rule(
                            "win-bulk-delete",
                            snippet=_snippet(seg, 0),
                            language=language,
                            target_path=target,
                            workspace_root=workspace_root,
                            in_loop=in_loop,
                        )
                    )
        if head in {"rd", "rmdir", "remove-item"} and recurse_flags & {
            "/s",
            "/q",
            "-recurse",
            "-force",
            "-r",
            "-recursive",
        }:
            for target in _collect_targets(rest):
                if _is_root_target(target) or _is_home_root(target):
                    rule_id = (
                        "win-bulk-delete" if language == "powershell" else "rmdir-root"
                    )
                    findings.append(
                        _finding_from_rule(
                            rule_id,
                            snippet=_snippet(seg, 0),
                            language=language,
                            target_path=target,
                            workspace_root=workspace_root,
                            in_loop=in_loop,
                        )
                    )
        if (
            head == "mkfs"
            or head.startswith("mkfs.")
            or head in {"mke2fs", "mkdosfs", "format-volume"}
        ):
            findings.append(
                _finding_from_rule(
                    "format-filesystem",
                    snippet=_snippet(seg, 0),
                    language=language,
                )
            )
        if head == "format" and any(re.match(r"^[a-z]:$", tok.lower()) for tok in rest):
            findings.append(
                _finding_from_rule(
                    "format-drive", snippet=_snippet(seg, 0), language=language
                )
            )
        if head == "dd" and any(re.match(r"of\s*=\s*/dev/", tok) for tok in rest):
            findings.append(
                _finding_from_rule(
                    "dd-write-device",
                    snippet=_snippet(seg, 0),
                    language=language,
                )
            )
        if head in {"chmod", "chown"} and recursive:
            for target in _collect_targets(rest):
                if _is_root_target(target) or _is_system_dir(target):
                    findings.append(
                        _finding_from_rule(
                            "recursive-permission-change",
                            snippet=_snippet(seg, 0),
                            language=language,
                            target_path=target,
                            workspace_root=workspace_root,
                            in_loop=in_loop,
                        )
                    )
        if head in {
            "shutdown",
            "reboot",
            "poweroff",
            "halt",
            "init",
            "stop-computer",
            "restart-computer",
        }:
            findings.append(
                _finding_from_rule(
                    "system-control",
                    snippet=_snippet(seg, 0),
                    language=language,
                )
            )
    return findings


def _target_names(targets: list[ast.expr]) -> list[str]:
    names: list[str] = []
    for target in targets:
        if isinstance(target, ast.Name):
            names.append(target.id)
        elif isinstance(target, (ast.Tuple, ast.List)):
            names.extend(_target_names(target.elts))
        elif isinstance(target, ast.Attribute):
            names.append(target.attr)
        elif isinstance(target, ast.Subscript):
            names.append("__subscript__")
    return names


def _python_owner_name(func: ast.expr) -> tuple[str, str]:
    if isinstance(func, ast.Attribute):
        owner = ""
        if isinstance(func.value, ast.Name):
            owner = func.value.id
        elif isinstance(func.value, ast.Attribute):
            owner = func.value.attr
        return owner, func.attr
    if isinstance(func, ast.Name):
        return "", func.id
    return "", ""


def _emit_python_finding(
    findings: list[Finding],
    text: str,
    *,
    rule_id: str,
    node: ast.AST,
    loop_depth: int,
) -> None:
    findings.append(
        _finding_from_rule(
            rule_id,
            snippet=ast.get_source_segment(text, node) or "",
            language="python",
            in_loop=loop_depth > 0,
            line=getattr(node, "lineno", None),
        )
    )


def _scan_python_call(
    findings: list[Finding], text: str, node: ast.Call, *, loop_depth: int
) -> None:
    owner, name = _python_owner_name(node.func)
    if owner == "os" and name == "system":
        _emit_python_finding(
            findings, text, rule_id="py-os-system", node=node, loop_depth=loop_depth
        )
    if name in {"load", "loads"} and owner == "pickle":
        _emit_python_finding(
            findings,
            text,
            rule_id="py-unsafe-deserialization",
            node=node,
            loop_depth=loop_depth,
        )
    if name == "load" and owner in {"yaml", "YAML"}:
        _emit_python_finding(
            findings,
            text,
            rule_id="py-unsafe-deserialization",
            node=node,
            loop_depth=loop_depth,
        )
    if owner == "shutil" and name == "rmtree":
        _emit_python_finding(
            findings,
            text,
            rule_id="py-rmtree-unprotected",
            node=node,
            loop_depth=loop_depth,
        )
    if name in {"eval", "exec"} and not owner:
        _emit_python_finding(
            findings, text, rule_id="py-eval-exec", node=node, loop_depth=loop_depth
        )
    if owner in {"subprocess", "asyncio"} and name in {
        "run",
        "Popen",
        "call",
        "check_call",
        "check_output",
    }:
        for kw in node.keywords:
            if (
                kw.arg == "shell"
                and isinstance(kw.value, ast.Constant)
                and kw.value.value is True
            ):
                _emit_python_finding(
                    findings,
                    text,
                    rule_id="py-shell-true",
                    node=node,
                    loop_depth=loop_depth,
                )
                break


def _scan_python_secret_assign(
    findings: list[Finding],
    text: str,
    targets: list[ast.expr],
    value: ast.expr,
    *,
    loop_depth: int,
) -> None:
    if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
        return
    if len(value.value) < 8:
        return
    names = _target_names(targets)
    if not any(_SECRET_VAR_RE.fullmatch(item or "") for item in names):
        return
    _emit_python_finding(
        findings,
        text,
        rule_id="secret-assignment-generic",
        node=value,
        loop_depth=loop_depth,
    )


def _walk_python_ast(
    findings: list[Finding], text: str, node: ast.AST, *, loop_depth: int
) -> None:
    if isinstance(node, ast.Call):
        _scan_python_call(findings, text, node, loop_depth=loop_depth)
    elif isinstance(node, ast.Assign):
        _scan_python_secret_assign(
            findings, text, node.targets, node.value, loop_depth=loop_depth
        )
    elif isinstance(node, ast.AnnAssign) and node.value is not None:
        _scan_python_secret_assign(
            findings, text, [node.target], node.value, loop_depth=loop_depth
        )

    if isinstance(node, (ast.For, ast.While)):
        next_depth = loop_depth + 1
        for child in ast.iter_child_nodes(node):
            _walk_python_ast(findings, text, child, loop_depth=next_depth)
        return
    for child in ast.iter_child_nodes(node):
        _walk_python_ast(findings, text, child, loop_depth=loop_depth)


def _check_python(text: str) -> list[Finding]:
    findings: list[Finding] = []
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return findings

    _walk_python_ast(findings, text, tree, loop_depth=0)
    return findings


_JS_RULES = (
    ("js-eval-function", re.compile(r"\beval\s*\(|\bnew\s+Function\s*\(")),
    ("js-child-process-exec", re.compile(r"\b(?:exec|execSync|spawn|spawnSync)\s*\(")),
    ("js-fs-rm-force", re.compile(r"\b(?:rmSync|unlinkSync)\s*\(|\bfs\.rm\s*\(")),
    ("js-inner-html", re.compile(r"\binnerHTML\s*=|\bdocument\.write(?:ln)?\s*\(")),
    (
        "secret-assignment-generic",
        re.compile(
            r"\b(?:api[_-]?key|token|password|passwd|secret|access[_-]?key)\s*[:=]\s*['\"][^'\"]{8,}",
            re.IGNORECASE,
        ),
    ),
)


def _check_javascript(text: str) -> list[Finding]:
    findings: list[Finding] = []
    in_loop = bool(re.search(r"\b(for|while|do)\s*[\(\{]", text))
    for rule_id, pattern in _JS_RULES:
        for match in pattern.finditer(text):
            findings.append(
                _finding_from_rule(
                    rule_id,
                    snippet=_snippet(text, match.start()),
                    language="javascript",
                    in_loop=in_loop,
                    line=_line_of(text, match.start()),
                )
            )
    return findings


def _apply_pattern_rules(text: str, language: str) -> list[Finding]:
    findings: list[Finding] = []
    for rule in RULES.values():
        if not rule.pattern:
            continue
        if "all" not in rule.languages and language not in rule.languages:
            continue
        for match in list(rule.pattern.finditer(text))[:5]:
            findings.append(
                _finding_from_rule(
                    rule.id,
                    snippet=_snippet(text, match.start()),
                    language=language,
                    line=_line_of(text, match.start()),
                )
            )
    return findings


def evaluate(findings: list[Finding]) -> ScanResult:
    """聚合为最高决策与风险等级。"""
    active: list[Finding] = []
    counts: dict[str, int] = {}
    for finding in findings:
        counts[finding.rule_id] = counts.get(finding.rule_id, 0) + 1
    for finding in findings:
        rule = RULES.get(finding.rule_id)
        threshold = rule.repeat_escalate if rule else 0
        if (
            threshold
            and counts[finding.rule_id] >= threshold
            and finding.action != "block"
        ):
            finding.severity, finding.action = _escalate(
                finding.severity, finding.action
            )
        active.append(finding)

    decision = (
        max((item.action for item in active), key=decision_rank) if active else "allow"
    )
    risk = (
        max((item.severity for item in active), key=severity_rank) if active else "none"
    )
    executable = decision in {"allow", "warn"}
    if decision == "block":
        recommendation = "存在需要拦截的高危操作：请先核对目标与来源后再执行。"
    elif decision == "confirm":
        recommendation = (
            "存在需要确认的风险操作：请明确目的后再放行；涉及密钥请改用环境变量。"
        )
    elif decision == "warn":
        recommendation = "存在中等风险项：建议按命中规则逐条整改后再执行。"
    else:
        recommendation = "未命中需处置的风险规则；仍建议在隔离环境先行验证。"
    return ScanResult(risk, decision, executable, active, recommendation)


def scan_text(
    text: str,
    language: str = "generic",
    *,
    workspace_root: str | None = None,
    location: str = "",
) -> ScanResult:
    """对一段文本执行统一风险扫描。"""
    if not isinstance(text, str) or not text.strip():
        return ScanResult("none", "allow", True, [], "")
    lang = (language or "generic").strip().lower()
    if lang not in SUPPORTED_LANGUAGES:
        lang = "generic"
    findings: list[Finding] = []
    if lang in {"shell", "powershell", "generic"}:
        findings.extend(_check_command_text(text, lang, workspace_root))
    if lang == "python":
        findings.extend(_check_python(text))
    if lang == "javascript":
        findings.extend(_check_javascript(text))
    findings.extend(_apply_pattern_rules(text, lang))
    for finding in findings:
        if location:
            finding.location = location
        elif not finding.location:
            finding.location = lang
    return evaluate(findings)


def resolve_scan_path(path: str, workspace_root: str | None) -> Path:
    """解析为工作区内可读文件路径；越界抛 ValueError。"""
    expanded = Path(path).expanduser()
    base = Path(workspace_root) if workspace_root else Path(".").resolve()
    candidate = (
        (base / expanded).resolve()
        if not expanded.is_absolute()
        else expanded.resolve()
    )
    if workspace_root:
        try:
            candidate.relative_to(Path(workspace_root).resolve())
        except ValueError as exc:
            raise ValueError("path 超出工作区范围，只允许扫描工作区内的文件") from exc
    if not candidate.is_file():
        raise FileNotFoundError(f"文件不存在：{path}")
    return candidate


def language_for_path(path: str | Path) -> str:
    mapping = {
        ".py": "python",
        ".js": "javascript",
        ".mjs": "javascript",
        ".cjs": "javascript",
        ".sh": "shell",
        ".bash": "shell",
        ".zsh": "shell",
        ".ps1": "powershell",
        ".bat": "shell",
        ".cmd": "shell",
    }
    return mapping.get(Path(path).suffix.lower(), "generic")


def scan_file(
    file_path: str | Path,
    *,
    workspace_root: str | None = None,
    language: str = "",
) -> ScanResult:
    """读取并扫描工作区文件。"""
    path = resolve_scan_path(str(file_path), workspace_root)
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        content = handle.read(_MAX_FILE_SCAN_BYTES)
    result = scan_text(
        content,
        language or language_for_path(path),
        workspace_root=workspace_root,
        location=str(file_path),
    )
    if path.stat().st_size > _MAX_FILE_SCAN_BYTES:
        result.findings.append(
            Finding(
                rule_id="file-truncated",
                category="generic",
                severity="low",
                action="warn",
                message=f"文件超过 {_MAX_FILE_SCAN_BYTES // 1024}KB，仅扫描了前部内容",
                recommendation="对超长文件分段扫描或缩小范围",
                location=str(file_path),
            )
        )
        result = evaluate(result.findings)
    return result


def scan_path_targets(
    paths: Iterable[str],
    *,
    action: str,
    workspace_root: str | None,
    location: str = "",
) -> list[Finding]:
    """对文件操作目标路径做密钥文件名与边界判定。"""
    findings: list[Finding] = []
    for path in [item for item in paths if isinstance(item, str) and item.strip()]:
        expanded = _expand_user_path(path, workspace_root)
        if is_credential_filename(path):
            rule_id = (
                "credential-file-delete"
                if action == "delete"
                else "credential-file-write"
            )
            findings.append(
                _finding_from_rule(rule_id, snippet=path, language=location or action)
            )
            continue
        if _is_root_target(path) or _is_home_root(path):
            rule = RULES.get("boundary-filesystem-root")
            if (
                rule
                and rule.outside_workspace
                and inside_workspace(expanded, workspace_root)
            ):
                continue
            findings.append(
                _finding_from_rule(
                    "boundary-filesystem-root",
                    snippet=path,
                    language=location or action,
                )
            )
            continue
        if _is_system_dir(path):
            findings.append(
                _finding_from_rule(
                    "boundary-system-dir",
                    snippet=path,
                    language=location or action,
                )
            )
    return findings


def scan_text_for_files(
    contents: Iterable[str],
    workspace_root: str | None,
    location: str,
) -> list[Finding]:
    """对写入文件的文本做内容级扫描（密钥等）。"""
    findings: list[Finding] = []
    for text in contents:
        if not isinstance(text, str) or not text.strip():
            continue
        findings.extend(_apply_pattern_rules(text, "generic"))
    for finding in findings:
        if not finding.location:
            finding.location = location
    return findings


class RiskScanTool(Tool):
    """代码/命令/脚本/文件风险扫描：固定契约输出。"""

    def __init__(self) -> None:
        super().__init__(
            ToolCard(
                id="risk_scan",
                name="risk_scan",
                description=(
                    "代码/命令/脚本/文件风险扫描器：对一段文本或工作区文件做静态风险扫描，"
                    "返回 risk_level、decision（allow/warn/confirm/block）、executable 与逐条 "
                    "findings。用户提供代码/文件附件、要求评估执行风险、检查危险指令或写入内容"
                    "是否含密钥时，必须先调用本工具再给结论。"
                ),
                input_params={
                    "type": "object",
                    "properties": {
                        "content": {
                            "type": "string",
                            "description": "待扫描的代码、命令或脚本文本（与 path 二选一）",
                        },
                        "path": {
                            "type": "string",
                            "description": "工作区内的文件路径；由工具读取后扫描（与 content 二选一）",
                        },
                        "language": {
                            "type": "string",
                            "enum": list(SUPPORTED_LANGUAGES),
                            "description": (
                                "内容语言：shell / python / javascript / powershell / generic。"
                                "缺省时按 path 后缀识别，纯文本按 generic"
                            ),
                        },
                    },
                },
            )
        )

    async def invoke(self, inputs: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        try:
            payload = inputs or {}
            content = payload.get("content")
            path = payload.get("path")
            language = str(payload.get("language") or "").strip().lower()
            if language and language not in SUPPORTED_LANGUAGES:
                return {
                    "success": False,
                    "error": f"不支持的 language：{language}（支持 {', '.join(SUPPORTED_LANGUAGES)}）",
                }
            if not content and not path:
                return {
                    "success": False,
                    "error": "缺少 content 或 path 参数，请至少提供其一",
                }
            if content and path:
                return {
                    "success": False,
                    "error": "content 与 path 请二选一，不要同时提供",
                }

            from openjiuwen.core.sys_operation.cwd import get_cwd
            from openjiuwen.core.sys_operation.cwd import get_workspace

            ws_root = get_workspace() or get_cwd()
            if path:
                result = scan_file(
                    resolve_scan_path(str(path), ws_root),
                    workspace_root=ws_root,
                    language=language,
                )
            else:
                text = content if isinstance(content, str) else ""
                if isinstance(content, (list, tuple)):
                    text = "\n".join(str(item) for item in content)
                if not text.strip():
                    return {"success": False, "error": "content 为空"}
                result = scan_text(
                    text,
                    language or "generic",
                    workspace_root=ws_root,
                )
        except FileNotFoundError as exc:
            return {"success": False, "error": str(exc)}
        except ValueError as exc:
            return {"success": False, "error": str(exc)}
        except Exception:  # noqa: BLE001
            return {"success": False, "error": "风险扫描失败，请检查输入内容格式"}

        body = result.to_dict()
        body["success"] = True
        body["source"] = "coding-guard"
        return body

    async def stream(self, inputs: dict[str, Any], **kwargs: Any) -> Any:
        yield await self.invoke(inputs, **kwargs)


__all__ = [
    "RAIL_PRIORITY",
    "Finding",
    "RiskScanTool",
    "ScanResult",
    "evaluate",
    "RULES",
    "scan_path_targets",
    "scan_text",
    "scan_text_for_files",
]
