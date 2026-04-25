# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path

from jiuwenclaw.agentserver.tools.deepresearch_plugin.mermaid_common import (
    clean_mermaid_code,
    load_svg_markup,
    save_failed_mermaid_source,
)

logger = logging.getLogger(__name__)


def _env_flag_enabled(name: str) -> bool:
    """Check if an environment variable flag is enabled.

    Args:
        name: The name of the environment variable to check.

    Returns:
        True if the environment variable value is one of "1", "true", "yes", or "on"
        (case-insensitive). False otherwise or if the variable is not set.
    """
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
    """Resolve the path to the Mermaid CLI (mmdc) executable.

    Searches for the mmdc executable in multiple locations in order:
    1. Environment variable MERMAID_MMDC_PATH (if set, with security validation)
    2. System PATH via shutil.which() for mmdc.cmd, mmdc, mmdc.ps1
    3. Windows APPDATA/npm directory
    4. User home directory AppData/Roaming/npm
    5. C:\\Program Files\\nodejs

    Returns:
        The normalized path to the mmdc executable if found, or None if:
        - MERMAID_DISABLE_CLI environment flag is enabled
        - No valid mmdc executable is found in any searched location
        
    Security Note:
        MERMAID_MMDC_PATH is validated to prevent path traversal and ensure
        it resolves to a real executable file. Environment variable override
        is logged for audit purposes.
    """
    if _env_flag_enabled("MERMAID_DISABLE_CLI"):
        return None

    candidates: list[str] = []
    checked: set[str] = set()

    env_path = os.getenv("MERMAID_MMDC_PATH")
    if env_path:
        try:
            # 规范化路径，防止路径穿越
            normalized_env = str(Path(env_path).expanduser().resolve())
            # 验证路径存在且是文件
            if Path(normalized_env).is_file():
                candidates.append(normalized_env)
                logger.warning(
                    "[Security] Using MERMAID_MMDC_PATH override: %s. "
                    "Ensure this path is trusted and not attacker-controlled.",
                    normalized_env
                )
            else:
                logger.warning(
                    "[Security] MERMAID_MMDC_PATH '%s' does not exist or is not a file. Ignoring.",
                    env_path
                )
        except Exception as e:
            logger.warning(
                "[Security] MERMAID_MMDC_PATH '%s' validation failed: %s. Ignoring.",
                env_path, e
            )

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
        try:
            normalized = str(Path(candidate).expanduser().resolve())
        except Exception:
            normalized = str(Path(candidate).expanduser())
        if normalized in checked:
            continue
        checked.add(normalized)
        if Path(normalized).exists():
            return normalized
    return None


def ensure_mermaid_cli() -> MermaidCliStatus:
    """Check if Mermaid CLI (mmdc) is available and return detailed status.

    Searches for the mmdc executable and returns a status object containing
    the resolved path (if found), all paths that were checked during the search,
    and a human-readable message describing the outcome.

    Returns:
        A MermaidCliStatus object with the following attributes:
        - path: The resolved mmdc path if found, otherwise None
        - checked_paths: Tuple of all candidate paths that were checked
        - message: Human-readable status message
        - available: Property that returns True if path is not None

        Returns early with empty checked_paths and disabled message if
        MERMAID_DISABLE_CLI environment flag is enabled.
    """
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
    """Build the command list for executing the Mermaid CLI (mmdc).

    Constructs a subprocess-compatible command list with appropriate arguments
    for rendering Mermaid diagrams. Handles Windows-specific invocation for
    .cmd/.bat files by wrapping with cmd.exe.

    Args:
        mmdc_path: The path to the mmdc executable.
        input_path: Path to the input .mmd file containing Mermaid code.
        output_path: Path where the rendered output file will be saved.
        output_format: The output format (e.g., "png", "svg"). PNG format
            includes an additional scale factor argument.

    Returns:
        A list of strings suitable for subprocess.run(), formatted as:
        - On Windows with .cmd/.bat files: ["cmd.exe", "/d", "/c", mmdc_path, ...args]
        - Otherwise: [mmdc_path, ...args]

        The args include: "-i" (input), "-o" (output), "-b white" (background),
        and "-s 2" (scale) for PNG format.
    """
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
    """Build a formatted failure details string from mmdc execution results.

    Constructs a human-readable multiline string containing diagnostic
    information about a failed mmdc CLI execution, suitable for logging
    or saving to debug files.

    Args:
        command: The command list that was executed.
        result: The CompletedProcess object from subprocess.run() containing
            returncode, stdout, and stderr.

    Returns:
        A newline-separated string containing:
        - The executed command as a space-separated string
        - The process return code
        - stdout content (if any, stripped of leading/trailing whitespace)
        - stderr content (if any, stripped of leading/trailing whitespace)
    """
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
    """Render Mermaid diagram code to an image file using the offline CLI.

    Takes Mermaid diagram code, cleans it, writes it to a temporary file,
    invokes the mmdc CLI to render it, and saves the output to the specified
    path. Handles various failure scenarios by saving debug information.

    Args:
        code: The raw Mermaid diagram code to render.
        output_path: The path where the rendered output file will be saved.
            Parent directories will be created if they don't exist.
        output_format: The output format for the rendered image (e.g., "png", "svg").
        debug_base_path: Optional base path for saving failed Mermaid source files
            for debugging. If None, uses output_path as the base.

    Returns:
        True if rendering succeeded and output file was created with non-zero size.
        False if:
        - Mermaid CLI is not available or disabled
        - CLI execution failed or timed out (120 second timeout)
        - CLI returned non-zero exit code
        - Output file was not created or is empty

        On failure, saves the cleaned Mermaid source code and error details to
        a debug file for troubleshooting, and logs a warning message.
    """
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
