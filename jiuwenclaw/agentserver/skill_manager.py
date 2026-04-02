# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""SkillManager - 管理 skills 的加载、安装、卸载与 marketplace 操作."""

from __future__ import annotations

import logging
import asyncio
import json
import os
import re
import shutil
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import urlparse

from jiuwenclaw.utils import (
    get_agent_root_dir,
    get_agent_skills_dir,
    get_builtin_skills_dir,
)
from jiuwenclaw.evolution.schema import EvolutionEntry, EvolutionFile

logger = logging.getLogger(__name__)

_SKILLNET_DOWNLOAD_TIMEOUT: int = int(os.environ.get("SKILLNET_DOWNLOAD_TIMEOUT", "60"))
_SKILLNET_MAX_RETRIES: int = int(os.environ.get("SKILLNET_MAX_RETRIES", "3"))


# ---------------------------------------------------------------------------
# 默认路径
# ---------------------------------------------------------------------------
_EVOLUTION_FILENAME = "evolutions.json"



def _get_agent_root_dir() -> "Path":
    return get_agent_root_dir()


def _get_marketplace_dir() -> "Path":
    return get_agent_skills_dir() / "_marketplace"


def _get_state_file() -> "Path":
    return get_agent_skills_dir() / "skills_state.json"


class SkillNetEmptyDownloadError(Exception):
    """skillnet-ai ``download()`` returned None; 前端用 detail_key 做多语言。"""

    def __init__(self, *, github_context: str = "") -> None:
        self.github_context = (github_context or "").strip()
        self.detail_key = "skills.skillNet.errors.emptyDownloadResult"
        hint = f"\n{self.github_context[:800]}" if self.github_context else ""
        self.detail_params = {"hint": hint}
        super().__init__(self.github_context or "empty download path")


def _is_valid_http_mirror_url(url: str) -> bool:
    """Return True if url is a plausible http(s) mirror base (for SkillDownloader)."""
    s = url.strip()
    if not s or len(s) > 2048:
        return False
    parsed = urlparse(s)
    if parsed.scheme not in ("http", "https"):
        return False
    if not parsed.netloc:
        return False
    return True


def _safe_rmtree(path: Path) -> bool:
    """安全地删除目录树，处理 Windows 上的 git 文件锁定问题.
    """
    if not path.exists():
        return True

    import time
    import stat

    max_retries = 3
    retry_delay = 0.2

    for attempt in range(max_retries):
        try:
            shutil.rmtree(path)
            return True
        except OSError as exc:
            logger.debug("删除目录失败（尝试 %d/%d）: %s", attempt + 1, max_retries, exc)

            # 最后一次尝试失败，直接返回 False
            if attempt == max_retries - 1:
                logger.warning("删除目录失败（已重试 %d 次）: %s", max_retries, path)
                return False

            # 检查是否是 Windows 上的权限问题
            # 尝试修改文件权限
            if os.name == 'nt':
                try:
                    # 尝试递归修改权限
                    for root, dirs, files in os.walk(path):
                        for name in files + dirs:
                            filepath = Path(root) / name
                            try:
                                # 移除只读属性
                                if os.name == 'nt':
                                    os.chmod(filepath, stat.S_IWRITE)
                                elif os.name == 'posix':
                                    os.chmod(filepath, 0o777)
                                # 对目录，尝试删除其中的文件
                                if filepath.is_dir():
                                    try:
                                        shutil.rmtree(filepath)
                                    except OSError:
                                        pass  # 忽略子目录删除失败，外层会重试
                                elif filepath.is_file():
                                    try:
                                        os.unlink(filepath)
                                    except PermissionError:
                                        pass  # 忽略文件删除失败
                                # 小延迟
                                time.sleep(0.01)
                            except OSError:
                                pass  # 忽略权限修改失败
                except Exception:
                    pass  # 忽略其他异常

            # 等待后重试
            time.sleep(retry_delay)
            retry_delay *= 2

    return False


