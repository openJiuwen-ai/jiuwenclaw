# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""归档处理：ZIP/TAR 安全解压、下载验证."""

from __future__ import annotations

import hashlib
import io
import logging
import tarfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

import httpx

from jiuwenclaw.agentserver.skill_utils import (
    _IMPORT_LOCAL_DEFAULT_ALLOWED_DOWNLOAD_HOSTS,
    _IMPORT_LOCAL_REMOTE_TIMEOUT,
    _OPENJIUWEN_DEFAULT_ALLOWED_DOWNLOAD_HOSTS,
    _OPENJIUWEN_MARKET_TIMEOUT,
)

logger = logging.getLogger(__name__)


def safe_extract_zip_to_dir(zip_path: Path, dest_dir: Path) -> None:
    """将 ZIP 解压到 dest_dir，拒绝 Zip Slip（..、绝对路径、写出目标目录外）。"""
    dest_root = dest_dir.resolve()
    dest_root.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "r") as zf:
        for info in zf.infolist():
            raw = (info.filename or "").replace("\\", "/")
            if not raw or raw.startswith("/"):
                continue
            if "\0" in raw:
                raise RuntimeError("ZIP 包含非法文件名")
            is_dir = raw.endswith("/") or info.is_dir()
            rel_str = raw.rstrip("/")
            if not rel_str:
                continue
            rel = PurePosixPath(rel_str)
            if rel.is_absolute() or ".." in rel.parts:
                raise RuntimeError("ZIP 包含非法路径")
            dest_path = dest_root.joinpath(*rel.parts)
            try:
                dest_path = dest_path.resolve()
                dest_path.relative_to(dest_root)
            except ValueError as exc:
                raise RuntimeError("ZIP 路径越界") from exc
            if is_dir:
                dest_path.mkdir(parents=True, exist_ok=True)
                continue
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info, "r") as src:
                dest_path.write_bytes(src.read())


def safe_extract_tar_to_dir(tar_path: Path, dest_dir: Path) -> None:
    """Extract TAR/TAR.GZ/TGZ safely into dest_dir."""
    dest_root = dest_dir.resolve()
    dest_root.mkdir(parents=True, exist_ok=True)
    with tarfile.open(tar_path, "r:*") as tf:
        for member in tf.getmembers():
            raw = (member.name or "").replace("\\", "/")
            if not raw or raw.startswith("/"):
                continue
            if "\0" in raw:
                raise RuntimeError("归档包含非法文件名")
            rel = PurePosixPath(raw.rstrip("/"))
            if not rel.parts:
                continue
            if rel.is_absolute() or ".." in rel.parts:
                raise RuntimeError("归档包含非法路径")
            if member.islnk() or member.issym():
                raise RuntimeError("归档包含链接文件，已拒绝导入")
            dest_path = dest_root.joinpath(*rel.parts)
            try:
                dest_path = dest_path.resolve()
                dest_path.relative_to(dest_root)
            except ValueError as exc:
                raise RuntimeError("归档路径越界") from exc
            if member.isdir():
                dest_path.mkdir(parents=True, exist_ok=True)
                continue
            extracted = tf.extractfile(member)
            if extracted is None:
                continue
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            with extracted:
                dest_path.write_bytes(extracted.read())


def detect_archive_format(body: bytes) -> str:
    if len(body) >= 4 and body.startswith(b"PK"):
        return "zip"
    try:
        with tarfile.open(fileobj=io.BytesIO(body), mode="r:*"):
            return "tar"
    except tarfile.TarError:
        pass
    return ""


def extract_archive_bytes_to_dir(body: bytes, dest_dir: Path) -> None:
    archive_format = detect_archive_format(body)
    logger.info(
        "[skill_archive] extract archive: format=%s bytes=%s dest_dir=%s",
        archive_format or "unknown",
        len(body),
        dest_dir,
    )
    if archive_format == "zip":
        archive_path = dest_dir / "artifact.zip"
        archive_path.write_bytes(body)
        safe_extract_zip_to_dir(archive_path, dest_dir)
        return
    if archive_format == "tar":
        archive_path = dest_dir / "artifact.tar"
        archive_path.write_bytes(body)
        safe_extract_tar_to_dir(archive_path, dest_dir)
        return
    raise RuntimeError("下载内容不是受支持的归档格式，目前仅支持 zip/tar/tar.gz/tgz")


