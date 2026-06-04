from __future__ import annotations

import os
import re
import signal
import sys
import threading
import time

from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import urljoin

from jiuwenswarm.common.config import get_config_raw
from jiuwenswarm.common.version import __version__
from jiuwenswarm.common.upgrade_executor import create_executor
from jiuwenswarm.common.version_source import (
    GitHubReleasesSource,
    GitCodeReleasesSource,
    PyPIVersionSource,
    ReleaseInfo,
    is_prerelease_version,
    strip_prerelease_suffix,
)

DEFAULT_RELEASE_API_GITCODE = "https://api.gitcode.com/api/v5/repos/{owner}/{repo}/releases/latest"
DEFAULT_RELEASE_API_GITHUB = "https://api.github.com/repos/{owner}/{repo}/releases/latest"
DEFAULT_RELEASE_API_PYPI = "https://pypi.org/simple/{package}/"
DEFAULT_ASSET_PATTERN_WINDOWS = "JiuwenSwarm-setup-{version}.exe"
DEFAULT_ASSET_PATTERN_MACOS = "JiuwenSwarm-{version}.dmg"
DEFAULT_ASSET_PATTERN_LINUX = "JiuwenSwarm-{version}.tar.gz"
DEFAULT_TIMEOUT_SECONDS = 20
DEFAULT_TEXT = "HzUzzbjzJNsWmfsdiy2GKcEg"


def get_access_token() -> str:
    return os.getenv("GITCODE_TOKEN", "").strip() or DEFAULT_TEXT


def _normalize_version(raw: str) -> str:
    return (raw or "").strip().lstrip("vV")


def _version_key(version: str) -> tuple[int, ...]:
    numbers = re.findall(r"\d+", _normalize_version(version))
    return tuple(int(part) for part in numbers) or (0,)


def _base_version_key(version: str) -> tuple[int, ...]:
    """Numeric key for the *base* version with pre-release suffix removed.

    ``0.2.0.beta1`` and ``0.2.0`` both yield ``(0, 2, 0)``.
    """
    return _version_key(strip_prerelease_suffix(version))


def _is_newer_version(candidate: str, current: str) -> bool:
    """Return True when *candidate* is a newer release than *current*.

    Pre-release rules:
    - A stable release is always newer than a pre-release with the same base version.
      e.g. 0.2.0 > 0.2.0.beta1
    - A pre-release is *never* considered newer than a stable release with the same
      base version.  e.g. 0.2.0.rc1 is NOT newer than 0.2.0
    - Between two pre-releases (or two stables) the numeric components decide.
    """
    candidate_base = _base_version_key(candidate)
    current_base = _base_version_key(current)
    max_len = max(len(candidate_base), len(current_base))
    candidate_padded = candidate_base + (0,) * (max_len - len(candidate_base))
    current_padded = current_base + (0,) * (max_len - len(current_base))

    if candidate_padded > current_padded:
        return True
    if candidate_padded < current_padded:
        return False

    # Same base — pre-release vs stable decides
    current_is_pre = is_prerelease_version(current)
    candidate_is_pre = is_prerelease_version(candidate)
    if current_is_pre and not candidate_is_pre:
        return True   # stable > pre-release
    if candidate_is_pre and not current_is_pre:
        return False  # pre-release < stable

    # Both pre-release (e.g. 0.2.0.beta2 vs 0.2.0.beta1) — full segments decide
    if candidate_is_pre and current_is_pre:
        candidate_full = _version_key(candidate)
        current_full = _version_key(current)
        max_full = max(len(candidate_full), len(current_full))
        return (candidate_full + (0,) * (max_full - len(candidate_full))
                > current_full + (0,) * (max_full - len(current_full)))

    return False


def _detect_install_mode() -> str:
    return "desktop" if getattr(sys, "frozen", False) else "pip"


def _platform_asset_key() -> str:
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


@dataclass
class UpdateStatus:
    current_version: str
    latest_version: str = ""
    state: str = "idle"
    has_update: bool = False
    install_mode: str = ""
    release_notes: str = ""
    published_at: str = ""
    source_type: str = ""
    asset_name: str = ""
    matched_asset: str = ""
    download_url: str = ""
    downloaded_path: str = ""
    downloaded_bytes: int = 0
    total_bytes: int = 0
    error: str = ""
    checked_at: float = 0.0
    installing: bool = False
    restart_command: str = ""


