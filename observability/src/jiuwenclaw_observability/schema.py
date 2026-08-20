"""Single source of truth for the audit_rules table schema + default rules.

Imported by both:
  - observability web (db.py) — creates DB + table + seeds defaults on startup
  - AgentServer (rule_loader.py) — ensures DB + table exist on first access
"""

from __future__ import annotations

from openjiuwen_runtime.foundation.db.table_def import ColumnDefinition, IndexDefinition, TableDefinition

AUDIT_RULES_TABLE = TableDefinition(
    table_name="audit_rules",
    columns=[
        ColumnDefinition(name="id", data_type="integer", primary_key=True, nullable=False, autoincrement=True),
        ColumnDefinition(name="rule_name", data_type="string", nullable=False, length=100),
        ColumnDefinition(name="detector", data_type="string", nullable=False, length=50),
        ColumnDefinition(name="pattern", data_type="text", nullable=False, length=2000),
        ColumnDefinition(name="severity", data_type="string", nullable=True, default="medium", length=20),
        ColumnDefinition(name="action", data_type="string", nullable=True, default="log", length=20),
        ColumnDefinition(name="enabled", data_type="integer", nullable=False, default=1),
        ColumnDefinition(name="description", data_type="text", nullable=True, length=500),
        ColumnDefinition(name="created_at", data_type="datetime", nullable=True),
        ColumnDefinition(name="updated_at", data_type="datetime", nullable=True),
    ],
    indexes=[
        IndexDefinition(columns=["detector"], name="ix_audit_rules_detector"),
        IndexDefinition(columns=["rule_name"], name="ix_audit_rules_rule_name"),
    ],
)

