from __future__ import annotations

import hashlib
import os
import re
import sys
import threading
import time

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from jiuwenswarm.common.config import get_config_raw
from jiuwenswarm.common.utils import get_user_workspace_dir
from jiuwenswarm.common.version import __version__
from jiuwenswarm.common.version_source import (
    GitHubReleasesSource,
    GitCodeReleasesSource,
    PyPIVersionSource,
    ReleaseInfo,
)

DEFAULT_TEXT = "HzUzzbjzJNsWmfsdiy2GKcEg"
DEFAULT_RELEASE_API_GITCODE = "https://api.gitcode.com/api/v5/repos/{owner}/{repo}/releases/latest"
DEFAULT_RELEASE_API_GITHUB = "https://api.github.com/repos/{owner}/{repo}/releases/latest"
DEFAULT_RELEASE_API_PYPI = "https://pypi.org/simple/{package}/"
DEFAULT_ASSET_PATTERN_WINDOWS = "JiuwenSwarm-setup-{version}.exe"
DEFAULT_ASSET_PATTERN_MACOS = "JiuwenSwarm-{version}.dmg"
DEFAULT_ASSET_PATTERN_LINUX = "JiuwenSwarm-{version}.tar.gz"
DEFAULT_SHA256_PATTERN = "JiuwenSwarm-setup-{version}.exe.sha256"
DEFAULT_TIMEOUT_SECONDS = 20
DOWNLOAD_CHUNK_SIZE = 1024 * 512


def _updates_dir() -> Path:
    path = get_user_workspace_dir() / ".updates"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _normalize_version(raw: str) -> str:
    return (raw or "").strip().lstrip("vV")


def _version_key(version: str) -> tuple[int, ...]:
    numbers = re.findall(r"\d+", _normalize_version(version))
    return tuple(int(part) for part in numbers) or (0,)


def _is_newer_version(candidate: str, current: str) -> bool:
    candidate_key = _version_key(candidate)
    current_key = _version_key(current)
    max_len = max(len(candidate_key), len(current_key))
    candidate_padded = candidate_key + (0,) * (max_len - len(candidate_key))
    current_padded = current_key + (0,) * (max_len - len(current_key))
    return candidate_padded > current_padded


def _parse_sha256(raw: str) -> str:
    token = (raw or "").strip().split()
    if not token:
        return ""
    digest = token[0].strip().lower()
    if re.fullmatch(r"[0-9a-f]{64}", digest):
        return digest
    return ""


def _detect_install_mode() -> str:
    return "desktop" if getattr(sys, "frozen", False) else "pip"