class SkillManager:
    """Skill 管理器，对应 skills.* 请求方法."""

    def __init__(self, workspace_dir: str | None = None) -> None:
        # 若传入 workspace_dir（harness adapter 使用），优先通过 Workspace/WorkspaceNode
        # 解析 skills 路径；否则使用全局默认路径（react adapter 或无参数时）。
        if workspace_dir is not None:
            try:
                from openjiuwen.harness.workspace.workspace import Workspace, WorkspaceNode
                workspace = Workspace(root_path=workspace_dir)
                skills_path = workspace.get_node_path(WorkspaceNode.SKILLS)
                self._skills_dir: Path = (
                    skills_path if skills_path is not None
                    else Path(workspace_dir) / WorkspaceNode.SKILLS.value
                )
            except ImportError:
                self._skills_dir = Path(workspace_dir) / "skills"
            self._agent_root: Path = Path(workspace_dir)
            self._marketplace_dir: Path = self._skills_dir / "_marketplace"
            self._state_file: Path = self._skills_dir / "skills_state.json"
        else:
            self._agent_root = _get_agent_root_dir()
            self._skills_dir = get_agent_skills_dir()
            self._marketplace_dir = _get_marketplace_dir()
            self._state_file = _get_state_file()
        self._skills_dir.mkdir(parents=True, exist_ok=True)
        self._state: dict[str, Any] = self._load_state()
        # SkillNet 异步安装：install 立即返回 install_id，后台下载；完成后调用 hook 重载 Agent
        self._skillnet_install_jobs: dict[str, dict[str, Any]] = {}
        self._skillnet_install_complete_hook: Callable[[], Awaitable[None]] | None = None

    def set_skillnet_install_complete_hook(
            self, hook: Callable[[], Awaitable[None]] | None
    ) -> None:
        """安装成功落盘后回调（通常为重载 Agent 实例）."""
        self._skillnet_install_complete_hook = hook

    # -----------------------------------------------------------------------
    # 公开 handler
    # -----------------------------------------------------------------------

    async def handle_skills_list(self, params: dict) -> dict:
        """返回所有可用 skill（本地 + marketplace 中未安装的）.

        params:
            refresh_marketplaces: bool (可选, 默认 False)
                为 True 时，先对已配置 marketplace 执行 clone/pull，再扫描列表。
            with_installed: bool (可选, 默认 False)
                为 True 时，同一次响应中附带 plugins（与 skills.installed 一致），
                避免网关串行处理两次 RPC 导致列表刷新超时或排队过久。
        """
        refresh_marketplaces = bool(params.get("refresh_marketplaces", False))
        if refresh_marketplaces:
            await self._sync_marketplace_repos()
        local = self._scan_local_skills()
        marketplace = self._scan_marketplace_skills()
        out: dict[str, Any] = {"skills": local + marketplace}
        if bool(params.get("with_installed", False)):
            installed = await self.handle_skills_installed(params)
            out["plugins"] = installed.get("plugins") or []
        return out

    async def handle_skills_installed(self, params: dict) -> dict:
        """返回已安装的 marketplace 插件列表.

        按前端期望格式返回：plugin_name, marketplace, spec, version, installed_at, git_commit, skills[]
        """
        raw_plugins = self._get_installed_plugins()
        plugins = []
        for p in raw_plugins:
            name = p.get("name", "")
            marketplace = p.get("marketplace", "")
            # 构造 spec (plugin_name@marketplace_name)
            spec = f"{name}@{marketplace}" if marketplace else name
            # 转换字段名以符合前端期望
            plugin = {
                "plugin_name": name,
                "marketplace": marketplace,
                "spec": spec,
                "version": p.get("version", ""),
                "installed_at": p.get("installed_at", ""),
                "git_commit": p.get("commit", ""),
                # skills 数组：通常一个 plugin 包含同名 skill
                "skills": [name] if name else [],
            }
            plugins.append(plugin)
        return {"plugins": plugins}

    async def handle_skills_get(self, params: dict) -> dict:
        """获取单个 skill 详情（name 必填）.

        返回字段转换：body -> content, path -> file_path
        """
        name = params.get("name")
        if not name:
            raise ValueError("缺少参数: name")

        # 先在本地 skills 目录中查找
        for child in self._skills_dir.iterdir():
            if child.name.startswith("_") or not child.is_dir():
                continue
            md = self._try_find_skill_file(child)
            if md is None:
                continue
            meta = self._parse_skill_md(md)
            if meta and meta.get("name") == name:
                # 字段转换以符合前端期望
                meta["content"] = meta.pop("body", "")
                meta["file_path"] = meta.pop("path", "")
                meta["source"] = self._resolve_skill_source(meta.get("name", ""))
                meta["is_builtin"] = self._is_builtin_skill(meta.get("name", ""), self._get_installed_plugins(), child)
                meta["has_evolutions"] = (child / _EVOLUTION_FILENAME).is_file()
                return meta

        # 再在 marketplace 目录中查找
        if self._marketplace_dir.exists():
            for repo_dir in self._marketplace_dir.iterdir():
                if not repo_dir.is_dir():
                    continue
                for plugin_dir in repo_dir.iterdir():
                    if not plugin_dir.is_dir():
                        continue
                    md = self._try_find_skill_file(plugin_dir)
                    if md is None:
                        continue
                    meta = self._parse_skill_md(md)
                    if meta and meta.get("name") == name:
                        # 字段转换以符合前端期望
                        meta["content"] = meta.pop("body", "")
                        meta["file_path"] = meta.pop("path", "")
                        marketplace_name = repo_dir.name
                        meta["source"] = marketplace_name
                        meta["marketplace"] = marketplace_name
                        meta["is_builtin"] = False
                        meta["has_evolutions"] = False
                        return meta

        raise ValueError(f"未找到 skill: {name}")

    async def handle_skills_evolution_status(self, params: dict) -> dict:
        """检查某个 skill 是否存在 evolutions.json."""
        name = str(params.get("name") or "").strip()
        if not name:
            raise ValueError("缺少参数: name")
        evo_path = self._get_skill_evolution_path(name)
        return {
            "name": name,
            "exists": bool(evo_path and evo_path.is_file()),
        }

    async def handle_skills_evolution_get(self, params: dict) -> dict:
        """获取某个 skill 的 evolutions.json 内容（重点返回 entries）."""
        name = str(params.get("name") or "").strip()
        if not name:
            raise ValueError("缺少参数: name")

        evo_path = self._get_skill_evolution_path(name)
        if evo_path is None or not evo_path.is_file():
            return {
                "name": name,
                "exists": False,
                "valid": True,
                "skill_id": name,
                "version": "1.0.0",
                "updated_at": "",
                "entries": [],
            }

        try:
            raw = json.loads(evo_path.read_text(encoding="utf-8"))
            evo_file = EvolutionFile.from_dict(raw)
            return {
                "name": name,
                "exists": True,
                "valid": True,
                **evo_file.to_dict(),
            }
        except Exception as exc:
            logger.warning("读取 evolutions.json 失败: skill=%s error=%s", name, exc)
            return {
                "name": name,
                "exists": True,
                "valid": False,
                "detail": "evolutions.json 格式错误或读取失败",
                "skill_id": name,
                "version": "1.0.0",
                "updated_at": "",
                "entries": [],
            }

    async def handle_skills_evolution_save(self, params: dict) -> dict:
        """保存某个 skill 的 evolutions.json 条目列表."""
        name = str(params.get("name") or "").strip()
        if not name:
            raise ValueError("缺少参数: name")

        if not self._resolve_local_skill_dir(name):
            raise ValueError(f"未找到 skill: {name}")

        entries = params.get("entries")
        if not isinstance(entries, list):
            raise ValueError("参数 entries 必须是数组")

        normalized_entries: list[EvolutionEntry] = []
        for idx, item in enumerate(entries):
            if not isinstance(item, dict):
                raise ValueError(f"entries[{idx}] 必须是对象")
            entry_id = str(item.get("id") or "").strip()
            content = item.get("change", {}).get("content") if isinstance(item.get("change"), dict) else None
            if not entry_id:
                raise ValueError(f"entries[{idx}].id 不能为空")
            if not isinstance(content, str):
                raise ValueError(f"entries[{idx}].change.content 必须是字符串")
            normalized_entries.append(EvolutionEntry.from_dict(item))

        evo_path = self._get_skill_evolution_path(name)
        evo_file = EvolutionFile.empty(skill_id=name)
        if evo_path and evo_path.is_file():
            try:
                current = json.loads(evo_path.read_text(encoding="utf-8"))
                evo_file = EvolutionFile.from_dict(current)
            except Exception as exc:
                logger.warning("读取原 evolutions.json 失败，将以新内容覆盖: skill=%s error=%s", name, exc)

        evo_file.entries = normalized_entries
        evo_file.updated_at = datetime.now(timezone.utc).isoformat()
        if not evo_file.skill_id:
            evo_file.skill_id = name

        if evo_path is None:
            raise ValueError(f"未找到 skill: {name}")
        evo_path.parent.mkdir(parents=True, exist_ok=True)
        evo_path.write_text(
            json.dumps(evo_file.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return {
            "success": True,
            "name": name,
            "entry_count": len(evo_file.entries),
            "updated_at": evo_file.updated_at,
        }

    async def handle_skills_marketplace_list(self, params: dict) -> dict:
        """列出已配置的 marketplace 源.

        返回格式符合前端期望：name, url, install_location?, last_updated?
        """
        marketplaces = self._get_marketplaces()
        # 为每个 marketplace 添加前端期望的可选字段
        result = []
        for m in marketplaces:
            item = {
                "name": m.get("name", ""),
                "url": m.get("url", ""),
                "enabled": bool(m.get("enabled", True)),
                "install_location": m.get("install_location"),
                "last_updated": m.get("last_updated"),
            }
            result.append(item)
        return {"marketplaces": result}

    async def handle_skills_install(self, params: dict) -> dict:
        """安装 marketplace 中的 skill.

        params:
            spec: "plugin_name@marketplace_name"
            force: bool (可选, 默认 False)
        """
        spec = params.get("spec", "")
        force = params.get("force", False)

        if "@" not in spec:
            return {"success": False, "detail": "spec 格式应为 plugin@marketplace"}

        plugin_name, marketplace_name = spec.rsplit("@", 1)
        if not plugin_name or not marketplace_name:
            return {"success": False, "detail": "plugin 或 marketplace 名称为空"}

        # 查找 marketplace 配置
        marketplace = None
        for m in self._get_marketplaces():
            if m.get("name") == marketplace_name:
                marketplace = m
                break
        if marketplace is None:
            return {"success": False, "detail": f"未找到 marketplace: {marketplace_name}"}

        git_url = marketplace.get("url", "")
        if not git_url:
            return {"success": False, "detail": f"marketplace {marketplace_name} 缺少 url"}

        # 确保 marketplace 仓库已 clone
        repo_dir = self._marketplace_dir / marketplace_name
        if repo_dir.exists():
            await self._git_pull(repo_dir)
        else:
            commit = await self._git_clone(git_url, repo_dir)
            if commit is None:
                return {"success": False, "detail": f"git clone 失败: {git_url}"}

        # 在仓库中查找 plugin 目录
        plugin_src = repo_dir / "skills" / plugin_name

        # 兼容单skill模式
        if not plugin_src.exists() or not plugin_src.is_dir():
            plugin_src = repo_dir
        if not plugin_src.is_dir():
            return {"success": False, "detail": f"在 marketplace 仓库中未找到 plugin: {plugin_name}"}

        md = self._try_find_skill_file(plugin_src)
        if md is None:
            return {"success": False, "detail": f"plugin {plugin_name} 缺少 SKILL.md"}

        # 复制到本地 skills 目录
        dest = self._skills_dir / plugin_name
        if dest.exists():
            if not force:
                return {"success": False, "detail": f"skill {plugin_name} 已存在"}
            _safe_rmtree(dest)
        shutil.copytree(plugin_src, dest)

        # 解析元数据并记录（添加 installed_at 时间戳）
        from datetime import datetime, timezone
        meta = self._parse_skill_md(self._try_find_skill_file(dest)) or {}
        commit_hash = await self._git_get_commit(repo_dir)
        self._add_installed_plugin({
            "name": plugin_name,
            "marketplace": marketplace_name,
            "version": meta.get("version", ""),
            "commit": commit_hash or "",
            "source": marketplace_name,
            "installed_at": datetime.now(timezone.utc).isoformat(),
        })
        self._refresh_agent_data_indexes()

        return {"success": True}

    async def handle_skills_skillnet_search(self, params: dict) -> dict:
        """在线搜索 SkillNet 技能."""
        query = str(params.get("q", "")).strip()
        if not query:
            return {"success": False, "detail": "缺少参数: q"}

        # 尽量与 SkillNet API 对齐，便于前端透传。
        search_kwargs: dict[str, Any] = {"q": query}
        if params.get("mode"):
            search_kwargs["mode"] = params.get("mode")
        if params.get("category"):
            search_kwargs["category"] = params.get("category")
        if params.get("limit") is not None:
            try:
                search_kwargs["limit"] = int(params.get("limit"))
            except Exception:
                return {"success": False, "detail": "参数 limit 必须是整数"}
        if params.get("page") is not None:
            try:
                search_kwargs["page"] = int(params.get("page"))
            except Exception:
                return {"success": False, "detail": "参数 page 必须是整数"}
        if params.get("min_stars") is not None:
            try:
                search_kwargs["min_stars"] = int(params.get("min_stars"))
            except Exception:
                return {"success": False, "detail": "参数 min_stars 必须是整数"}
        if params.get("sort_by"):
            search_kwargs["sort_by"] = params.get("sort_by")
        if params.get("threshold") is not None:
            try:
                search_kwargs["threshold"] = float(params.get("threshold"))
            except Exception:
                return {"success": False, "detail": "参数 threshold 必须是数字"}

        try:
            raw_results = await asyncio.to_thread(self._skillnet_search_sync, search_kwargs)
        except Exception as exc:
            logger.error("SkillNet 搜索失败: %s", exc)
            raw = str(exc).strip()
            if raw:
                return {"success": False, "detail": raw}
            return {
                "success": False,
                "detail": "搜索失败，请稍后重试。",
                "detail_key": "skills.skillNet.errors.searchFailedFallback",
            }

        normalized: list[dict[str, Any]] = []
        for item in raw_results:
            if hasattr(item, "dict"):
                try:
                    item = item.dict()
                except Exception:
                    item = vars(item)
            elif not isinstance(item, dict):
                item = vars(item)

            normalized.append({
                "skill_name": item.get("skill_name", item.get("name", "")),
                "skill_description": item.get("skill_description", item.get("description", "")),
                "author": item.get("author", ""),
                "stars": item.get("stars", 0),
                "skill_url": item.get("skill_url", item.get("url", "")),
                "category": item.get("category", ""),
            })

        return {
            "success": True,
            "query": query,
            "count": len(normalized),
            "skills": normalized,
        }

    async def handle_skills_skillnet_install(self, params: dict) -> dict:
        """从 SkillNet URL 异步安装：立即返回 install_id，不阻塞网关队列.

        前端应轮询 skills.skillnet.install_status 直至 status 为 done/failed。
        """
        skill_url = str(params.get("url", "")).strip()
        force = bool(params.get("force", False))
        if not skill_url:
            return {"success": False, "detail": "缺少参数: url"}

        mirror_url: str | None = None
        raw_mirror = params.get("mirror_url")
        if raw_mirror is not None:
            ms = str(raw_mirror).strip()
            if ms:
                if not _is_valid_http_mirror_url(ms):
                    return {
                        "success": False,
                        "detail": "mirror_url 不是有效的 http(s) 地址",
                        "detail_key": "skills.skillNet.errors.invalidMirrorUrl",
                    }
                mirror_url = ms

        install_id = uuid.uuid4().hex
        self._skillnet_install_jobs[install_id] = {"status": "pending"}
        asyncio.create_task(
            self._skillnet_install_background(
                install_id, skill_url, force, mirror_url
            ),
            name=f"skillnet_install_{install_id[:8]}",
        )
        return {
            "success": True,
            "pending": True,
            "install_id": install_id,
        }

    async def handle_skills_skillnet_install_status(self, params: dict) -> dict:
        """查询 SkillNet 异步安装状态."""
        install_id = str(params.get("install_id", "")).strip()
        if not install_id:
            return {"success": False, "detail": "缺少参数: install_id"}
        job = self._skillnet_install_jobs.get(install_id)
        if job is None:
            return {
                "success": False,
                "detail": "安装会话已过期，请重新点击安装。",
                "detail_key": "skills.skillNet.errors.sessionExpired",
            }

        status = job.get("status", "pending")
        if status == "pending":
            return {"success": True, "status": "pending"}
        if status == "failed":
            out: dict[str, Any] = {
                "success": False,
                "status": "failed",
                "detail": job.get("detail", "安装失败"),
            }
            if "detail_key" in job:
                out["detail_key"] = job["detail_key"]
            if "detail_params" in job:
                out["detail_params"] = job["detail_params"]
            return out
        # done
        return {
            "success": True,
            "status": "done",
            "skill": job.get("skill"),
        }

    async def handle_skills_skillnet_evaluate(self, params: dict) -> dict:
        """使用 skillnet-ai 的 evaluate，LLM 配置复用 react.model_client_config + model_name."""
        skill_url = str(params.get("url", "")).strip()
        if not skill_url:
            return {"success": False, "detail": "缺少参数: url"}

        try:
            out = await asyncio.to_thread(SkillManager._skillnet_evaluate_sync, skill_url)
        except Exception as exc:
            logger.error("SkillNet 评估失败: %s", exc)
            raw = str(exc).strip()
            return {
                "success": False,
                "detail": raw or "评估失败，请稍后重试。",
                "detail_key": "skills.skillNet.errors.evaluateFailedFallback",
            }

        if not out.get("ok"):
            detail = str(out.get("detail", "")).strip() or "评估失败，请稍后重试。"
            resp: dict[str, Any] = {"success": False, "detail": detail}
            if out.get("detail_key"):
                resp["detail_key"] = out["detail_key"]
            return resp

        return {"success": True, "evaluation": out.get("evaluation")}

    async def handle_skills_clawhub_get_token(self, params: dict) -> dict:
        """获取 ClawHub CLI token（已掩码）."""
        token = self._get_clawhub_token()
        return {
            "success": True,
            "token": self._mask_clawhub_token(token),
            "has_token": bool(token),
        }

    async def handle_skills_clawhub_set_token(self, params: dict) -> dict:
        """设置 ClawHub CLI token."""
        token = str(params.get("token", "")).strip()
        self._set_clawhub_token(token)
        return {
            "success": True,
            "token": self._mask_clawhub_token(token),
        }

    async def handle_skills_clawhub_search(self, params: dict) -> dict:
        """从 ClawHub 搜索技能.

        params:
            q: 搜索查询字符串 (必需)
            limit: 结果数量限制 (可选)
        """
        query = str(params.get("q", "")).strip()
        if not query:
            return {"success": False, "detail": "缺少参数: q"}

        token = self._get_clawhub_token()
        if not token:
            return {
                "success": False,
                "detail": "未配置 ClawHub CLI token，请先配置",
                "detail_key": "skills.clawhub.errors.tokenNotConfigured",
            }

        limit = params.get("limit")
        if limit is not None:
            try:
                limit = int(limit)
            except Exception:
                return {"success": False, "detail": "参数 limit 必须是整数"}

        try:
            import httpx
            base_url = "https://clawhub.ai"
            search_url = f"{base_url}/api/v1/search"

            headers = {}
            if token:
                headers["Authorization"] = f"Bearer {token}"

            search_params = {"q": query}
            if limit is not None:
                search_params["limit"] = limit

            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(
                    search_url,
                    params=search_params,
                    headers=headers,
                )
                response.raise_for_status()
                data = response.json()

                results = data.get("results", [])
                normalized = []
                for item in results:
                    normalized.append({
                        "slug": item.get("slug", ""),
                        "display_name": item.get("displayName", ""),
                        "summary": item.get("summary", ""),
                        "version": item.get("version", ""),
                        "updated_at": item.get("updatedAt", 0),
                    })

                return {
                    "success": True,
                    "query": query,
                    "count": len(normalized),
                    "skills": normalized,
                }
        except httpx.HTTPStatusError as exc:
            logger.error("ClawHub 搜索 HTTP 错误: %s", exc)
            detail = f"HTTP {exc.response.status_code}: {exc.response.text[:200]}"
            return {
                "success": False,
                "detail": detail,
                "detail_key": "skills.clawhub.errors.httpError",
            }
        except Exception as exc:
            logger.error("ClawHub 搜索失败: %s", exc)
            return {
                "success": False,
                "detail": str(exc)[:500],
                "detail_key": "skills.clawhub.errors.searchFailed",
            }

    async def handle_skills_clawhub_download(self, params: dict) -> dict:
        """从 ClawHub 下载技能.

        params:
            slug: skill slug (必需)
            version: 版本号 (可选，默认 latest)
            tag: 标签 (可选，如 latest)
            force: 强制覆盖 (可选，默认 False)
        """
        slug = str(params.get("slug", "")).strip()
        if not slug:
            return {"success": False, "detail": "缺少参数: slug"}

        token = self._get_clawhub_token()
        if not token:
            return {
                "success": False,
                "detail": "未配置 ClawHub CLI token，请先配置",
                "detail_key": "skills.clawhub.errors.tokenNotConfigured",
            }

        version = params.get("version")
        tag = params.get("tag")
        force = bool(params.get("force", False))

        # 检查 skill 是否已安装
        dest = self._skills_dir / slug
        if dest.exists() and not force:
            return {
                "success": False,
                "detail": f"技能 {slug} 已安装",
                "detail_key": "skills.clawhub.errors.skillAlreadyInstalled",
            }

        try:
            import httpx
            import zipfile
            import io

            base_url = "https://clawhub.ai"
            download_url = f"{base_url}/api/v1/download"

            headers = {}
            if token:
                headers["Authorization"] = f"Bearer {token}"

            download_params = {"slug": slug}
            if version:
                download_params["version"] = version
            if tag:
                download_params["tag"] = tag

            async with httpx.AsyncClient(timeout=120.0) as client:
                response = await client.get(
                    download_url,
                    params=download_params,
                    headers=headers,
                )
                response.raise_for_status()

                # 解压下载的内容
                with tempfile.TemporaryDirectory(prefix="jiuwenclari_clawhub_") as tmpdir:
                    tmp_path = Path(tmpdir)

                    # 保存 zip 文件
                    zip_content = io.BytesIO(response.content)
                    with zipfile.ZipFile(zip_content, "r") as zip_ref:
                        zip_ref.extractall(tmp_path)

                    # 查找 skill 目录
                    skill_dir = self._locate_skill_dir(tmp_path)
                    if skill_dir is None:
                        return {
                            "success": False,
                            "detail": "下载内容不完整，未找到 SKILL.md",
                            "detail_key": "skills.clawhub.errors.skillMdNotFound",
                        }

                    # 解析元数据
                    md = self._try_find_skill_file(skill_dir)
                    meta = self._parse_skill_md(md) if md else None
                    if meta is None:
                        return {
                            "success": False,
                            "detail": "无法解析下载的技能文件",
                            "detail_key": "skills.clawhub.errors.parseSkillFailed",
                        }

                    # 删除已存在的
                    if dest.exists():
                        if not force:
                            return {
                                "success": False,
                                "detail": f"技能 {slug} 已安装",
                                "detail_key": "skills.clawhub.errors.skillAlreadyInstalled",
                            }
                        _safe_rmtree(dest)

                    # 复制到 skills 目录
                    shutil.copytree(skill_dir, dest)
                    for mirror_root in self._get_mirror_skills_dirs():
                        mirror_dest = mirror_root / slug
                        if mirror_dest.exists():
                            if not force:
                                continue
                            _safe_rmtree(mirror_dest)
                        mirror_root.mkdir(parents=True, exist_ok=True)
                        shutil.copytree(skill_dir, mirror_dest)

                    # 记录安装信息
                    skill_name = meta.get("name", slug)
                    self._add_local_skill({
                        "name": skill_name,
                        "origin": f"clawhub:{slug}",
                        "source": "clawhub",
                        "installed_at": datetime.now(timezone.utc).isoformat(),
                    })
                    self._add_installed_plugin({
                        "name": skill_name,
                        "marketplace": "clawhub",
                        "version": meta.get("version", ""),
                        "commit": "",
                        "source": "clawhub",
                        "installed_at": datetime.now(timezone.utc).isoformat(),
                    })
                    self._refresh_agent_data_indexes()

                    return {
                        "success": True,
                        "skill": {"name": skill_name, "source": "clawhub"},
                    }

        except httpx.HTTPStatusError as exc:
            logger.error("ClawHub 下载 HTTP 错误: %s", exc)
            detail = f"HTTP {exc.response.status_code}: {exc.response.text[:200]}"
            return {
                "success": False,
                "detail": detail,
                "detail_key": "skills.clawhub.errors.httpError",
            }
        except Exception as exc:
            logger.error("ClawHub 下载失败: %s", exc)
            return {
                "success": False,
                "detail": str(exc)[:500],
                "detail_key": "skills.clawhub.errors.downloadFailed",
            }

    async def _skillnet_install_background(
            self,
            install_id: str,
            skill_url: str,
            force: bool,
            mirror_url: str | None = None,
    ) -> None:
        try:
            result = await asyncio.to_thread(
                self._skillnet_install_files_sync, skill_url, force, mirror_url
            )
        except Exception as exc:
            logger.error("SkillNet 后台安装异常: %s", exc)
            raw = str(exc).strip()
            self._skillnet_install_jobs[install_id] = {
                "status": "failed",
                "detail": raw or "安装失败，请重试。",
                **(
                    {}
                    if raw
                    else {
                        "detail_key": "skills.skillNet.errors.installFailedFallback",
                    }
                ),
            }
            return

        if not result.get("ok"):
            job_entry: dict[str, Any] = {
                "status": "failed",
                "detail": result.get("detail", "安装失败，请重试。"),
            }
            if result.get("detail_key"):
                job_entry["detail_key"] = result["detail_key"]
            if result.get("detail_params") is not None:
                job_entry["detail_params"] = result["detail_params"]
            self._skillnet_install_jobs[install_id] = job_entry
            return

        skill_name = result["skill_name"]
        meta = result["meta"]
        skill_url_stored = result["skill_url"]
        try:
            self._add_local_skill({
                "name": skill_name,
                "origin": skill_url_stored,
                "source": "skillnet",
                "installed_at": datetime.now(timezone.utc).isoformat(),
            })
            self._add_installed_plugin({
                "name": skill_name,
                "marketplace": "skillnet",
                "version": meta.get("version", ""),
                "commit": "",
                "source": "skillnet",
                "installed_at": datetime.now(timezone.utc).isoformat(),
            })
            self._refresh_agent_data_indexes()
        except Exception as exc:
            logger.error("SkillNet 写入状态失败: %s", exc)
            self._skillnet_install_jobs[install_id] = {
                "status": "failed",
                "detail": "安装完成但保存配置失败，请刷新页面重试。",
                "detail_key": "skills.skillNet.errors.saveConfigFailed",
            }
            return

        hook = self._skillnet_install_complete_hook
        if hook is not None:
            try:
                await hook()
            except Exception as exc:
                logger.error("SkillNet 安装完成后 hook 失败: %s", exc)
                self._skillnet_install_jobs[install_id] = {
                    "status": "failed",
                    "detail": "技能已安装，请手动刷新页面生效。",
                    "detail_key": "skills.skillNet.errors.reloadRequired",
                }
                return

        self._skillnet_install_jobs[install_id] = {
            "status": "done",
            "skill": {"name": skill_name, "source": "skillnet"},
        }

    def _skillnet_install_files_sync(
            self, skill_url: str, force: bool, mirror_url: str | None = None
    ) -> dict[str, Any]:
        """在工作线程中下载并拷贝到 skills 目录；返回 ok / skill_name / meta / skill_url."""
        try:
            with tempfile.TemporaryDirectory(prefix="jiuwenclaw_skillnet_") as tmpdir:
                tmp_path = Path(tmpdir)
                download_path_str = self._skillnet_download_sync(
                    skill_url, str(tmp_path), mirror_url
                )
                download_path = Path(download_path_str).resolve()
                if not download_path.exists():
                    return {
                        "ok": False,
                        "detail": "下载失败，请重试。",
                        "detail_key": "skills.skillNet.errors.downloadFailed",
                    }

                # 库在部分文件下载失败时仍会返回路径，只有找到 SKILL.md 才视为下载完整，才继续后续逻辑
                skill_dir = self._locate_skill_dir(download_path)
                if skill_dir is None:
                    return {
                        "ok": False,
                        "detail": "下载未完成或内容不完整，未找到 SKILL.md，请重试。",
                        "detail_key": "skills.skillNet.errors.skillMdNotFound",
                    }

                md = self._try_find_skill_file(skill_dir)
                meta = self._parse_skill_md(md) if md else None
                if meta is None:
                    return {
                        "ok": False,
                        "detail": "无法解析下载的技能文件",
                        "detail_key": "skills.skillNet.errors.parseSkillFailed",
                    }

                skill_name = str(meta.get("name", skill_dir.name)).strip() or skill_dir.name
                dest = self._skills_dir / skill_name
                if dest.exists():
                    if not force:
                        return {
                            "ok": False,
                            "detail": "该技能已安装。",
                            "detail_key": "skills.skillNet.errors.skillAlreadyInstalled",
                        }
                    _safe_rmtree(dest)

                shutil.copytree(skill_dir, dest)
                for mirror_root in self._get_mirror_skills_dirs():
                    mirror_dest = mirror_root / skill_name
                    if mirror_dest.exists():
                        if not force:
                            continue
                        _safe_rmtree(mirror_dest)
                    mirror_root.mkdir(parents=True, exist_ok=True)
                    shutil.copytree(skill_dir, mirror_dest)

                return {
                    "ok": True,
                    "skill_name": skill_name,
                    "meta": meta,
                    "skill_url": skill_url,
                }
        except SkillNetEmptyDownloadError as exc:
            logger.error("SkillNet 下载失败: %s", exc)
            out: dict[str, Any] = {
                "ok": False,
                "detail_key": exc.detail_key,
                "detail": "",
            }
            out["detail_params"] = exc.detail_params
            return out
        except Exception as exc:
            logger.error("SkillNet 下载失败: %s", exc)
            raw = str(exc).strip()
            detail = raw or "安装失败，请重试。"
            extra: dict[str, Any] = {}
            if not raw:
                extra["detail_key"] = "skills.skillNet.errors.installFailedFallback"
            return {"ok": False, "detail": detail, **extra}

    async def handle_skills_uninstall(self, params: dict) -> dict:
        """卸载已安装的 skill.

        params:
            name: skill 名称
        """
        name = params.get("name", "")
        if not name:
            return {"success": False, "detail": "缺少参数: name"}

        # 内置技能不允许删除（传入实际路径判断）
        dest = self._skills_dir / name
        if self._is_builtin_skill(name, self._get_installed_plugins(), dest):
            return {"success": False, "detail": "内置技能不允许删除"}

        if dest.exists() and dest.is_dir():
            _safe_rmtree(dest)
        for mirror_root in self._get_mirror_skills_dirs():
            mirror_dest = mirror_root / name
            if mirror_dest.exists() and mirror_dest.is_dir():
                _safe_rmtree(mirror_dest)

        self._remove_installed_plugin(name)
        self._remove_local_skill(name)
        self._refresh_agent_data_indexes()
        return {"success": True}

    async def handle_skills_import_local(self, params: dict) -> dict:
        """从本地路径导入 skill.

        params:
            path: 本地文件或目录路径
            force: bool (可选, 默认 False)
        """
        raw_path = params.get("path", "")
        force = params.get("force", False)
        if not raw_path:
            return {"success": False, "detail": "缺少参数: path"}

        src = Path(raw_path)
        if not src.exists():
            return {"success": False, "detail": f"路径不存在: {raw_path}"}

        if src.is_file():
            # 单文件导入：解析后放入以 name 命名的目录
            meta = self._parse_skill_md(src)
            if meta is None:
                return {"success": False, "detail": "无法解析 skill 文件"}
            skill_name = meta.get("name", src.stem)
            dest = self._skills_dir / skill_name
            if dest.exists():
                if not force:
                    return {"success": False, "detail": f"skill {skill_name} 已存在"}
                _safe_rmtree(dest)
            dest.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest / src.name)
        elif src.is_dir():
            md = self._try_find_skill_file(src)
            if md is None:
                return {"success": False, "detail": f"目录中未找到 SKILL.md: {raw_path}"}
            meta = self._parse_skill_md(md) or {}
            skill_name = meta.get("name", src.name)
            dest = self._skills_dir / skill_name
            if dest.exists():
                if not force:
                    return {"success": False, "detail": f"skill {skill_name} 已存在"}
                _safe_rmtree(dest)
            shutil.copytree(src, dest)
        else:
            return {"success": False, "detail": f"不支持的路径类型: {raw_path}"}

        self._add_local_skill({"name": skill_name, "origin": raw_path, "source": "local"})
        self._refresh_agent_data_indexes()
        return {"success": True, "skill": {"name": skill_name}}

    async def handle_skills_marketplace_add(self, params: dict) -> dict:
        """添加 marketplace 源.

        params:
            name: marketplace 名称
            url: git 仓库 URL
        """
        name = params.get("name", "")
        url = params.get("url", "")
        if not name or not url:
            return {"success": False, "detail": "缺少参数: name 和 url"}

        # 检查是否已存在
        for m in self._get_marketplaces():
            if m.get("name") == name:
                return {"success": False, "detail": f"marketplace 已存在: {name}"}

        # 新增源默认禁用，避免未经确认就触发远程同步。
        self._add_marketplace({"name": name, "url": url, "enabled": False})
        return {"success": True}

    async def handle_skills_marketplace_remove(self, params: dict) -> dict:
        """删除 marketplace 源.

        params:
            name: marketplace 名称
            remove_cache: 是否删除本地仓库缓存（可选，默认 True）
        """
        name = params.get("name", "")
        remove_cache = params.get("remove_cache", True)
        if not name:
            return {"success": False, "detail": "缺少参数: name"}

        removed = self._remove_marketplace(name)
        if not removed:
            return {"success": False, "detail": f"marketplace 不存在: {name}"}

        cache_removed = False
        if bool(remove_cache):
            repo_dir = self._marketplace_dir / name
            if repo_dir.exists() and repo_dir.is_dir():
                try:
                    _safe_rmtree(repo_dir)
                    cache_removed = True
                except Exception as exc:
                    logger.warning("删除 marketplace 缓存失败: %s", exc)

        return {
            "success": True,
            "name": name,
            "cache_removed": cache_removed,
        }

    async def handle_skills_marketplace_toggle(self, params: dict) -> dict:
        """启用或禁用 marketplace 源.

        params:
            name: marketplace 名称
            enabled: 目标状态
        """
        name = params.get("name", "")
        enabled = params.get("enabled")
        if not name:
            return {"success": False, "detail": "缺少参数: name"}
        if not isinstance(enabled, bool):
            return {"success": False, "detail": "缺少参数: enabled (bool)"}

        marketplace = next(
            (m for m in self._get_marketplaces() if m.get("name") == name),
            None,
        )
        if marketplace is None:
            return {"success": False, "detail": f"marketplace 不存在: {name}"}

        if enabled:
            repo_dir = self._marketplace_dir / name
            url = marketplace.get("url", "")
            if not url:
                return {"success": False, "detail": f"marketplace {name} 缺少 url"}

            detail = "已启用"
            if repo_dir.exists():
                commit = await self._git_pull(repo_dir)
                if commit is None:
                    return {"success": False, "name": name, "enabled": False, "detail": "git pull 失败"}
                detail = "已启用并执行 git pull"
            else:
                commit = await self._git_clone(url, repo_dir)
                if commit is None:
                    return {"success": False, "name": name, "enabled": False, "detail": "git clone 失败"}
                detail = "已启用并执行 git clone"

            self._set_marketplace_enabled(name, True)
            self._set_marketplace_last_updated(name)
            return {"success": True, "name": name, "enabled": True, "detail": detail}

        # 禁用：删除本地缓存目录，不卸载已安装 skill。
        repo_dir = self._marketplace_dir / name
        cache_removed = False
        if repo_dir.exists() and repo_dir.is_dir():
            cache_removed = _safe_rmtree(repo_dir)
            if not cache_removed:
                return {"success": False, "name": name, "enabled": True, "detail": "删除本地缓存失败"}

        self._set_marketplace_enabled(name, False)
        self._set_marketplace_last_updated(name)
        return {
            "success": True,
            "name": name,
            "enabled": False,
            "cache_removed": cache_removed,
            "detail": "已禁用并删除本地缓存" if cache_removed else "已禁用（无本地缓存）",
        }

    # -----------------------------------------------------------------------
    # SKILL.md 解析
    # -----------------------------------------------------------------------

    @staticmethod
    def _coerce_str_list(val: Any) -> list[str]:
        """frontmatter 里 tags/allowed_tools 可能是逗号分隔字符串，统一为 list[str]."""
        if val is None:
            return []
        if isinstance(val, list):
            return [str(x).strip() for x in val if str(x).strip()]
        if isinstance(val, str):
            s = val.strip()
            if not s:
                return []
            if "," in s:
                return [p.strip() for p in s.split(",") if p.strip()]
            return [s]
        return [str(val)]

    @staticmethod
    def _parse_skill_md(path: Path) -> dict | None:
        """解析 SKILL.md，提取 YAML frontmatter 和正文.

        支持两种格式:
        1. 有 frontmatter（--- 分隔的 YAML 头 + 正文）
        2. 无 frontmatter（整个文件作为 body，name 从文件名推断）
        """
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            logger.warning("无法读取文件: %s", path)
            return None

        meta: dict[str, Any] = {}
        body = text

        # 尝试解析 frontmatter
        fm_match = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)", text, re.DOTALL)
        if fm_match:
            fm_text = fm_match.group(1)
            body = fm_match.group(2).strip()
            # 简单 YAML 解析（key: value 格式），避免引入额外依赖
            for line in fm_text.splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                m = re.match(r"^(\w[\w_-]*)\s*:\s*(.*)", line)
                if m:
                    key = m.group(1)
                    val = m.group(2).strip()
                    # 处理 YAML 列表 [a, b, c]
                    if val.startswith("[") and val.endswith("]"):
                        inner = val[1:-1]
                        val = [v.strip().strip("'\"") for v in inner.split(",") if v.strip()]
                    # 去掉引号
                    elif val.startswith(("'", '"')) and val.endswith(("'", '"')):
                        val = val[1:-1]
                    meta[key] = val

        # 如果没有 name，从文件名推断
        if "name" not in meta:
            meta["name"] = path.stem

        # 默认字段
        meta.setdefault("description", "")
        meta.setdefault("version", "")
        meta.setdefault("author", "")
        meta["tags"] = SkillManager._coerce_str_list(meta.get("tags"))
        meta["allowed_tools"] = SkillManager._coerce_str_list(meta.get("allowed_tools"))

        meta["body"] = body
        meta["path"] = str(path)

        return meta

    @staticmethod
    def _try_find_skill_file(directory: Path) -> Path | None:
        """在目录中查找 skill 文件.

        优先查找 SKILL.md，其次查找任意 .md 文件.
        """
        skill_md = directory / "SKILL.md"
        if skill_md.is_file():
            return skill_md

        # 兼容：查找任意 .md 文件
        md_files = list(directory.glob("*.md"))
        if md_files:
            return md_files[0]

        return None

    # -----------------------------------------------------------------------
    # 内置技能判断
    # -----------------------------------------------------------------------

    def _is_builtin_skill(self, skill_name: str, installed_plugins: list[dict], skill_path: Path | None = None) -> bool:
        """判断技能是否为内置技能.

        内置技能的判断标准：
        1. 不在 local_skills 中（用户本地导入）
        2. 不在 installed_plugins 中（marketplace安装）
        3. 实际路径在源码内置路径下（通过 skill_path 参数判断）

        注意：如果 skill_path 不为 None，则直接判断该路径是否在 builtin_dir 下，
        这比仅通过 skill_name 判断更准确，避免名称冲突导致的误判。
        """
        try:
            # 检查是否在 local_skills 中（用户本地导入或SkillNet下载）
            for local_skill in self._state.get("local_skills", []):
                if local_skill.get("name") == skill_name:
                    return False

            # 检查是否在 installed_plugins 中记录（marketplace安装的）
            for plugin in installed_plugins:
                if plugin.get("name") == skill_name:
                    return False

            # 如果提供了 skill_path，直接判断该路径是否在 builtin_dir 下
            if skill_path is not None:
                builtin_dir = get_builtin_skills_dir()
                if builtin_dir.exists():
                    return skill_path.resolve().parent == builtin_dir.resolve()
                return False

            # 没有提供 skill_path 时，回退到通过 skill_name 判断（兼容旧代码）
            builtin_dir = get_builtin_skills_dir()
            if builtin_dir.exists():
                builtin_skill_path = builtin_dir / skill_name
                return builtin_skill_path.exists() and builtin_skill_path.is_dir()
            return False
        except Exception:
            return False

    # -----------------------------------------------------------------------
    # 目录扫描
    # -----------------------------------------------------------------------

    def _scan_local_skills(self) -> list[dict]:
        """扫描 agent/skills/ 下的本地 skill（跳过 _marketplace）."""
        results: list[dict] = []
        if not self._skills_dir.exists():
            return results

        for child in self._skills_dir.iterdir():
            if not child.is_dir() or child.name.startswith("_"):
                continue
            md = self._try_find_skill_file(child)
            if md is None:
                continue
            meta = self._parse_skill_md(md)
            if meta is None:
                continue

            # 判断 source 类型
            installed = self._get_installed_plugins()
            source = "project"
            for p in installed:
                if p.get("name") == meta.get("name"):
                    source = p.get("source", "project")
                    if source == "project" and p.get("marketplace"):
                        source = p.get("marketplace", "project")
                    break
            # 检查是否通过 import_local / SkillNet 等写入 local_skills（含 origin 供前端对照 skill_url）
            for ls in self._state.get("local_skills", []):
                if ls.get("name") == meta.get("name"):
                    source = ls.get("source", "local") if isinstance(ls, dict) else "local"
                    if isinstance(ls, dict):
                        origin = ls.get("origin")
                        if isinstance(origin, str) and origin.strip():
                            meta["origin"] = origin.strip()
                    break

            meta["source"] = source
            # 判断是否为内置技能（传入 child 路径，通过实际路径判断）
            meta["is_builtin"] = self._is_builtin_skill(meta.get("name", ""), self._get_installed_plugins(), child)
            meta["has_evolutions"] = (child / _EVOLUTION_FILENAME).is_file()
            # 不在列表中返回 body
            meta.pop("body", None)
            results.append(meta)

        return results

    def _resolve_skill_source(self, skill_name: str) -> str:
        """解析 skill 来源（local / project / marketplace 名称）."""
        if not skill_name:
            return "project"

        for plugin in self._get_installed_plugins():
            if plugin.get("name") == skill_name:
                source = plugin.get("source")
                marketplace = plugin.get("marketplace")
                if source == "project" and isinstance(marketplace, str) and marketplace:
                    return marketplace
                if isinstance(source, str) and source:
                    return source
                if isinstance(marketplace, str) and marketplace:
                    return marketplace
                return "project"

        for local_skill in self._state.get("local_skills", []):
            if local_skill.get("name") == skill_name:
                return "local"

        return "project"

    def _resolve_local_skill_dir(self, skill_name: str) -> Path | None:
        """根据 skill name 定位本地技能目录（仅 agent/skills 下）."""
        direct = self._skills_dir / skill_name
        if direct.is_dir():
            return direct

        if not self._skills_dir.exists():
            return None

        for child in self._skills_dir.iterdir():
            if not child.is_dir() or child.name.startswith("_"):
                continue
            md = self._try_find_skill_file(child)
            if md is None:
                continue
            meta = self._parse_skill_md(md)
            if meta and meta.get("name") == skill_name:
                return child
        return None

    def _get_skill_evolution_path(self, skill_name: str) -> Path | None:
        skill_dir = self._resolve_local_skill_dir(skill_name)
        if skill_dir is None:
            return None
        return skill_dir / _EVOLUTION_FILENAME

    def _scan_marketplace_skills(self) -> list[dict]:
        """扫描 _marketplace/ 下已 clone 的仓库中未安装的 skill.

        扫描路径：_marketplace/{marketplace_name}/skills/{plugin_name}
        """
        results: list[dict] = []
        if not self._marketplace_dir.exists():
            return results

        installed_names = {p.get("name") for p in self._get_installed_plugins()}

        enabled_marketplaces = {
            m.get("name")
            for m in self._get_marketplaces()
            if bool(m.get("enabled", True)) and m.get("name")
        }

        for repo_dir in self._marketplace_dir.iterdir():
            if not repo_dir.is_dir():
                continue
            marketplace_name = repo_dir.name
            if marketplace_name not in enabled_marketplaces:
                continue

            # 检查 skills 子目录是否存在
            skills_dir = repo_dir / "skills"
            # 单skill兼容
            is_skills_dir = True
            if not skills_dir.exists() or not skills_dir.is_dir():
                # 如果没有 skills 子目录，尝试直接扫描 repo_dir（兼容旧结构）
                skills_dir = repo_dir
                is_skills_dir = False

            for plugin_dir in skills_dir.iterdir():
                if not is_skills_dir and plugin_dir != repo_dir:
                    continue
                if not plugin_dir.is_dir():
                    continue
                # 跳过 git 元数据和以 _ 开头的目录
                if plugin_dir.name.startswith((".", "_")):
                    continue
                md = self._try_find_skill_file(plugin_dir)
                if md is None:
                    continue
                meta = self._parse_skill_md(md)
                if meta is None:
                    continue

                # 跳过已安装的
                if meta.get("name") in installed_names:
                    continue

                # source 直接返回 marketplace 名称，便于前端安装时自动拼接 spec
                meta["source"] = marketplace_name
                meta["marketplace"] = marketplace_name
                meta["is_builtin"] = False
                meta["has_evolutions"] = False
                meta.pop("body", None)
                results.append(meta)

        return results

    def _get_mirror_skills_dirs(self) -> list[Path]:
        """返回需要镜像同步的 skills 目录（不包含当前运行目录）.

        注意：开发模式下不返回源码目录作为镜像目标，避免用户下载的
        skill被复制到源码目录，重启后被误判为内置skill。
        """
        mirrors: list[Path] = []
        try:
            source_repo_root = Path(__file__).resolve().parents[2]
            source_resources_skills_dir = (
                    source_repo_root / "jiuwenclaw" / "resources" / "agent" / "skills"
            )
            # 开发模式下不将源码目录作为镜像目标
            # 这样用户下载的skill只保存在用户目录，不会污染源码目录
            if (
                    source_resources_skills_dir.exists()
                    and source_resources_skills_dir.resolve() != self._skills_dir.resolve()
                    and source_resources_skills_dir.resolve() != get_builtin_skills_dir().resolve()
            ):
                mirrors.append(source_resources_skills_dir)
        except Exception:
            return []
        return mirrors

    @staticmethod
    def _normalize_lang_suffix(name: str) -> str:
        """将 xxxx_zh.MD / xxxx_en.MD 规范为 xxxx.MD（去除 _zh/_en 后缀）。"""
        stem, suffix = name.rpartition(".")[0], name.rpartition(".")[2]
        suffix_lower = suffix.lower()
        if suffix_lower in ("md", "mdx"):
            stem_lower = stem.lower()
            if stem_lower.endswith("_zh"):
                stem = stem[:-3]
            elif stem_lower.endswith("_en"):
                stem = stem[:-3]
        return f"{stem}.{suffix}" if stem else name

    @staticmethod
    def _generate_agent_data_for_workspace(workspace_root: Path) -> None:
        """Generate agent/jiuwenclaw_workspace/agent-data.json from agent tree."""
        agent_root = workspace_root.resolve()
        output_path = (agent_root / "jiuwenclaw_workspace" / "agent-data.json").resolve()
        root_folder_key = "__root__"

        if not agent_root.exists() or not agent_root.is_dir():
            return

        folder_data: dict[str, list[dict[str, str | bool]]] = {}
        seen_paths: dict[str, set[str]] = {}
        for entry in sorted(agent_root.rglob("*")):
            if not entry.is_file():
                continue
            relative_file_path = entry.relative_to(agent_root).as_posix()
            relative_folder_path = entry.parent.relative_to(agent_root).as_posix()
            folder_key = root_folder_key if relative_folder_path == "." else relative_folder_path

            display_name = SkillManager._normalize_lang_suffix(entry.name)
            display_path = (
                f"agent/{relative_folder_path}/{display_name}".replace("/.", "/").replace("//", "/")
                if relative_folder_path != "."
                else f"agent/{display_name}"
            )

            seen = seen_paths.setdefault(folder_key, set())
            if display_path in seen:
                continue
            seen.add(display_path)

            folder_data.setdefault(folder_key, []).append(
                {
                    "name": display_name,
                    "path": display_path,
                    "isMarkdown": entry.suffix.lower() in {".md", ".mdx"},
                }
            )

        sorted_folder_data = {
            folder_key: sorted(files, key=lambda item: item["path"])
            for folder_key, files in sorted(folder_data.items(), key=lambda item: item[0])
        }
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(sorted_folder_data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _refresh_agent_data_indexes(self) -> None:
        """Refresh agent-data.json for runtime and mirror workspaces."""
        workspace_roots: set[Path] = {self._agent_root.resolve()}
        for mirror_root in self._get_mirror_skills_dirs():
            try:
                # mirror_root = .../agent/skills → agent 根目录为其 parent
                workspace_roots.add(mirror_root.parent.resolve())
            except Exception:
                continue
        for workspace_root in workspace_roots:
            try:
                self._generate_agent_data_for_workspace(workspace_root)
            except Exception as exc:
                logger.warning("重建 agent-data.json 失败: agent_root=%s error=%s", workspace_root, exc)

    @staticmethod
    def _locate_skill_dir(path: Path) -> Path | None:
        """定位包含 SKILL.md 的目录（优先当前目录，再向下递归）；文件名大小写不敏感."""
        if path.is_file() and path.name.lower() == "skill.md":
            return path.parent
        if path.is_dir():
            direct = path / "SKILL.md"
            if direct.is_file():
                return path
            for md in path.rglob("SKILL.md"):
                if md.is_file():
                    return md.parent
            # 兼容小写 skill.md（如 Linux 下仓库命名）
            for md in path.rglob("*.md"):
                if md.is_file() and md.name.lower() == "skill.md":
                    return md.parent
        return None

    @staticmethod
    def _get_github_token() -> str:
        return (os.getenv("GITHUB_TOKEN") or "").strip()

    @staticmethod
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

    @staticmethod
    def _skillnet_evaluate_sync(skill_url: str) -> dict[str, Any]:
        """同步 evaluate，供 asyncio.to_thread 调用."""
        try:
            from skillnet_ai import SkillNetClient
            from skillnet_ai.client import SkillNetError
        except Exception as exc:
            return {
                "ok": False,
                "detail": "未安装 skillnet-ai，请先安装依赖: pip install skillnet-ai",
                "detail_key": "skills.skillNet.errors.skillnetAiMissing",
            }

        llm = SkillManager._skillnet_eval_llm_params()
        if not llm.get("api_key"):
            return {
                "ok": False,
                "detail": "",
                "detail_key": "skills.skillNet.errors.evaluateNoApiKey",
            }

        kwargs: dict[str, Any] = {
            "api_key": llm["api_key"],
            "base_url": llm["base_url"],
            "github_token": SkillManager._get_github_token() or None,
        }
        try:
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

    @staticmethod
    def _skillnet_search_sync(search_kwargs: dict[str, Any]) -> list[Any]:
        """同步调用 skillnet-ai search，供 asyncio.to_thread 使用."""
        try:
            from skillnet_ai import SkillNetClient
        except Exception as exc:
            raise RuntimeError(
                "未安装 skillnet-ai，请先安装依赖: pip install skillnet-ai"
            ) from exc

        client = SkillNetClient(github_token=SkillManager._get_github_token())
        results = client.search(**search_kwargs)
        if results is None:
            return []
        if isinstance(results, list):
            return results
        return list(results)

    @staticmethod
    def _github_skillnet_install_error_context(skill_url: str) -> str:
        """下载失败时拉 GitHub Contents 与 rate_limit，把官方 message 等拼给前端."""
        try:
            from skillnet_ai.downloader import SkillDownloader
        except ImportError:
            return ""

        dl = SkillDownloader(api_token=SkillManager._get_github_token())
        parsed = dl._parse_github_url(skill_url)
        if not parsed:
            return ""

        owner, repo, ref, dir_path, _ = parsed
        api = f"https://api.github.com/repos/{owner}/{repo}/contents/{dir_path}?ref={ref}"
        try:
            r = dl.session.get(api, timeout=_SKILLNET_DOWNLOAD_TIMEOUT)
        except Exception as exc:
            logger.debug(
                "SkillNet 安装错误上下文: GitHub Contents 请求失败: %s", exc
            )
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
                logger.debug(
                    "SkillNet 安装错误上下文: 解析 GitHub 错误 JSON 失败: %s", exc
                )
                raw = (r.text or "").strip()[:500]
                if raw:
                    parts.append(f"HTTP {r.status_code}: {raw}")

            if r.status_code == 403 or any(
                    "rate limit" in p.lower() for p in parts
            ):
                try:
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

    @staticmethod
    def _skillnet_download_sync(
            skill_url: str, target_dir: str, mirror_url: str | None = None
    ) -> str:
        """同步调用 skillnet-ai download；失败时附带 GitHub API 返回说明（如前端的限流文案）。"""
        try:
            from skillnet_ai.downloader import SkillDownloader, GitHubAPIError
        except Exception as exc:
            raise RuntimeError(
                "未安装 skillnet-ai，请先安装依赖: pip install skillnet-ai"
            ) from exc

        token = SkillManager._get_github_token()
        dl_kwargs: dict[str, Any] = {
            "api_token": token,
            "timeout": _SKILLNET_DOWNLOAD_TIMEOUT,
            "max_retries": _SKILLNET_MAX_RETRIES,
        }
        if mirror_url:
            dl_kwargs["mirror_url"] = mirror_url
        downloader = SkillDownloader(**dl_kwargs)

        try:
            local_path = downloader.download(folder_url=skill_url, target_dir=target_dir)
        except GitHubAPIError:
            raise
        except Exception as exc:
            ctx = SkillManager._github_skillnet_install_error_context(skill_url)
            if ctx:
                raise RuntimeError(f"{exc} | {ctx}") from exc
            raise
        if not local_path:
            # skillnet-ai 在多种情况下会无异常地返回 None：URL 无效、目录下列表为空、
            # 或 Contents API 成功但拉 raw 文件全部失败（超时/网络）等，库未区分原因。
            ctx = SkillManager._github_skillnet_install_error_context(skill_url)
            raise SkillNetEmptyDownloadError(github_context=ctx)
        return str(local_path)

    async def _git_clone(self, url: str, dest: Path) -> str | None:
        """浅克隆 git 仓库，返回 commit hash 或 None."""
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "clone", "--depth", "1", url, str(dest),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await proc.communicate()
            if proc.returncode != 0:
                logger.error("git clone 失败: %s", stderr.decode(errors="replace"))
                return None
            return await self._git_get_commit(dest)
        except Exception as exc:
            logger.error("git clone 异常: %s", exc)
            return None

    async def _git_pull(self, repo_path: Path) -> str | None:
        """拉取最新代码，返回 commit hash 或 None."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "-C", str(repo_path), "pull", "--ff-only",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await proc.communicate()
            if proc.returncode != 0:
                logger.warning("git pull 失败: %s", stderr.decode(errors="replace"))
                return None
            return await self._git_get_commit(repo_path)
        except Exception as exc:
            logger.warning("git pull 异常: %s", exc)
            return None

    async def _git_get_commit(self, repo_path: Path) -> str | None:
        """获取当前 HEAD commit hash."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "-C", str(repo_path), "rev-parse", "HEAD",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
            if proc.returncode != 0:
                return None
            return stdout.decode().strip()
        except Exception:
            return None

    async def _sync_marketplace_repos(self) -> None:
        """同步所有已配置 marketplace 到本地目录（存在则 pull，不存在则 clone）."""
        marketplaces = [m for m in self._get_marketplaces() if bool(m.get("enabled", True))]
        if not marketplaces:
            return

        self._marketplace_dir.mkdir(parents=True, exist_ok=True)

        for marketplace in marketplaces:
            name = marketplace.get("name", "")
            url = marketplace.get("url", "")
            if not name or not url:
                continue

            repo_dir = self._marketplace_dir / name
            try:
                if repo_dir.exists():
                    await self._git_pull(repo_dir)
                else:
                    await self._git_clone(url, repo_dir)
            except Exception as exc:
                logger.warning(
                    "同步 marketplace 失败: name=%s url=%s error=%s",
                    name,
                    url,
                    exc,
                )

    # -----------------------------------------------------------------------
    # 状态持久化
    # -----------------------------------------------------------------------

    def _load_state(self) -> dict[str, Any]:
        """加载 skills_state.json，失败时返回默认空状态."""
        try:
            if self._state_file.exists():
                state = json.loads(self._state_file.read_text(encoding="utf-8"))
                self._normalize_state(state)
                return state
        except Exception:
            logger.warning("加载 skills_state.json 失败，使用默认空状态")
        default_state = {"marketplaces": [], "installed_plugins": [], "local_skills": []}
        self._normalize_state(default_state)
        return default_state

    def _save_state(self) -> None:
        """持久化状态到 skills_state.json."""
        try:
            self._state_file.parent.mkdir(parents=True, exist_ok=True)
            self._state_file.write_text(
                json.dumps(self._state, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            logger.error("保存 skills_state.json 失败")

    def _get_marketplaces(self) -> list[dict]:
        marketplaces = self._state.get("marketplaces", [])
        normalized = self._normalize_marketplaces(marketplaces)
        # 仅当结构发生变化时写回，避免每次读取都触盘。
        if normalized != marketplaces:
            self._state["marketplaces"] = normalized
            self._save_state()
        return normalized

    def _add_marketplace(self, marketplace: dict) -> None:
        self._state.setdefault("marketplaces", []).append(marketplace)
        self._state["marketplaces"] = self._normalize_marketplaces(
            self._state.get("marketplaces", [])
        )
        self._save_state()

    def _remove_marketplace(self, name: str) -> bool:
        marketplaces = self._state.get("marketplaces", [])
        kept = [m for m in marketplaces if m.get("name") != name]
        if len(kept) == len(marketplaces):
            return False
        self._state["marketplaces"] = self._normalize_marketplaces(kept)
        self._save_state()
        return True

    def _set_marketplace_enabled(self, name: str, enabled: bool) -> bool:
        marketplaces = self._normalize_marketplaces(self._state.get("marketplaces", []))
        updated = False
        for marketplace in marketplaces:
            if marketplace.get("name") == name:
                marketplace["enabled"] = bool(enabled)
                updated = True
                break
        if updated:
            self._state["marketplaces"] = marketplaces
            self._save_state()
        return updated

    def _set_marketplace_last_updated(self, name: str) -> bool:
        from datetime import datetime, timezone

        marketplaces = self._normalize_marketplaces(self._state.get("marketplaces", []))
        updated = False
        for marketplace in marketplaces:
            if marketplace.get("name") == name:
                marketplace["last_updated"] = datetime.now(timezone.utc).isoformat()
                updated = True
                break
        if updated:
            self._state["marketplaces"] = marketplaces
            self._save_state()
        return updated

    @staticmethod
    def _normalize_marketplaces(raw_marketplaces: Any) -> list[dict]:
        normalized: list[dict] = []
        if not isinstance(raw_marketplaces, list):
            return normalized
        for item in raw_marketplaces:
            if not isinstance(item, dict):
                continue
            name = item.get("name", "")
            url = item.get("url", "")
            if not name or not url:
                continue
            normalized.append({
                **item,
                "name": name,
                "url": url,
                "enabled": bool(item.get("enabled", True)),
            })
        return normalized

    def _normalize_state(self, state: dict[str, Any]) -> None:
        state.setdefault("marketplaces", [])
        state.setdefault("installed_plugins", [])
        state.setdefault("local_skills", [])
        state["marketplaces"] = self._normalize_marketplaces(state.get("marketplaces"))

    def _get_installed_plugins(self) -> list[dict]:
        return self._state.get("installed_plugins", [])

    def _add_installed_plugin(self, plugin: dict) -> None:
        plugins = self._state.setdefault("installed_plugins", [])
        # 更新已有记录
        for i, p in enumerate(plugins):
            if p.get("name") == plugin.get("name"):
                plugins[i] = plugin
                self._save_state()
                return
        plugins.append(plugin)
        self._save_state()

    def _remove_installed_plugin(self, name: str) -> None:
        plugins = self._state.get("installed_plugins", [])
        self._state["installed_plugins"] = [p for p in plugins if p.get("name") != name]
        self._save_state()

    def _add_local_skill(self, skill: dict) -> None:
        local = self._state.setdefault("local_skills", [])
        # 更新已有记录
        for i, s in enumerate(local):
            if s.get("name") == skill.get("name"):
                local[i] = skill
                self._save_state()
                return
        local.append(skill)
        self._save_state()

    def _remove_local_skill(self, name: str) -> None:
        local = self._state.get("local_skills", [])
        self._state["local_skills"] = [s for s in local if s.get("name") != name]
        self._save_state()

    # -----------------------------------------------------------------------
    # ClawHub 相关方法
    # -----------------------------------------------------------------------

    def _get_clawhub_token(self) -> str:
        """获取 ClawHub CLI token."""
        return (self._state.get("clawhub", {}).get("token") or "").strip()

    def _set_clawhub_token(self, token: str) -> None:
        """设置 ClawHub CLI token（掩码处理）。"""
        self._state.setdefault("clawhub", {})["token"] = token.strip() if token.strip() else ""
        self._save_state()

    @staticmethod
    def _mask_clawhub_token(token: str) -> str:
        """掩码处理 ClawHub token。"""
        if not token:
            return ""
        if len(token) <= 8:
            return "*" * len(token)
        return token[:4] + "*" * (len(token) - 8) + token[-4:]