async def download_remote_archive_and_verify(
    download_url: str,
    *,
    checksum_sha256: str = "",
    timeout: float | None = None,
) -> bytes:
    timeout = max(30.0, timeout or _IMPORT_LOCAL_REMOTE_TIMEOUT)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        resp = await client.get(download_url)
        resp.raise_for_status()
        body = resp.content or b""

    if not body:
        raise RuntimeError("下载内容为空")

    expected = checksum_sha256.strip().lower()
    if expected:
        digest = hashlib.sha256(body).hexdigest().lower()
        if digest != expected:
            raise RuntimeError("下载文件校验失败（SHA256 不匹配）")

    archive_format = detect_archive_format(body)
    if archive_format == "zip":
        try:
            with zipfile.ZipFile(io.BytesIO(body), "r") as zf:
                if zf.testzip() is not None:
                    raise RuntimeError("下载 ZIP 文件已损坏")
        except zipfile.BadZipFile as exc:
            raise RuntimeError("下载内容不是有效 ZIP 文件") from exc
        return body
    if archive_format == "tar":
        try:
            with tarfile.open(fileobj=io.BytesIO(body), mode="r:*"):
                pass
        except tarfile.TarError as exc:
            raise RuntimeError("下载内容不是有效 TAR 归档") from exc
        return body
    raise RuntimeError("下载内容不是受支持的归档格式，目前仅支持 zip/tar/tar.gz/tgz")


async def download_zip_and_verify(
    download_url: str,
    *,
    checksum_sha256: str = "",
    timeout: float | None = None,
) -> bytes:
    timeout = max(30.0, timeout or _OPENJIUWEN_MARKET_TIMEOUT)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        resp = await client.get(download_url)
        resp.raise_for_status()
        body = resp.content or b""

    if not body:
        raise RuntimeError("下载内容为空")
    if len(body) < 4 or not body.startswith(b"PK"):
        raise RuntimeError("下载内容不是 ZIP 文件")

    expected = checksum_sha256.strip().lower()
    if expected:
        digest = hashlib.sha256(body).hexdigest().lower()
        if digest != expected:
            raise RuntimeError("下载文件校验失败（SHA256 不匹配）")

    try:
        with zipfile.ZipFile(io.BytesIO(body), "r") as zf:
            if zf.testzip() is not None:
                raise RuntimeError("下载 ZIP 文件已损坏")
    except zipfile.BadZipFile as exc:
        raise RuntimeError("下载内容不是有效 ZIP 文件") from exc
    return body


def locate_skill_dir(path: Path) -> Path | None:
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
        for md in path.rglob("*.md"):
            if md.is_file() and md.name.lower() == "skill.md":
                return md.parent
    return None


def normalize_lang_suffix(name: str) -> str:
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


def generate_agent_data_for_workspace(workspace_root: Path) -> None:
    """Generate agent/jiuwenclaw_workspace/agent-data.json from agent tree."""
    agent_root = workspace_root.resolve()
    output_path = (agent_root / "agent-data.json").resolve()
    root_folder_key = "__root__"

    if not agent_root.exists() or not agent_root.is_dir():
        return

    import json

    folder_data: dict[str, list[dict[str, str | bool]]] = {}
    seen_paths: dict[str, set[str]] = {}
    for entry in sorted(agent_root.rglob("*")):
        if not entry.is_file():
            continue
        relative_folder_path = entry.parent.relative_to(agent_root.parent).as_posix()
        folder_key = root_folder_key if relative_folder_path == "." else relative_folder_path

        display_name = normalize_lang_suffix(entry.name)
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
