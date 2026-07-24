"""model_routing.privacy — privacy detection.

隐私检测规则从 ``model_routing_privacy.json`` 加载（用户目录 > 包内模板）。
加载失败 → 使用内置硬编码兜底规则（不 raise，隐私检测不中断主流程）。
"""
from __future__ import annotations
import json
import re
from jiuwenswarm.common.utils import logger

# --------------------------------------------------------------------------- #
# JSON 加载
# --------------------------------------------------------------------------- #


def _ensure_user_copy(filename: str) -> None:
    """确保用户 routing_state 目录下有 filename 的副本。

    若用户目录下不存在，从包内模板拷贝过去，让用户可以自定义覆盖。
    已存在则不覆盖（保留用户的修改）。
    """
    import shutil
    try:
        from jiuwenswarm.common.utils import get_config_dir, _find_package_root
    except Exception:
        return
    try:
        pkg_root = _find_package_root()
        if pkg_root is None:
            return
        src = pkg_root / "resources" / filename
        if not src.exists():
            return
        dst_dir = get_config_dir() / "routing_state"
        dst_dir.mkdir(parents=True, exist_ok=True)
        dst = dst_dir / filename
        if not dst.exists():
            shutil.copy2(src, dst)
            logger.info("[ModelRouting] copied template %s to %s", filename, dst)
    except Exception as exc:
        logger.debug("[ModelRouting] template copy for %s failed: %s", filename, exc)


# 内置兜底规则：JSON 加载失败时使用，保证隐私检测不中断。
_FALLBACK_PATTERNS = [
    re.compile(r"(?i)\b(password|passwd|pwd|secret|api[_-]?key|access[_-]?token|"
               r"private[_-]?key|credential|bearer)\b"),
    re.compile(r"(密码|身份证|手机号|电话号码|银行卡|社保号|私钥|密钥|验证码|隐私)"),
    re.compile(r"\b\d{15}\b|\b\d{17}[\dXx]\b"),
    re.compile(r"\b1[3-9]\d{9}\b"),
    re.compile(r"(?i)Bearer\s+[A-Za-z0-9._-]+"),
    re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"),
]


def _load_privacy_patterns() -> tuple[list[re.Pattern], dict[str, str]]:
    """从 JSON 加载隐私检测规则。

    先确保用户目录有模板副本（从包内拷贝），然后按顺序加载：
    1. ~/.jiuwenswarm/config/routing_state/model_routing_privacy.json（用户自定义）
    2. <package>/resources/model_routing_privacy.json（包内兜底）

    加载失败 → 返回内置兜底规则（不 raise，隐私检测不中断主流程）。

    返回 (compiled_patterns, label_map)。
    label_map: {pattern_label: raw_regex_string}，用于日志和调试。
    """
    _ensure_user_copy("model_routing_privacy.json")

    paths = []
    try:
        from jiuwenswarm.common.utils import get_config_dir
        paths.append(get_config_dir() / "routing_state" / "model_routing_privacy.json")
    except Exception:
        pass
    try:
        from jiuwenswarm.common.utils import _find_package_root
        paths.append(_find_package_root() / "resources" / "model_routing_privacy.json")
    except Exception:
        pass

    config_path = next((p for p in paths if p.exists()), None)
    if config_path is None:
        logger.debug(
            "[ModelRouting] privacy config not found, tried: %s; using fallback",
            ", ".join(str(p) for p in paths) or "(no paths)",
        )
        return list(_FALLBACK_PATTERNS), {}

    try:
        with open(config_path, encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception as exc:
        logger.warning("[ModelRouting] privacy config load failed (%s): %s; using fallback", config_path, exc)
        return list(_FALLBACK_PATTERNS), {}

    if not isinstance(cfg, dict) or not isinstance(cfg.get("patterns"), list):
        logger.warning("[ModelRouting] privacy config invalid (missing 'patterns' list): %s; using fallback", config_path)
        return list(_FALLBACK_PATTERNS), {}

    compiled: list[re.Pattern] = []
    label_map: dict[str, str] = {}
    for item in cfg["patterns"]:
        if not isinstance(item, dict):
            continue
        raw = item.get("regex", "")
        label = str(item.get("label", ""))
        if not raw:
            continue
        try:
            compiled.append(re.compile(raw))
            if label:
                label_map[label] = raw
        except re.error as exc:
            logger.warning("[ModelRouting] privacy pattern '%s' invalid regex: %s; skipped", label or raw, exc)

    if not compiled:
        logger.warning("[ModelRouting] privacy config has no valid patterns: %s; using fallback", config_path)
        return list(_FALLBACK_PATTERNS), {}

    logger.info("[ModelRouting] privacy config loaded from %s: patterns=%d", config_path, len(compiled))
    return compiled, label_map


_PRIVACY_PATTERNS, _PATTERN_LABELS = _load_privacy_patterns()


# --------------------------------------------------------------------------- #
# 隐私检测
# --------------------------------------------------------------------------- #


def _check_privacy(text: str) -> bool:
    """检查文本是否包含隐私/敏感信息."""
    if not text:
        return False
    return any(pat.search(text) for pat in _PRIVACY_PATTERNS)
