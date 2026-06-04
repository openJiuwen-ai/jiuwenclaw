from __future__ import annotations

import json
import logging
import re
import socket

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from jiuwenswarm.common.version import __version__

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 20
GITHUB_API = "https://api.github.com/repos/{owner}/{repo}/releases/latest"
GITCODE_API = "https://api.gitcode.com/api/v5/repos/{owner}/{repo}/releases/latest"
PYPI_SIMPLE_API = "https://pypi.org/simple/{package}/"

# Matches pre-release markers: 0.2.0-beta1, 0.2.0.beta.1, 0.2.0alpha, 0.2.0rc2, 0.2.0.dev0, etc.
_PRERELEASE_PATTERN = re.compile(
    r"\d[.\-_]?(?:alpha|beta|rc|dev|pre|a|b)(?:\.?\d+)?(?:\b|$)",
    re.IGNORECASE,
)


def is_prerelease_version(version: str) -> bool:
    """Return True when *version* looks like a pre-release (alpha / beta / rc / dev)."""
    return bool(_PRERELEASE_PATTERN.search((version or "").strip().lstrip("vV")))


def strip_prerelease_suffix(version: str) -> str:
    """Remove the pre-release suffix so that ``0.2.0.beta1`` becomes ``0.2.0``."""
    normalized = (version or "").strip().lstrip("vV")
    # Capture the base numeric version (e.g. 0.2.0) and discard the optional
    # pre-release tail (e.g. .beta1, -rc.2, rc2, alpha, etc.).
    m = re.match(
        r"(\d+(?:\.\d+)*)"
        r"(?:[.\-_]?(?:alpha|beta|rc|dev|pre|a|b)(?:[.\-_]?\d+)?)*"
        r"$",
        normalized,
        re.IGNORECASE,
    )
    if m:
        return m.group(1)
    return normalized


def _is_prerelease_entry(data: dict) -> bool:
    """Return True when a release dict is a pre-release or draft."""
    return bool(
        data.get("prerelease")
        or data.get("is_prerelease")
        or data.get("draft")
    )


def _unwrap_list(raw: Any) -> list | None:
    """Normalise a list-API response that may be wrapped in a dict.

    Some hosts return ``{"data": [...]}`` or ``{"items": [...]}`` instead of a
    bare JSON array.  Returns the unwrapped list, or *raw* if it is already a
    list.  Returns None when the shape is unrecognised.
    """
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        for key in ("data", "items", "releases", "results"):
            candidate = raw.get(key)
            if isinstance(candidate, list):
                return candidate
    return None


@dataclass
class ReleaseAsset:
    name: str
    download_url: str
    size: int = 0


@dataclass
class ReleaseInfo:
    version: str
    release_notes: str = ""
    published_at: str = ""
    assets: list[ReleaseAsset] = field(default_factory=list)
    source_type: str = ""
    prerelease: bool = False


class VersionSource(ABC):
    def __init__(self, name: str = "", timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS) -> None:
        self._timeout = timeout_seconds
        self._name = name

    @abstractmethod
    def fetch_latest(self) -> ReleaseInfo:
        ...

    def fetch_assets(self) -> list[ReleaseAsset]:
        return self.fetch_latest().assets

    @staticmethod
    def _clean_version(raw: str) -> str:
        cleaned = (raw or "").strip().lstrip("vV")
        match = re.search(r"\d+(?:\.\d+)*", cleaned)
        return match.group() if match else cleaned

    def _fetch_json(self, url: str, headers: dict[str, str] | None = None) -> Any:
        return json.loads(self._fetch_text(url, headers))

    def _fetch_text(self, url: str, headers: dict[str, str] | None = None) -> str:
        request = Request(url, headers=headers or {})
        try:
            with urlopen(request, timeout=self._timeout) as response:
                return response.read().decode("utf-8")
        except HTTPError as exc:
            raise RuntimeError(f"HTTP {exc.code} when requesting {url}") from exc
        except socket.timeout as exc:
            raise RuntimeError(
                f"Timeout ({self._timeout}s) when requesting {url}"
            ) from exc
        except URLError as exc:
            raise RuntimeError(
                f"Network error when requesting {url}: {exc.reason}"
            ) from exc


