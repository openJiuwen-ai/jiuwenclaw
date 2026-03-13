# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""SkillManager - 管理 skills 的加载、安装、卸载与 marketplace 操作."""

from __future__ import annotations

import asyncio
import json
import re
import shutil
from pathlib import Path
from typing import Any

from jiuwenclaw.utils import get_workspace_dir, logger

# ---------------------------------------------------------------------------
# 默认路径
# ---------------------------------------------------------------------------
_WORKSPACE = get_workspace_dir()
_SKILLS_DIR = _WORKSPACE / "agent" / "skills"
_MARKETPLACE_DIR = _SKILLS_DIR / "_marketplace"
_STATE_FILE = _WORKSPACE / "skills_state.json"


class SkillManager:
    """Skill 管理器，对应 skills.* 请求方法."""

    def __init__(self) -> None:
        _SKILLS_DIR.mkdir(parents=True, exist_ok=True)
        self._state: dict[str, Any] = self._load_state()

    # -----------------------------------------------------------------------
    # 公开 handler
    # -----------------------------------------------------------------------

    async def handle_skills_list(self, params: dict) -> dict:
        """返回所有可用 skill（本地 + marketplace 中未安装的）.

        params:
            refresh_marketplaces: bool (可选, 默认 False)
                为 True 时，先对已配置 marketplace 执行 clone/pull，再扫描列表。
        """
        refresh_marketplaces = bool(params.get("refresh_marketplaces", False))
        if refresh_marketplaces:
            await self._sync_marketplace_repos()
        local = self._scan_local_skills()
        marketplace = self._scan_marketplace_skills()
        return {"skills": local + marketplace}

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
        for child in _SKILLS_DIR.iterdir():
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
                return meta

        # 再在 marketplace 目录中查找
        if _MARKETPLACE_DIR.exists():
            for repo_dir in _MARKETPLACE_DIR.iterdir():
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
                        return meta

        raise ValueError(f"未找到 skill: {name}")

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
        repo_dir = _MARKETPLACE_DIR / marketplace_name
        if repo_dir.exists():
            await self._git_pull(repo_dir)
        else:
            commit = await self._git_clone(git_url, repo_dir)
            if commit is None:
                return {"success": False, "detail": f"git clone 失败: {git_url}"}

        # 在仓库中查找 plugin 目录
        plugin_src = repo_dir / "skills" / plugin_name
        if not plugin_src.is_dir():
            return {"success": False, "detail": f"在 marketplace 仓库中未找到 plugin: {plugin_name}"}

        md = self._try_find_skill_file(plugin_src)
        if md is None:
            return {"success": False, "detail": f"plugin {plugin_name} 缺少 SKILL.md"}

        # 复制到本地 skills 目录
        dest = _SKILLS_DIR / plugin_name
        if dest.exists():
            if not force:
                return {"success": False, "detail": f"skill {plugin_name} 已存在，使用 force=true 覆盖"}
            shutil.rmtree(dest)
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

        return {"success": True}

    async def handle_skills_uninstall(self, params: dict) -> dict:
        """卸载已安装的 skill.

        params:
            name: skill 名称
        """
        name = params.get("name", "")
        if not name:
            return {"success": False, "detail": "缺少参数: name"}

        dest = _SKILLS_DIR / name
        if dest.exists() and dest.is_dir():
            shutil.rmtree(dest)

        self._remove_installed_plugin(name)
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
            dest = _SKILLS_DIR / skill_name
            if dest.exists():
                if not force:
                    return {"success": False, "detail": f"skill {skill_name} 已存在，使用 force=true 覆盖"}
                shutil.rmtree(dest)
            dest.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest / src.name)
        elif src.is_dir():
            md = self._try_find_skill_file(src)
            if md is None:
                return {"success": False, "detail": f"目录中未找到 SKILL.md: {raw_path}"}
            meta = self._parse_skill_md(md) or {}
            skill_name = meta.get("name", src.name)
            dest = _SKILLS_DIR / skill_name
            if dest.exists():
                if not force:
                    return {"success": False, "detail": f"skill {skill_name} 已存在，使用 force=true 覆盖"}
                shutil.rmtree(dest)
            shutil.copytree(src, dest)
        else:
            return {"success": False, "detail": f"不支持的路径类型: {raw_path}"}

        self._add_local_skill({"name": skill_name, "origin": raw_path, "source": "local"})
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
            repo_dir = _MARKETPLACE_DIR / name
            if repo_dir.exists() and repo_dir.is_dir():
                try:
                    shutil.rmtree(repo_dir)
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
            repo_dir = _MARKETPLACE_DIR / name
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
        repo_dir = _MARKETPLACE_DIR / name
        cache_removed = False
        if repo_dir.exists() and repo_dir.is_dir():
            try:
                shutil.rmtree(repo_dir)
                cache_removed = True
            except Exception as exc:
                logger.warning("禁用 marketplace 时删除缓存失败: %s", exc)
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
        meta.setdefault("tags", [])
        meta.setdefault("allowed_tools", [])

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
    # 目录扫描
    # -----------------------------------------------------------------------

    def _scan_local_skills(self) -> list[dict]:
        """扫描 workspace/agent/skills/ 下的本地 skill（跳过 _marketplace）."""
        results: list[dict] = []
        if not _SKILLS_DIR.exists():
            return results

        for child in _SKILLS_DIR.iterdir():
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
            # 检查是否通过 import_local 导入
            for ls in self._state.get("local_skills", []):
                if ls.get("name") == meta.get("name"):
                    source = "local"
                    break

            meta["source"] = source
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

    def _scan_marketplace_skills(self) -> list[dict]:
        """扫描 _marketplace/ 下已 clone 的仓库中未安装的 skill.

        扫描路径：_marketplace/{marketplace_name}/skills/{plugin_name}
        """
        results: list[dict] = []
        if not _MARKETPLACE_DIR.exists():
            return results

        installed_names = {p.get("name") for p in self._get_installed_plugins()}

        enabled_marketplaces = {
            m.get("name")
            for m in self._get_marketplaces()
            if bool(m.get("enabled", True)) and m.get("name")
        }

        for repo_dir in _MARKETPLACE_DIR.iterdir():
            if not repo_dir.is_dir():
                continue
            marketplace_name = repo_dir.name
            if marketplace_name not in enabled_marketplaces:
                continue

            # 检查 skills 子目录是否存在
            skills_dir = repo_dir / "skills"
            if not skills_dir.exists() or not skills_dir.is_dir():
                # 如果没有 skills 子目录，尝试直接扫描 repo_dir（兼容旧结构）
                skills_dir = repo_dir

            for plugin_dir in skills_dir.iterdir():
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
                meta.pop("body", None)
                results.append(meta)

        return results

    # -----------------------------------------------------------------------
    # Git 操作
    # -----------------------------------------------------------------------

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

        _MARKETPLACE_DIR.mkdir(parents=True, exist_ok=True)

        for marketplace in marketplaces:
            name = marketplace.get("name", "")
            url = marketplace.get("url", "")
            if not name or not url:
                continue

            repo_dir = _MARKETPLACE_DIR / name
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
            if _STATE_FILE.exists():
                state = json.loads(_STATE_FILE.read_text(encoding="utf-8"))
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
            _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            _STATE_FILE.write_text(
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
