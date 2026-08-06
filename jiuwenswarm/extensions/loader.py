from __future__ import annotations

import hashlib
import importlib
import importlib.util
import inspect
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openjiuwen.agent_teams.harness.manifest import (
    ElementKind,
    get_catalog,
    register_from_catalog,
    resolve_factory,
)

from jiuwenswarm.common.utils import logger
from jiuwenswarm.extensions.registry import ExtensionRegistry

MANIFEST_FILENAME = "extension.yaml"
ENTRY_FILENAME = "extension.py"


@dataclass(frozen=True)
class _DeclarativeExtensionMetadata:
    id: str
    name: str
    version: str


@dataclass(frozen=True)
class _DeclarativeExtensionHandle:
    """Lifecycle/status handle for an extension that only declares providers."""

    metadata: _DeclarativeExtensionMetadata
    root: Path


def _find_manifest(root: Path) -> Path | None:
    p = root / MANIFEST_FILENAME
    return p if p.exists() else None


def _find_entry_script(root: Path) -> Path | None:
    p = root / ENTRY_FILENAME
    return p if p.exists() else None


def _is_extension_root(path: Path) -> bool:
    return _find_manifest(path) is not None or _find_entry_script(path) is not None


def is_extension_required(root: Path) -> bool:
    """Return whether an extension declares fail-fast loading semantics."""
    required = _load_manifest_dict(root).get("required", False)
    if not isinstance(required, bool):
        raise TypeError("extension manifest 'required' must be true or false")
    return required


def _extension_display_name(manifest: dict, root: Path) -> str:
    name = str(manifest.get("name", "")).strip()
    return name or root.name


def _extension_module_name(root: Path) -> str:
    """Return a stable, collision-resistant import name for an extension root."""
    safe_name = re.sub(r"[^0-9A-Za-z_]", "_", root.name).strip("_")
    safe_name = safe_name or "extension"
    path_digest = hashlib.sha256(str(root.resolve()).encode("utf-8")).hexdigest()[:12]
    return f"jiuwenswarm.loaded_extension.ext_{safe_name}_{path_digest}"


def _module_loaded_from_root(module: Any, root: Path) -> bool:
    """Return whether a module's source file lives inside an extension root."""
    source = getattr(module, "__file__", None)
    if not source:
        return False
    try:
        return Path(source).resolve().is_relative_to(root.resolve())
    except (OSError, RuntimeError, TypeError, ValueError):
        return False


def _validate_new_harness_descriptors(
    catalog: dict[str, Any],
    names: set[str],
) -> None:
    """Resolve all new descriptor factories before provider registries mutate."""
    for name, descriptor in catalog.items():
        if name not in names:
            continue
        target = resolve_factory(descriptor.factory_ref)
        if inspect.isclass(target) and descriptor.kind in {
            ElementKind.TOOL,
            ElementKind.RAIL,
        }:
            # register_from_catalog() adapts class providers by inspecting their
            # constructors. Preflight the same potentially-failing operation so
            # registration itself becomes a sequence of name-keyed assignments.
            inspect.signature(target.__init__)