class GitHubReleasesSource(VersionSource):
    def __init__(
        self,
        owner: str,
        repo: str,
        token: str = "",
        api_url: str = "",
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        super().__init__(name=repo, timeout_seconds=timeout_seconds)
        self._api_url = api_url or GITHUB_API.format(owner=owner, repo=repo)
        self._token = token

    def fetch_latest(self) -> ReleaseInfo:
        headers = self._build_headers()
        data = self._fetch_json(self._api_url, headers)
        tag_name = str(data.get("tag_name") or "")
        version = self._clean_version(tag_name)
        if not version:
            raise RuntimeError("GitHub release tag_name is missing or empty.")

        published_at = str(data.get("published_at") or "")
        body = str(data.get("body") or "")
        prerelease = bool(data.get("prerelease") or data.get("draft"))

        assets_raw = data.get("assets") or []
        assets = [
            ReleaseAsset(
                name=str(item.get("name", "")),
                download_url=str(item.get("browser_download_url", "")),
                size=int(item.get("size", 0)),
            )
            for item in assets_raw
            if isinstance(item, dict) and item.get("name")
        ]

        return ReleaseInfo(
            version=version,
            release_notes=body,
            published_at=published_at,
            assets=assets,
            source_type="github",
            prerelease=prerelease,
        )

    def _build_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {
            "Accept": "application/json",
            "User-Agent": f"{self._name}-Updater/{__version__}",
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers


class GitCodeReleasesSource(VersionSource):
    def __init__(
        self,
        owner: str,
        repo: str,
        access_token: str = "",
        api_url: str = "",
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        super().__init__(name=repo, timeout_seconds=timeout_seconds)
        self._api_url = api_url or GITCODE_API.format(owner=owner, repo=repo)
        self._access_token = access_token
        # Derive the releases-list URL from the latest-release URL.
        self._list_url = (
            self._api_url.replace("/latest", "")
            if "/latest" in self._api_url
            else self._api_url
        )

    def fetch_latest(self) -> ReleaseInfo:
        headers = self._build_headers()
        data = self._fetch_json(self._api_url, headers)

        if not isinstance(data, dict):
            raise RuntimeError(f"Unexpected GitCode API response type: {type(data)}")

        release = self._parse_release(data)
        if release is None:
            raise RuntimeError("GitCode release tag_name is missing or empty.")

        # If the /latest endpoint returned a pre-release, walk the releases list
        # to find the newest stable release instead.
        if release.prerelease:
            try:
                stable = self._fetch_latest_stable(headers)
                if stable is not None:
                    return stable
            except Exception:
                logger.warning("Failed to fetch latest stable release, using /latest result instead: %s",
                    release.version)

        return release

    def _fetch_latest_stable(self, headers: dict[str, str]) -> ReleaseInfo | None:
        """Fetch the releases list, find the newest non-prerelease by version."""
        raw = self._fetch_json(self._list_url, headers)

        # Normalise wrapped responses (some APIs wrap lists in a dict)
        entries = _unwrap_list(raw)
        if entries is None:
            return None

        best: ReleaseInfo | None = None
        best_key: tuple[int, ...] = ()

        for entry in entries:
            if not isinstance(entry, dict):
                continue
            if _is_prerelease_entry(entry):
                continue
            release = self._parse_release(entry)
            if release is None:
                continue
            key = self._version_key_for_cmp(release.version)
            if best is None or key > best_key:
                best = release
                best_key = key

        return best

    @staticmethod
    def _version_key_for_cmp(version: str) -> tuple[int, ...]:
        numbers = re.findall(r"\d+", strip_prerelease_suffix(version))
        return tuple(int(part) for part in numbers) or (0,)

    @staticmethod
    def _parse_release(data: dict) -> ReleaseInfo | None:
        tag_name = str(data.get("tag_name") or "")
        version = VersionSource._clean_version(tag_name)
        if not version:
            return None

        release_notes = str(data.get("body") or data.get("description") or "")
        published_at = str(
            data.get("published_at")
            or data.get("created_at")
            or ""
        )
        prerelease = _is_prerelease_entry(data)

        assets_raw = data.get("assets") or []
        assets = [
            ReleaseAsset(
                name=str(item.get("name", "")),
                download_url=str(item.get("url") or item.get("browser_download_url", "")),
                size=int(item.get("size", 0)),
            )
            for item in assets_raw
            if isinstance(item, dict) and item.get("name")
        ]

        return ReleaseInfo(
            version=version,
            release_notes=release_notes,
            published_at=published_at,
            assets=assets,
            source_type="gitcode",
            prerelease=prerelease,
        )

    def _build_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {
            "Accept": "application/json",
            "User-Agent": f"{self._name}-Updater/{__version__}",
        }
        if self._access_token:
            headers["PRIVATE-TOKEN"] = self._access_token
        return headers


class PyPIVersionSource(VersionSource):
    def __init__(
        self,
        package: str = "jiuwenswarm",
        mirror: str = "",
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        super().__init__(name=package, timeout_seconds=timeout_seconds)
        if mirror:
            self._base_url = mirror.rstrip("/")
            self._api_url = urljoin(self._base_url + "/", f"simple/{package}/")
        else:
            self._base_url = "https://pypi.org"
            self._api_url = PYPI_SIMPLE_API.format(package=package)

    def fetch_latest(self) -> ReleaseInfo:
        data = self._fetch_simple_json()
        if data is None:
            raise RuntimeError("Failed to fetch PyPI simple API response.")
        files = data.get("files") or []
        if not isinstance(files, list):
            files = []

        whl_entries = [
            f for f in files
            if isinstance(f, dict) and f.get("filename", "").endswith(".whl")
        ]
        if not whl_entries:
            raise RuntimeError("No .whl files found in PyPI simple API response.")

        versions = set()
        for entry in whl_entries:
            fn = str(entry.get("filename", ""))
            m = re.match(rf"{re.escape(self._name)}-([\d.]+(?:[.\-_]?(?:alpha|beta|rc|dev|pre|a|b)(?:\.?\d+)?)*)-", fn)
            if m:
                versions.add(m.group(1))
        if not versions:
            raise RuntimeError("Could not parse any version from .whl filenames.")

        # Prefer the latest *stable* version; fall back to pre-release if none exist
        stable_versions = {v for v in versions if not is_prerelease_version(v)}
        pool = stable_versions if stable_versions else versions
        latest_version = max(pool, key=self._version_key)
        latest_whls = [e for e in whl_entries if latest_version in e.get("filename", "")]
        latest_whl = latest_whls[-1] if latest_whls else whl_entries[-1]

        published_at = str(latest_whl.get("upload-time") or "")
        assets = [
            ReleaseAsset(
                name=str(e.get("filename", "")),
                download_url=self._resolve_url(str(e.get("url", ""))),
                size=int(e.get("size", 0)),
            )
            for e in whl_entries
        ]

        return ReleaseInfo(
            version=latest_version,
            published_at=published_at,
            assets=assets,
            source_type="pypi",
        )

    def _fetch_simple_json(self) -> Any:
        req = Request(self._api_url)
        req.add_header("Accept", "application/vnd.pypi.simple.v1+json")
        try:
            with urlopen(req, timeout=self._timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (HTTPError, URLError, json.JSONDecodeError):
            return self._fetch_simple_html()

    def _fetch_simple_html(self) -> Any:
        raw = self._fetch_text(self._api_url)
        links = re.findall(
            r'<a\s+(?:[^>]*?\s+)?href="([^"]*)"[^>]*>([^<]+)</a>',
            raw,
        )
        files = []
        for href, text in links:
            filename = text.strip()
            if not filename:
                continue
            files.append({
                "filename": filename,
                "url": href,
            })
        return {"files": files}

    def _resolve_url(self, url: str) -> str:
        return urljoin(self._api_url, url)

    @staticmethod
    def _version_key(version: str) -> tuple[int, ...]:
        numbers = re.findall(r"\d+", version)
        return tuple(int(part) for part in numbers) or (0,)