DEFAULT_RULES = [
    {
        "detector": "tool_risk",
        "rule_name": "sql_dangerous_keyword",
        "pattern": r"\b(DROP\s+(TABLE|DATABASE|INDEX|SCHEMA)|TRUNCATE\s+TABLE|"
                  r"DELETE\s+FROM\s+\w+\s*$|ALTER\s+(TABLE|DATABASE|USER)|"
                  r"GRANT\s+ALL|REVOKE\s+ALL|SHUTDOWN|DROP\s+USER)\b",
        "severity": "high",
        "action": "log",
        "enabled": 1,
        "description": "SQL dangerous keywords",
    },
    {
        "detector": "tool_risk",
        "rule_name": "shell_dangerous_command",
        "pattern": r"(rm\s+-rf?\s+/|chmod\s+777|chown\s+-R\s+root|"
                  r"curl\s+.*\|\s*(bash|sh|zsh)|wget\s+.*\|\s*(bash|sh|zsh)|"
                  r"mkfs\.\w+|dd\s+.*of=/dev/[sh]d|:\(\)\{.*\|.*&|"
                  r"kill\s+-9\s+1\b|sudo\s+(rm|chmod|chown|dd|mkfs)|"
                  r">\s*/dev/[sh]d[a-z]|nc\s+.*-e\s+/bin/(ba)?sh|"
                  r"python\s+-c\s+import\s+os)",
        "severity": "high",
        "action": "log",
        "enabled": 1,
        "description": "Shell dangerous commands",
    },
    {
        "detector": "tool_risk",
        "rule_name": "sensitive_path_access",
        "pattern": r"(/etc/(passwd|shadow|sudoers)|/root/\.\w+|"
                  r"/proc/(1|self)/|/var/log/|/\.ssh/(id_|authorized|known))",
        "severity": "medium",
        "action": "log",
        "enabled": 1,
        "description": "Sensitive system paths",
    },
    {
        "detector": "tool_risk",
        "rule_name": "api_key_in_arguments",
        "pattern": r"(sk-[a-zA-Z0-9]{20,}|AKIA[0-9A-Z]{16}|"
                  r"Bearer\s+[a-zA-Z0-9._\-]{20,})",
        "severity": "medium",
        "action": "log",
        "enabled": 1,
        "description": "API key leakage in arguments",
    },
    {
        "detector": "pii",
        "rule_name": "id_card",
        "pattern": r"(?<!\d)[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])"
                  r"(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx](?!\d)",
        "severity": "high",
        "action": "warn",
        "enabled": 1,
        "description": "Chinese ID card",
    },
    {
        "detector": "pii",
        "rule_name": "phone",
        "pattern": r"(?<!\d)1[3-9]\d{9}(?!\d)",
        "severity": "high",
        "action": "warn",
        "enabled": 1,
        "description": "Chinese mobile phone",
    },
    {
        "detector": "pii",
        "rule_name": "email",
        "pattern": r"(?<![a-zA-Z0-9._%+\-])[a-zA-Z0-9._%+\-]+"
                  r"@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}(?![a-zA-Z])",
        "severity": "medium",
        "action": "warn",
        "enabled": 1,
        "description": "Email address",
    },
    {
        "detector": "pii",
        "rule_name": "api_key",
        "pattern": r"\bsk-[a-zA-Z0-9]{20,}\b|\bBearer\s+[a-zA-Z0-9._\-]{20,}\b"
                  r"|\bAKIA[0-9A-Z]{16}\b",
        "severity": "high",
        "action": "warn",
        "enabled": 1,
        "description": "API keys",
    },
    {
        "detector": "pii",
        "rule_name": "bank_card",
        "pattern": r"(?<!\d)622[0-9]\d{12,16}(?!\d)|(?<!\d)62[0-9]{2}\d{12,16}(?!\d)",
        "severity": "high",
        "action": "warn",
        "enabled": 1,
        "description": "Bank card number",
    },
    {
        "detector": "safety",
        "rule_name": "injection_pattern_0",
        "pattern": r"ignore\s+(all\s+)?(previous|prior|above)\s+instructions?",
        "severity": "high",
        "action": "block",
        "enabled": 1,
        "description": "Prompt injection: ignore instructions",
    },
    {
        "detector": "safety",
        "rule_name": "injection_pattern_1",
        "pattern": r"disregard\s+(all\s+)?(previous|prior|above)\s+(instructions?|prompts?)",
        "severity": "high",
        "action": "block",
        "enabled": 1,
        "description": "Prompt injection: disregard",
    },
    {
        "detector": "safety",
        "rule_name": "injection_pattern_2",
        "pattern": r"you\s+are\s+now\s+(a|an)\s+\w+",
        "severity": "high",
        "action": "block",
        "enabled": 1,
        "description": "Prompt injection: role override",
    },
    {
        "detector": "safety",
        "rule_name": "injection_pattern_3",
        "pattern": r"act\s+as\s+(if\s+)?(you\s+are|a|an)\s+",
        "severity": "high",
        "action": "block",
        "enabled": 1,
        "description": "Prompt injection: act as",
    },
    {
        "detector": "safety",
        "rule_name": "injection_pattern_4",
        "pattern": r"(system|developer|admin)\s*(prompt|instruction|message)\s*:",
        "severity": "high",
        "action": "block",
        "enabled": 1,
        "description": "Prompt injection: system prompt leak",
    },
    {
        "detector": "safety",
        "rule_name": "injection_pattern_5",
        "pattern": r"\bDAN\b.*\b(jailbreak|do anything now)\b",
        "severity": "high",
        "action": "block",
        "enabled": 1,
        "description": "Jailbreak: DAN",
    },
    {
        "detector": "safety",
        "rule_name": "injection_pattern_6",
        "pattern": r"pretend\s+(you\s+are|to\s+be)\s+",
        "severity": "high",
        "action": "block",
        "enabled": 1,
        "description": "Prompt injection: pretend",
    },
    {
        "detector": "safety",
        "rule_name": "injection_pattern_7",
        "pattern": r"forget\s+(everything|all\s+(previous|prior))",
        "severity": "high",
        "action": "block",
        "enabled": 1,
        "description": "Prompt injection: forget",
    },
    {
        "detector": "safety",
        "rule_name": "injection_pattern_8",
        "pattern": r"override\s+(your|the)\s+(system|safety|rules?|guidelines?)",
        "severity": "high",
        "action": "block",
        "enabled": 1,
        "description": "Prompt injection: override",
    },
    {
        "detector": "safety",
        "rule_name": "injection_pattern_9",
        "pattern": r"reveal\s+(your|the)\s+(system\s+)?prompt",
        "severity": "high",
        "action": "block",
        "enabled": 1,
        "description": "Prompt injection: reveal prompt",
    },
    {
        "detector": "safety",
        "rule_name": "injection_pattern_10",
        "pattern": r"(\[/?system\]|\[/?admin\]|\[/?developer\])",
        "severity": "high",
        "action": "block",
        "enabled": 1,
        "description": "Prompt injection: tag injection",
    },
    {
        "detector": "safety",
        "rule_name": "jailbreak_high",
        "pattern": r"(DAN|do\s+anything\s+now|developer\s+mode|"
                  r"evil\s+(twin|mode)|unrestricted\s+ai|jailbreak)",
        "severity": "high",
        "action": "block",
        "enabled": 1,
        "description": "Jailbreak patterns",
    },
    {
        "detector": "safety",
        "rule_name": "violence",
        "pattern": r"(如何|怎么).*(制造|获取|购买|制作).*(炸弹|武器|枪支|毒品|毒药)|"
                  r"(how\s+to).*(make|build|get).*(bomb|weapon|gun|drug|poison)",
        "severity": "high",
        "action": "block",
        "enabled": 1,
        "description": "Violence / weapons / drugs",
    },
    {
        "detector": "safety",
        "rule_name": "illegal_activity",
        "pattern": r"(如何|怎么).*(洗钱|造假|破解|黑入|入侵|盗取)|"
                  r"(how\s+to).*(launder|counterfeit|hack|break\s+into|steal)",
        "severity": "high",
        "action": "block",
        "enabled": 1,
        "description": "Illegal activities",
    },
    {
        "detector": "safety",
        "rule_name": "self_harm",
        "pattern": r"(自杀|自残|轻生|割腕|吞药)|"
                  r"(suicide|self[\-\s]harm|kill\s+myself|end\s+my\s+life)",
        "severity": "high",
        "action": "block",
        "enabled": 1,
        "description": "Self-harm",
    },
]
