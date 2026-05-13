"""Enterprise edition gateway extension bundles (filesystem roots for ExtensionManager)."""

from __future__ import annotations

from pathlib import Path

__version__ = "0.1.0"

_ROOT = Path(__file__).resolve().parent


def extensions_root() -> Path:
    """九问网关侧外置扩展目录（含 agent_client、runtime_management 等）。"""
    return _ROOT / "extensions"


def yr_extensions_root() -> Path:
    """元戎网关侧外置扩展目录（如 ``agent_client`` 元戎客户端扩展）。"""
    return _ROOT / "yr_extensions"
