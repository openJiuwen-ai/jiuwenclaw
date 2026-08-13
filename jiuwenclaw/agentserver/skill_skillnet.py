# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""SkillNet 操作：搜索、安装、评估、下载、错误上下文."""

from __future__ import annotations

import logging
import os
from typing import Any

from jiuwenclaw.agentserver.skill_utils import (
    SkillNetEmptyDownloadError,
    _SKILLNET_DOWNLOAD_TIMEOUT,
    _SKILLNET_MAX_RETRIES,
    _configure_skillnet_requests_session,
    _skillnet_network_context,
)

logger = logging.getLogger(__name__)


def _get_github_token() -> str:
    return (os.getenv("GITHUB_TOKEN") or "").strip()


def _skillnet_eval_llm_params() -> dict[str, str | None]:
    """与主对话一致的 API Key / Base URL / 模型名（config.yaml react 段）."""
    try:
        from jiuwenclaw.config import get_config
    except Exception:
        return {
            "api_key": (os.getenv("API_KEY") or "").strip() or None,
            "base_url": (os.getenv("API_BASE") or "").strip() or None,
            "model": (os.getenv("MODEL_NAME") or "gpt-4o").strip(),
        }

    cfg = get_config() or {}
    react = cfg.get("react") or {}
    mcc = react.get("model_client_config") or {}
    api_key = (mcc.get("api_key") or os.getenv("API_KEY") or "").strip()
    base_url = (mcc.get("api_base") or os.getenv("API_BASE") or "").strip()
    model = (react.get("model_name") or os.getenv("MODEL_NAME") or "gpt-4o").strip()
    if base_url.endswith("/chat/completions"):
        base_url = base_url.rsplit("/chat/completions", 1)[0]
    return {
        "api_key": api_key or None,
        "base_url": base_url or None,
        "model": model or "gpt-4o",
    }


def skillnet_evaluate_sync(skill_url: str) -> dict[str, Any]:
    """同步 evaluate，供 asyncio.to_thread 调用."""
    try:
        from skillnet_ai import SkillNetClient
        from skillnet_ai.client import SkillNetError
    except Exception:
        return {
            "ok": False,
            "detail": "未安装 skillnet-ai，请先安装依赖: pip install skillnet-ai",
            "detail_key": "skills.skillNet.errors.skillnetAiMissing",
        }

    llm = _skillnet_eval_llm_params()
    if not llm.get("api_key"):
        return {
            "ok": False,
            "detail": "",
            "detail_key": "skills.skillNet.errors.evaluateNoApiKey",
        }

    kwargs: dict[str, Any] = {
        "api_key": llm["api_key"],
        "base_url": llm["base_url"],
        "github_token": _get_github_token() or None,
    }
    try:
        with _skillnet_network_context():
            client = SkillNetClient(**kwargs)
            result = client.evaluate(target=skill_url, model=str(llm["model"]))
    except SkillNetError as exc:
        return {"ok": False, "detail": str(exc).strip() or "评估失败。"}
    except Exception as exc:
        logger.exception("SkillNet evaluate 异常")
        return {"ok": False, "detail": str(exc).strip() or "评估失败。"}

    if not isinstance(result, dict):
        return {"ok": True, "evaluation": result}
    return {"ok": True, "evaluation": result}


def skillnet_search_sync(search_kwargs: dict[str, Any]) -> list[Any]:
    """同步调用 skillnet-ai search，供 asyncio.to_thread 使用."""
    try:
        from skillnet_ai.searcher import SkillNetSearcher
    except Exception as exc:
        raise RuntimeError("未安装 skillnet-ai，请先安装依赖: pip install skillnet-ai") from exc

    with _skillnet_network_context():
        searcher = SkillNetSearcher()
        _configure_skillnet_requests_session(searcher.session)
        results = searcher.search(**search_kwargs)
    if results is None:
        return []
    if isinstance(results, list):
        return results
    return list(results)


def _github_skillnet_install_error_context(skill_url: str) -> str:
    """下载失败时拉 GitHub Contents 与 rate_limit，把官方 message 等拼给前端."""
    try:
        from skillnet_ai.downloader import SkillDownloader
    except ImportError:
        return ""

    dl = SkillDownloader(api_token=_get_github_token())
    _configure_skillnet_requests_session(dl.session)
    parsed = dl._parse_github_url(skill_url)
    if not parsed:
        return ""

    owner, repo, ref, dir_path, _ = parsed
    api = f"https://api.github.com/repos/{owner}/{repo}/contents/{dir_path}?ref={ref}"
    try:
        with _skillnet_network_context():
            r = dl.session.get(api, timeout=_SKILLNET_DOWNLOAD_TIMEOUT)
    except Exception as exc:
        logger.debug("SkillNet 安装错误上下文: GitHub Contents 请求失败: %s", exc)
        return ""

    parts: list[str] = []
    if r.status_code != 200:
        try:
            body = r.json()
            msg = body.get("message")
            if isinstance(msg, str) and msg.strip():
                parts.append(msg.strip()[:800])
            else:
                raw = (r.text or "").strip()[:500]
                if raw:
                    parts.append(f"HTTP {r.status_code}: {raw}")
        except Exception as exc:
            logger.debug("SkillNet 安装错误上下文: 解析 GitHub 错误 JSON 失败: %s", exc)
            raw = (r.text or "").strip()[:500]
            if raw:
                parts.append(f"HTTP {r.status_code}: {raw}")

        if r.status_code == 403 or any("rate limit" in p.lower() for p in parts):
            try:
                with _skillnet_network_context():
                    rl = dl.session.get("https://api.github.com/rate_limit", timeout=12)
                if rl.status_code == 200:
                    core = rl.json().get("resources", {}).get("core") or {}
                    rem, lim = core.get("remaining"), core.get("limit")
                    if rem is not None and lim is not None:
                        parts.append(
                            f"GitHub 核心 API 剩余 {rem}/{lim}，"
                            "可在配置页「第三方服务」填写 github_token（GITHUB_TOKEN）提高额度"
                        )
            except Exception as exc:
                logger.debug(
                    "SkillNet 安装错误上下文: GitHub rate_limit 请求失败: %s",
                    exc,
                )

    return " | ".join(parts) if parts else ""


def skillnet_download_sync(skill_url: str, target_dir: str, mirror_url: str | None = None) -> str:
    """同步调用 skillnet-ai download；失败时附带 GitHub API 返回说明（如前端的限流文案）。"""
    try:
        from skillnet_ai.downloader import SkillDownloader, GitHubAPIError
    except Exception as exc:
        raise RuntimeError("未安装 skillnet-ai，请先安装依赖: pip install skillnet-ai") from exc

    token = _get_github_token()
    dl_kwargs: dict[str, Any] = {
        "api_token": token,
        "timeout": _SKILLNET_DOWNLOAD_TIMEOUT,
        "max_retries": _SKILLNET_MAX_RETRIES,
    }
    if mirror_url:
        dl_kwargs["mirror_url"] = mirror_url
    with _skillnet_network_context():
        downloader = SkillDownloader(**dl_kwargs)
        _configure_skillnet_requests_session(downloader.session)
        try:
            local_path = downloader.download(folder_url=skill_url, target_dir=target_dir)
        except GitHubAPIError:
            raise
        except Exception as exc:
            ctx = _github_skillnet_install_error_context(skill_url)
            if ctx:
                raise RuntimeError(f"{exc} | {ctx}") from exc
            raise
    if not local_path:
        ctx = _github_skillnet_install_error_context(skill_url)
        raise SkillNetEmptyDownloadError(github_context=ctx)
    return str(local_path)
