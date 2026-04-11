from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import logging
import os
import re
import shutil
import subprocess
import uuid

from jiuwenclaw.agentserver.tools.deepresearch_plugin.mermaid_common import (
    clean_mermaid_code,
    load_svg_markup,
    save_failed_mermaid_source,
)


logger = logging.getLogger(__name__)


def _env_flag_enabled(name: str) -> bool:
    value = os.getenv(name, "")
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(slots=True)
class MermaidCliStatus:
    path: str | None
    checked_paths: tuple[str, ...]
    message: str

    @property
    def available(self) -> bool:
        return self.path is not None


def resolve_mmdc_path() -> str | None:
    if _env_flag_enabled("MERMAID_DISABLE_CLI"):
        return None

    candidates: list[str] = []
    checked: set[str] = set()

    env_path = os.getenv("MERMAID_MMDC_PATH")
    if env_path:
        candidates.append(env_path)

    for name in ("mmdc.cmd", "mmdc", "mmdc.ps1"):
        resolved = shutil.which(name)
        if resolved:
            candidates.append(resolved)

    appdata = os.getenv("APPDATA")
    if appdata:
        candidates.extend(
            [
                str(Path(appdata) / "npm" / "mmdc.cmd"),
                str(Path(appdata) / "npm" / "mmdc"),
            ]
        )

    home = Path.home()
    candidates.extend(
        [
            str(home / "AppData" / "Roaming" / "npm" / "mmdc.cmd"),
            str(home / "AppData" / "Roaming" / "npm" / "mmdc"),
            str(Path(r"C:\Program Files\nodejs") / "mmdc.cmd"),
            str(Path(r"C:\Program Files\nodejs") / "mmdc"),
        ]
    )

    for candidate in candidates:
        normalized = str(Path(candidate).expanduser())
        if normalized in checked:
            continue
        checked.add(normalized)
        if Path(normalized).exists():
            return normalized
    return None


def ensure_mermaid_cli() -> MermaidCliStatus:
    if _env_flag_enabled("MERMAID_DISABLE_CLI"):
        return MermaidCliStatus(
            path=None,
            checked_paths=tuple(),
            message="Mermaid CLI is disabled by MERMAID_DISABLE_CLI.",
        )

    checked_paths: list[str] = []

    env_path = os.getenv("MERMAID_MMDC_PATH")
    if env_path:
        checked_paths.append(str(Path(env_path).expanduser()))

    for name in ("mmdc.cmd", "mmdc", "mmdc.ps1"):
        resolved = shutil.which(name)
        if resolved:
            checked_paths.append(resolved)

    appdata = os.getenv("APPDATA")
    if appdata:
        checked_paths.extend(
            [
                str(Path(appdata) / "npm" / "mmdc.cmd"),
                str(Path(appdata) / "npm" / "mmdc"),
            ]
        )

    home = Path.home()
    checked_paths.extend(
        [
            str(home / "AppData" / "Roaming" / "npm" / "mmdc.cmd"),
            str(home / "AppData" / "Roaming" / "npm" / "mmdc"),
            str(Path(r"C:\Program Files\nodejs") / "mmdc.cmd"),
            str(Path(r"C:\Program Files\nodejs") / "mmdc"),
        ]
    )

    seen: list[str] = []
    for candidate in checked_paths:
        if candidate not in seen:
            seen.append(candidate)

    path = resolve_mmdc_path()
    if path:
        return MermaidCliStatus(
            path=path,
            checked_paths=tuple(seen),
            message=f"Using Mermaid CLI: {path}",
        )

    return MermaidCliStatus(
        path=None,
        checked_paths=tuple(seen),
        message=(
            "Mermaid CLI was not found. Install @mermaid-js/mermaid-cli "
            "or set MERMAID_MMDC_PATH."
        ),
    )


def _build_mmdc_command(
    mmdc_path: str,
    input_path: Path,
    output_path: Path,
    output_format: str,
) -> list[str]:
    args = [
        "-i",
        str(input_path),
        "-o",
        str(output_path),
        "-b",
        "white",
    ]
    if output_format.lower() == "png":
        args.extend(["-s", "2"])

    mmdc_file = Path(mmdc_path)
    if os.name == "nt" and mmdc_file.suffix.lower() in {".cmd", ".bat"}:
        return ["cmd.exe", "/d", "/c", str(mmdc_file), *args]
    return [str(mmdc_file), *args]


def _build_mmdc_failure_details(
    command: list[str],
    result: subprocess.CompletedProcess[str],
) -> str:
    parts = [
        f"command: {' '.join(command)}",
        f"returncode: {result.returncode}",
    ]
    stdout = result.stdout.strip()
    stderr = result.stderr.strip()
    if stdout:
        parts.extend(["stdout:", stdout])
    if stderr:
        parts.extend(["stderr:", stderr])
    return "\n".join(parts)


def render_mermaid_offline(
    code: str,
    output_path: str | Path,
    *,
    output_format: str,
    debug_base_path: Path | None = None,
) -> bool:
    cleaned_code = clean_mermaid_code(code)
    output_file = Path(output_path)
    output_file.parent.mkdir(parents=True, exist_ok=True)

    cli_status = ensure_mermaid_cli()
    if not cli_status.available:
        save_failed_mermaid_source(
            cleaned_code,
            debug_base_path or output_file,
            extra_text=cli_status.message + "\nChecked paths:\n" + "\n".join(cli_status.checked_paths),
        )
        logger.warning(cli_status.message)
        return False

    input_file = output_file.parent / f".tmp_mermaid_{uuid.uuid4().hex}.mmd"
    try:
        input_file.write_text(cleaned_code, encoding="utf-8")

        command = _build_mmdc_command(
            cli_status.path,
            input_file,
            output_file,
            output_format=output_format,
        )

        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=120,
                check=False,
                creationflags=creationflags,
            )
        except Exception as exc:
            save_failed_mermaid_source(
                cleaned_code,
                debug_base_path or output_file,
                extra_text=f"Failed to execute Mermaid CLI: {exc}",
            )
            logger.warning("Mermaid CLI execution failed: %s", exc)
            return False
    finally:
        input_file.unlink(missing_ok=True)

    if result.returncode != 0:
        save_failed_mermaid_source(
            cleaned_code,
            debug_base_path or output_file,
            extra_text=_build_mmdc_failure_details(command, result),
        )
        logger.warning("Mermaid CLI returned a non-zero exit code: %s", result.returncode)
        return False

    if not output_file.exists() or output_file.stat().st_size == 0:
        save_failed_mermaid_source(
            cleaned_code,
            debug_base_path or output_file,
            extra_text="Mermaid CLI completed without producing an output file.",
        )
        logger.warning("Mermaid CLI finished without producing output: %s", output_file)
        return False

    return True