class ExtensionLoader:
    def __init__(self, registry: ExtensionRegistry):
        self.registry = registry
        self._search_paths: list[Path] = []

    def add_search_path(self, path: Path) -> None:
        if path.exists():
            self._search_paths.append(path)

    def discover_extension_roots(self) -> list[Path]:
        roots: list[Path] = []
        seen: set[str] = set()

        def _append_root(root: Path) -> None:
            key = str(root.resolve()).lower()
            if key not in seen:
                seen.add(key)
                roots.append(root)

        logger.info("[ExtensionLoader] 开始搜索扩展路径: %s", self._search_paths)
        for base_path in self._search_paths:
            if not base_path.exists():
                continue
            if _is_extension_root(base_path):
                _append_root(base_path)
                continue
            for subdir in sorted(
                base_path.iterdir(), key=lambda item: item.name.lower()
            ):
                if not subdir.is_dir():
                    continue
                if _is_extension_root(subdir):
                    _append_root(subdir)
        return roots

    async def load_extension(self, root: Path) -> Any:
        manifest = _load_manifest_dict(root)

        await self._install_dependencies(manifest, root)

        catalog = get_catalog()
        catalog_before = dict(catalog)
        contributor_snapshot = self.registry.snapshot_harness_contributors()
        module_names_before = set(sys.modules)
        qualified_name = _extension_module_name(root)
        previous_module = sys.modules.get(qualified_name)
        try:
            module = self._import_module(root)

            if hasattr(module, "register_extensions"):
                registered = await module.register_extensions(self.registry)
            else:
                registered = None

            if registered:
                items = registered if isinstance(registered, list) else [registered]
                for ext in items:
                    if hasattr(ext, "set_extension_dir"):
                        ext.set_extension_dir(root)

            # Validate and publish this extension's manifest declarations while
            # the catalog/module/contributor changes are still transactional.
            # A bad factory_ref is therefore isolated to this extension instead
            # of leaving the process partially registered but apparently ready.
            new_catalog_names = set(catalog) - set(catalog_before)
            _validate_new_harness_descriptors(catalog, new_catalog_names)
            register_from_catalog()
        except Exception:
            # Import-time decorators mutate Agent Core's process-global catalog.
            # Roll back declarations and contributor registrations made by a
            # half-loaded extension so one bad package cannot poison every later
            # register_from_catalog() refresh.
            catalog.clear()
            catalog.update(catalog_before)
            self.registry.restore_harness_contributors(contributor_snapshot)
            for module_name in set(sys.modules) - module_names_before:
                module = sys.modules.get(module_name)
                if module_name == qualified_name or _module_loaded_from_root(
                    module, root
                ):
                    sys.modules.pop(module_name, None)
            if previous_module is None:
                sys.modules.pop(qualified_name, None)
            else:
                sys.modules[qualified_name] = previous_module
            raise

        if registered:
            return registered
        extension_name = _extension_display_name(manifest, root)
        extension_id = str(manifest.get("id", "")).strip() or extension_name
        extension_version = str(manifest.get("version", "")).strip() or "unknown"
        return _DeclarativeExtensionHandle(
            metadata=_DeclarativeExtensionMetadata(
                id=extension_id,
                name=extension_name,
                version=extension_version,
            ),
            root=root,
        )

    async def _install_dependencies(self, manifest: dict, root: Path) -> None:
        """安装扩展声明的依赖"""
        dependencies = manifest.get("dependencies", {})
        if not dependencies:
            return
        extension_name = _extension_display_name(manifest, root)

        import shutil
        import subprocess
        uv_path = shutil.which("uv")
        use_uv = uv_path is not None

        for package, version_spec in dependencies.items():
            package_name = f"{package}{version_spec}" if version_spec else package
            try:
                importlib.metadata.version(package)
                logger.info(
                    f"[ExtensionLoader] 扩展 {extension_name} 依赖 {package} 已安装"
                )
                continue
            except importlib.metadata.PackageNotFoundError:
                pass

            logger.info(
                f"[ExtensionLoader] 正在安装扩展 {extension_name} 的依赖: {package_name}"
            )
            try:
                if use_uv:
                    subprocess.check_call(
                        [uv_path, "pip", "install", package_name],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.PIPE,
                        timeout=120,
                    )
                else:
                    subprocess.check_call(
                        [sys.executable, "-m", "pip", "install", package_name],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.PIPE,
                        timeout=120,
                    )
                logger.info(
                    f"[ExtensionLoader] 扩展 {extension_name} 依赖 {package} 安装成功"
                )
            except subprocess.TimeoutExpired:
                logger.error(
                    f"[ExtensionLoader] 扩展 {extension_name} 依赖 {package} 安装超时 (120秒)"
                )
            except subprocess.CalledProcessError as e:
                logger.error(
                    f"[ExtensionLoader] 扩展 {extension_name} 依赖 {package} 安装失败: {e}"
                )

    @staticmethod
    def _import_module(root: Path) -> Any:
        entry = _find_entry_script(root)
        if entry is None:
            raise FileNotFoundError(
                f"扩展入口脚本不存在（期望 {ENTRY_FILENAME}）: {root}"
            )

        module_name = root.name
        qualified_name = _extension_module_name(root)
        spec = importlib.util.spec_from_file_location(
            qualified_name,
            entry,
        )
        if spec is None or spec.loader is None:
            raise ImportError(f"无法加载扩展: {module_name}")

        module = importlib.util.module_from_spec(spec)
        previous = sys.modules.get(qualified_name)
        sys.modules[qualified_name] = module
        try:
            spec.loader.exec_module(module)
        except Exception:
            if previous is None:
                sys.modules.pop(qualified_name, None)
            else:
                sys.modules[qualified_name] = previous
            raise
        return module


def _load_manifest_dict(root: Path) -> dict:
    manifest_path = _find_manifest(root)
    if manifest_path is None:
        return {}
    try:
        import yaml

        return yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    except ImportError:
        return {}
