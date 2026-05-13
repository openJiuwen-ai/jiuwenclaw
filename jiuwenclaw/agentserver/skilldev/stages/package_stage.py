# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""PACKAGE 阶段处理器.

- 将 skill/ 目录打包为 {skill_name}.skill（zip 格式，与官方 .skill 格式一致）
- 排除 evals/（根目录级）、__pycache__、node_modules、.DS_Store、*.pyc 等
- 推送 ARTIFACT_READY 事件 → 进入 COMPLETED（描述优化已在打包前完成）
"""

from __future__ import annotations

import logging
import shutil
import zipfile
from pathlib import Path

from jiuwenclaw.agentserver.skilldev.context import SkillDevContext
from jiuwenclaw.agentserver.skilldev.schema import SkillDevEventType, SkillDevStage
from jiuwenclaw.agentserver.skilldev.stages.base import StageHandler, StageResult
from jiuwenclaw.agentserver.skilldev.stages.validate_stage import parse_skill_frontmatter

logger = logging.getLogger(__name__)

# 打包排除规则
_EXCLUDE_DIRS = {"__pycache__", "node_modules", ".git"}
_EXCLUDE_FILES = {".DS_Store"}
_EXCLUDE_GLOBS = {"*.pyc"}
_ROOT_EXCLUDE_DIRS = {"evals"}

# 打包前需要递归清理的可执行/编译产物
# - 目录: __pycache__ (Python 字节码缓存目录)
# - 文件: *.pyc / *.pyo / *.pyd (Python 编译产物 / 扩展模块)
_CLEANUP_DIRS = {"__pycache__"}
_CLEANUP_GLOBS = ("*.pyc", "*.pyo", "*.pyd")


class PackageStageHandler(StageHandler):
    """PACKAGE 阶段：打包 skill/ 为 .skill (zip) 文件."""

    async def execute(self, ctx: SkillDevContext) -> StageResult:
        skill_dir = ctx.workspace / "skill"
        output_dir = ctx.workspace / "output"
        output_dir.mkdir(exist_ok=True)

        # skill_name = (ctx.state.plan or {}).get("skill_name", "skill")

        skill_md_path = skill_dir / "SKILL.md"
        skill_name, _, _ = parse_skill_frontmatter(skill_md_path)

        # 官方格式为 .skill（本质是 zip）
        skill_filename = f"{skill_name}.zip"
        skill_path = output_dir / skill_filename

        # 打包前递归清理 skill_dir 中的 pyc 等可执行/编译产物，
        # 避免将运行期间生成的字节码/扩展文件打入最终的 .skill 包。
        self._cleanup_executables(skill_dir, ctx.state.task_id)

        await ctx.emit(
            SkillDevEventType.PROGRESS, {"message": f"正在打包 {skill_filename}..."}
        )

        self._zip_skill_dir(skill_dir, skill_path, skill_name, ctx.state.task_id)

        ctx.state.zip_path = str(skill_path)
        ctx.state.zip_size = skill_path.stat().st_size

        await ctx.emit(
            SkillDevEventType.ARTIFACT_READY,
            {
                "artifact": {
                    "id": "skill_package",
                    "name": skill_filename,
                    "type": "skill_package",
                    "size_bytes": ctx.state.zip_size,
                    "browsable": True,
                    "downloadable": True,
                },
            },
        )
        return StageResult(next_stage=SkillDevStage.COMPLETED)

    def _cleanup_executables(self, skill_dir: Path, session_id: str) -> None:
        """递归清理 skill_dir 中的 pyc 等可执行/编译产物.

        - 删除所有 ``__pycache__`` 目录（递归）
        - 删除所有匹配 ``*.pyc`` / ``*.pyo`` / ``*.pyd`` 的文件（递归）

        清理过程中的单个文件/目录删除失败不会中断流程，仅记录 warning 日志。
        """
        if not skill_dir.exists() or not skill_dir.is_dir():
            return

        removed_files = 0
        removed_dirs = 0

        for pattern in _CLEANUP_GLOBS:
            for file_path in skill_dir.rglob(pattern):
                if not file_path.is_file():
                    continue
                try:
                    file_path.unlink()
                    removed_files += 1
                    logger.debug(
                        "[session=%s] [PackageStage] 已删除文件: %s",
                        session_id,
                        file_path.relative_to(skill_dir),
                    )
                except OSError as exc:
                    logger.warning(
                        "[session=%s] [PackageStage] 删除文件失败 %s: %s",
                        session_id,
                        file_path,
                        exc,
                    )

        # 删除目录需要在文件删除之后；使用 list() 物化结果避免边遍历边删除
        for dir_name in _CLEANUP_DIRS:
            for dir_path in list(skill_dir.rglob(dir_name)):
                if not dir_path.is_dir():
                    continue
                try:
                    shutil.rmtree(dir_path)
                    removed_dirs += 1
                    logger.debug(
                        "[session=%s] [PackageStage] 已删除目录: %s",
                        session_id,
                        dir_path.relative_to(skill_dir),
                    )
                except OSError as exc:
                    logger.warning(
                        "[session=%s] [PackageStage] 删除目录失败 %s: %s",
                        session_id,
                        dir_path,
                        exc,
                    )

        if removed_files or removed_dirs:
            logger.info(
                "[session=%s] [PackageStage] 打包前清理完成: 文件 %d 个, 目录 %d 个",
                session_id,
                removed_files,
                removed_dirs,
            )
        else:
            logger.info(
                "[session=%s] [PackageStage] 打包前清理: 未发现需清理的可执行/编译产物",
                session_id,
            )

    def _zip_skill_dir(
        self, skill_dir: Path, zip_path: Path, root_dir_name: str, session_id: str
    ) -> None:
        """将 skill_dir 打包为 zip，排除无关文件并添加根目录."""
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for file_path in skill_dir.rglob("*"):
                if not file_path.is_file():
                    continue
                if self._should_exclude(file_path, skill_dir):
                    continue
                arcname = Path(root_dir_name) / file_path.relative_to(skill_dir)
                zf.write(file_path, arcname)
        logger.info(
            "[session=%s] [PackageStage] 打包完成: %s (%d bytes)",
            session_id,
            zip_path,
            zip_path.stat().st_size,
        )

    def _should_exclude(self, file_path: Path, skill_dir: Path) -> bool:
        """判断文件是否应被排除出 zip 包.

        排除规则：目录级排除 + 文件级排除 + glob 匹配。
        """
        import fnmatch

        rel_path = file_path.relative_to(skill_dir)
        parts = rel_path.parts

        if any(part in _EXCLUDE_DIRS for part in parts):
            return True

        # 根目录级别的排除（如 evals/）
        if len(parts) > 0 and parts[0] in _ROOT_EXCLUDE_DIRS:
            return True

        if rel_path.name in _EXCLUDE_FILES:
            return True

        return any(fnmatch.fnmatch(rel_path.name, pat) for pat in _EXCLUDE_GLOBS)