class UpdaterService:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._download_thread: threading.Thread | None = None
        self._status = UpdateStatus(
            current_version=__version__,
            install_mode=_detect_install_mode(),
        )

    def get_status(self) -> dict[str, Any]:
        with self._lock:
            status = asdict(self._status)
        status["platform"] = sys.platform
        status["platform_supported"] = True
        return status

    def get_runtime_config(self) -> dict[str, Any]:
        config = self._load_config()
        return {
            "enabled": config["enabled"],
            "desktop_release_api_type": config["desktop_release_api_type"],
            "release_api_type": config["release_api_type"],
            "install_mode": config["install_mode"],
            "repo_owner": config["repo_owner"],
            "repo_name": config["repo_name"],
            "release_api_url": config["release_api_url"],
            "asset_name_pattern": config["asset_name_pattern_windows"],
            "asset_name_pattern_windows": config["asset_name_pattern_windows"],
            "asset_name_pattern_macos": config["asset_name_pattern_macos"],
            "asset_name_pattern_linux": config["asset_name_pattern_linux"],
            "timeout_seconds": config["timeout_seconds"],
            "pypi_mirror": config["pypi_mirror"],
            "access_token": self._mask_token(config["access_token"]),
        }

    @staticmethod
    def _mask_token(token: str) -> str:
        if len(token) <= 8:
            return token[:2] + "****" + token[-2:] if len(token) > 4 else "****"
        return token[:4] + "****" + token[-4:]

    def check(self, manual: bool = False) -> dict[str, Any]:
        config = self._load_config()
        if not config["enabled"]:
            self._update_status(state="disabled", error="Updater is disabled.")
            return self.get_status()

        self._update_status(state="checking", error="")
        try:
            self._check(config)
        except Exception as exc:
            self._update_status(
                latest_version="",
                has_update=False,
                release_notes="",
                published_at="",
                source_type="",
                asset_name="",
                matched_asset="",
                download_url="",
                state="error",
                error=f"Update check failed: {exc}",
                checked_at=time.time(),
            )
        return self.get_status()

    def start_download(self) -> dict[str, Any]:
        status = self.get_status()
        install_mode = status.get("install_mode", "desktop")

        if status["state"] in ("downloading", "upgrading"):
            return status

        config = self._load_config()
        executor = create_executor(
            install_mode,
            {**config, **status},
            self._executor_callback,
        )

        pip_state = "upgrading" if install_mode == "pip" else "downloading"
        self._update_status(
            state=pip_state,
            error="",
            downloaded_bytes=0,
            total_bytes=0,
            installing=False,
        )

        thread = threading.Thread(
            target=executor.install,
            daemon=True,
            name="JiuwenSwarm-Updater-download",
        )
        self._download_thread = thread
        thread.start()
        return self.get_status()

    def mark_installing(self, installer_path: str) -> dict[str, Any]:
        self._update_status(
            state="installing",
            installing=True,
            downloaded_path=installer_path,
            error="",
        )
        return self.get_status()

    def start_upgrade(self) -> dict[str, Any]:
        status = self.get_status()
        install_mode = status.get("install_mode", "desktop")
        config = self._load_config()
        executor = create_executor(
            install_mode,
            {**config, **status},
            self._executor_callback,
        )

        pip_state = "restarting" if install_mode == "pip" else "installing"
        self._update_status(
            state=pip_state,
            installing=True,
            error="",
        )

        try:
            executor.upgrade()
        except Exception as exc:
            self._update_status(
                state="error",
                error=f"Upgrade failed: {exc}",
            )
            return self.get_status()

        if install_mode == "pip":
            threading.Timer(3.0, os.kill, args=[os.getpid(), signal.SIGTERM]).start()

        return self.get_status()

    def _executor_callback(self, updates: dict[str, Any]) -> None:
        self._update_status(**updates)

    @staticmethod
    def _create_version_source(config: dict[str, Any]) -> Any:
        api_type = config["release_api_type"]
        timeout = config["timeout_seconds"]
        api_url = config["release_api_url"]

        creators = {
            "github": lambda: GitHubReleasesSource(
                owner=config["repo_owner"],
                repo=config["repo_name"],
                token=os.getenv("GITHUB_TOKEN", ""),
                api_url=api_url,
                timeout_seconds=timeout,
            ),
            "gitcode": lambda: GitCodeReleasesSource(
                owner=config["repo_owner"],
                repo=config["repo_name"],
                access_token=config["access_token"],
                api_url=api_url,
                timeout_seconds=timeout,
            ),
            "pypi": lambda: PyPIVersionSource(
                package=config["repo_name"],
                mirror=config["pypi_mirror"],
                timeout_seconds=timeout,
            ),
        }

        creator = creators.get(api_type)
        if creator is None:
            raise ValueError(f"Unsupported release_api_type: {api_type}")
        return creator()

    def _check(self, config: dict[str, Any]) -> None:
        source = self._create_version_source(config)
        try:
            release = source.fetch_latest()
        except Exception as exc:
            raise RuntimeError(
                f"Failed to fetch latest release from {config['release_api_type']}: {exc}"
            ) from exc

        latest_version = release.version
        if not latest_version:
            raise RuntimeError("Latest release version is missing.")

        install_mode = _detect_install_mode()
        has_update = _is_newer_version(latest_version, __version__)

        if not has_update:
            self._update_status(
                latest_version=latest_version,
                has_update=False,
                install_mode=install_mode,
                release_notes=release.release_notes,
                published_at=release.published_at,
                source_type=release.source_type,
                matched_asset="",
                checked_at=time.time(),
                state="up_to_date",
                error="",
                installing=False,
            )
            return

        if install_mode == "desktop":
            self._resolve_desktop_asset(config, release)
        else:
            self._resolve_pip_asset(config, release)

    def _resolve_desktop_asset(self, config: dict[str, Any], release: ReleaseInfo) -> None:
        platform_key = _platform_asset_key()
        pattern_key = f"asset_name_pattern_{platform_key}"
        asset_name_pattern = config.get(pattern_key) or config.get("asset_name_pattern_windows",
            DEFAULT_ASSET_PATTERN_WINDOWS)
        asset_name = asset_name_pattern.format(version=release.version)

        matched = next((a for a in release.assets if a.name == asset_name), None)
        if not matched:
            raise RuntimeError(f"Desktop installer not found: {asset_name}")

        self._update_status(
            latest_version=release.version,
            has_update=True,
            install_mode="desktop",
            release_notes=release.release_notes,
            published_at=release.published_at,
            source_type=release.source_type,
            asset_name=asset_name,
            matched_asset=asset_name,
            download_url=matched.download_url,
            checked_at=time.time(),
            state="update_available",
            error="",
            installing=False,
        )

    def _resolve_pip_asset(self, config: dict[str, Any], release: ReleaseInfo) -> None:
        whl = next((a for a in release.assets if a.name.endswith(".whl")), None)
        if not whl:
            raise RuntimeError(
                "No .whl package found in the release assets. "
                "For pip installations the release must include a .whl file."
            )

        self._update_status(
            latest_version=release.version,
            has_update=True,
            install_mode="pip",
            release_notes=release.release_notes,
            published_at=release.published_at,
            source_type=release.source_type,
            asset_name=whl.name,
            matched_asset=whl.name,
            download_url=whl.download_url,
            checked_at=time.time(),
            state="update_available",
            error="",
            installing=False,
        )

    @staticmethod
    def _load_config() -> dict[str, Any]:
        raw = get_config_raw() or {}
        updater = raw.get("updater") or {}

        api_type = str(updater.get("desktop_release_api_type") or "gitcode").strip().lower()
        desktop_api_type = api_type
        if _detect_install_mode() != "desktop":
            api_type = "pypi"
        owner = str(updater.get("repo_owner") or "openJiuwen").strip()
        repo = str(updater.get("repo_name") or "jiuwenswarm").strip()
        release_api_url = str(updater.get("release_api_url") or "").strip()
        if not release_api_url:
            if api_type == "github":
                release_api_url = DEFAULT_RELEASE_API_GITHUB.format(owner=owner, repo=repo)
            elif api_type == "pypi":
                pypi_mirror = str(updater.get("pypi_mirror") or "").strip()
                if pypi_mirror:
                    release_api_url = urljoin(pypi_mirror, f"simple/{repo}/")
                else:
                    release_api_url = DEFAULT_RELEASE_API_PYPI.format(package=repo)
            else:
                release_api_url = DEFAULT_RELEASE_API_GITCODE.format(owner=owner, repo=repo)
        timeout_seconds = updater.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)
        try:
            timeout_seconds = max(5, int(timeout_seconds))
        except (TypeError, ValueError):
            timeout_seconds = DEFAULT_TIMEOUT_SECONDS

        return {
            "enabled": bool(updater.get("enabled", True)),
            "desktop_release_api_type": desktop_api_type,
            "release_api_type": api_type,
            "install_mode": _detect_install_mode(),
            "repo_owner": owner,
            "repo_name": repo,
            "release_api_url": release_api_url,
            "asset_name_pattern_windows": str(
                updater.get("asset_name_pattern")
                or updater.get("asset_name_pattern_windows")
                or DEFAULT_ASSET_PATTERN_WINDOWS
            ),
            "asset_name_pattern_macos": str(
                updater.get("asset_name_pattern_macos") or DEFAULT_ASSET_PATTERN_MACOS
            ),
            "asset_name_pattern_linux": str(
                updater.get("asset_name_pattern_linux") or DEFAULT_ASSET_PATTERN_LINUX
            ),
            "timeout_seconds": timeout_seconds,
            "access_token": get_access_token(),
            "pypi_mirror": str(updater.get("pypi_mirror") or "").strip(),
        }

    def _update_status(self, **updates: Any) -> None:
        with self._lock:
            for key, value in updates.items():
                setattr(self._status, key, value)