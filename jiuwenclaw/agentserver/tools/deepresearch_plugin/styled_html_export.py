# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from __future__ import annotations

import base64
import io
import logging
import shutil
import zipfile
from pathlib import Path, PurePosixPath, PureWindowsPath
from tempfile import TemporaryDirectory

from openjiuwen_deepsearch.algorithm.report_style.service import (
    StyledReportResult,
    stylize_report,
)
from openjiuwen_deepsearch.framework.openjiuwen.llm.report_style_runtime import (
    report_style_llm_context,
)

logger = logging.getLogger(__name__)


def _validate_zip_member(member_name: str) -> str:
    normalized_name = member_name.replace("\\", "/")
    posix_path = PurePosixPath(normalized_name)
    windows_path = PureWindowsPath(member_name)
    if posix_path.is_absolute() or windows_path.is_absolute() or ".." in posix_path.parts:
        raise ValueError(f"unsafe ZIP member: {member_name}")
    return normalized_name


def _extract_bundle(convert_content: str, destination: Path) -> Path:
    try:
        archive_bytes = base64.b64decode(convert_content, validate=True)
    except ValueError as exc:
        raise ValueError("invalid styled report base64 payload") from exc

    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
        normalized_names = {
            _validate_zip_member(member.filename)
            for member in archive.infolist()
        }
        if "report_bundle/report.html" not in normalized_names:
            raise ValueError("styled report bundle is missing report_bundle/report.html")
        archive.extractall(destination)

    return destination / "report_bundle"


def _copy_asset_directory(source: Path, destination: Path) -> None:
    if not source.is_dir() or not any(source.iterdir()):
        return
    shutil.copytree(source, destination, dirs_exist_ok=True)


def _install_bundle(
    bundle_root: Path,
    html_path: Path,
) -> None:
    report_base = html_path.with_suffix("")
    infer_dir = report_base.with_name(f"{report_base.name}_infer")
    chart_dir = report_base.with_name(f"{report_base.name}_charts")
    _copy_asset_directory(bundle_root / "infer", infer_dir)
    _copy_asset_directory(bundle_root / "charts", chart_dir)

    html = (bundle_root / "report.html").read_text(encoding="utf-8")
    html = html.replace('href="infer/', f'href="{infer_dir.name}/')
    html = html.replace("href='infer/", f"href='{infer_dir.name}/")
    html = html.replace('src="charts/', f'src="{chart_dir.name}/')
    html = html.replace("src='charts/", f"src='{chart_dir.name}/")

    html_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_html_path = html_path.with_suffix(f"{html_path.suffix}.tmp")
    try:
        temporary_html_path.write_text(html, encoding="utf-8", newline="\n")
        temporary_html_path.replace(html_path)
    finally:
        temporary_html_path.unlink(missing_ok=True)


async def export_styled_html(
    final_result: dict,
    llm_config: dict,
    *,
    html_path: str | Path,
) -> StyledReportResult:
    async with report_style_llm_context(llm_config) as llm:
        result = await stylize_report(final_result, llm)

    target_html_path = Path(html_path)
    with TemporaryDirectory(prefix="jiuwenclaw_report_") as temporary_dir:
        bundle_root = _extract_bundle(result.convert_content, Path(temporary_dir))
        _install_bundle(bundle_root, target_html_path)
    return result


__all__ = ["export_styled_html"]