def _platform_asset_key() -> str:
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().lower()


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
    sha256_url: str = ""
    downloaded_path: str = ""
    downloaded_bytes: int = 0
    total_bytes: int = 0
    error: str = ""
    checked_at: float = 0.0
    installing: bool = False


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
            "sha256_name_pattern": config["sha256_name_pattern"],
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
                sha256_url="",
                state="error",
                error=f"Update check failed: {exc}",
                checked_at=time.time(),
            )
        return self.get_status()

    def start_download(self) -> dict[str, Any]:
        status = self.get_status()
        if not status.get("download_url"):
            self._update_status(
                state="error",
                error="No download URL available. Check for updates first.",
            )
            return self.get_status()

        if status["state"] == "downloading":
            return status

        self._update_status(
            state="downloading",
            error="",
            downloaded_bytes=0,
            total_bytes=0,
            installing=False,
        )
        thread = threading.Thread(
            target=self._download_worker,
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

    def _create_version_source(self, config: dict[str, Any]) -> Any:
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
        asset_name_pattern = config.get(pattern_key) or config.get("asset_name_pattern_windows", DEFAULT_ASSET_PATTERN_WINDOWS)
        asset_name = asset_name_pattern.format(version=release.version)

        matched = next((a for a in release.assets if a.name == asset_name), None)
        if not matched:
            raise RuntimeError(f"Desktop installer not found: {asset_name}")

        sha256_url = ""
        sha256_name = config["sha256_name_pattern"].format(version=release.version)
        sha_matched = next((a for a in release.assets if a.name == sha256_name), None)
        if sha_matched:
            sha256_url = sha_matched.download_url

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
            sha256_url=sha256_url,
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

    def _download_worker(self) -> None:
        status = self.get_status()
        download_url = str(status["download_url"])
        asset_name = str(status["asset_name"])
        sha256_url = str(status["sha256_url"])
        final_path = _updates_dir() / asset_name
        partial_path = final_path.with_suffix(final_path.suffix + ".part")
        try:
            self._download_file(download_url, partial_path)
            if sha256_url:
                sha_raw = self._fetch_text(sha256_url)
                expected_sha = _parse_sha256(sha_raw)
                if not expected_sha:
                    raise RuntimeError("Invalid SHA256 sidecar format.")
                actual_sha = _sha256_file(partial_path)
                if actual_sha != expected_sha:
                    raise RuntimeError("Downloaded installer SHA256 mismatch.")

            partial_path.replace(final_path)
            size = final_path.stat().st_size
            self._update_status(
                state="downloaded",
                downloaded_path=str(final_path),
                downloaded_bytes=size,
                total_bytes=size,
                error="",
            )
        except Exception as exc:
            if partial_path.exists():
                partial_path.unlink(missing_ok=True)
            self._update_status(
                state="error",
                error=f"Update download failed: {exc}",
                downloaded_bytes=0,
            )

    def _download_file(self, url: str, destination: Path) -> None:
        request = Request(url, headers=self._download_headers())
        destination.parent.mkdir(parents=True, exist_ok=True)
        timeout_seconds = self._load_config()["timeout_seconds"]
        with (
            urlopen(request, timeout=timeout_seconds) as response,
            open(destination, "wb") as handle,
        ):
            total_header = response.headers.get("Content-Length")
            total_bytes = (
                int(total_header) if total_header and total_header.isdigit() else 0
            )
            self._update_status(total_bytes=total_bytes)

            downloaded = 0
            while True:
                chunk = response.read(DOWNLOAD_CHUNK_SIZE)
                if not chunk:
                    break
                handle.write(chunk)
                downloaded += len(chunk)
                self._update_status(
                    downloaded_bytes=downloaded, total_bytes=total_bytes
                )

    def _fetch_text(self, url: str) -> str:
        request = Request(url, headers=self._download_headers())
        try:
            with urlopen(request, timeout=self._load_config()["timeout_seconds"]) as response:
                return response.read().decode("utf-8")
        except HTTPError as exc:
            raise RuntimeError(f"HTTP {exc.code} when requesting {url}") from exc
        except URLError as exc:
            raise RuntimeError(
                f"Network error when requesting {url}: {exc.reason}"
            ) from exc

    @staticmethod
    def _get_token() -> str:
        return os.getenv("GITCODE_TOKEN", "").strip() or DEFAULT_TEXT

    @staticmethod
    def _download_headers() -> dict[str, str]:
        headers = {
            "Accept": "application/octet-stream, */*",
            "User-Agent": f"JiuwenSwarm-Updater/{__version__}",
        }
        token = UpdaterService._get_token()
        if token:
            headers["PRIVATE-TOKEN"] = token
        return headers

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
                    release_api_url = pypi_mirror.rstrip("/") + "/simple/" + repo + "/"
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
            "sha256_name_pattern": str(
                updater.get("sha256_name_pattern") or DEFAULT_SHA256_PATTERN
            ),
            "timeout_seconds": timeout_seconds,
            "access_token": UpdaterService._get_token(),
            "pypi_mirror": str(updater.get("pypi_mirror") or "").strip(),
        }

    def _update_status(self, **updates: Any) -> None:
        with self._lock:
            for key, value in updates.items():
                setattr(self._status, key, value)