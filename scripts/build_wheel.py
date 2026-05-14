#!/usr/bin/env python3
"""JiuwenClaw wheel package builder with version control.

A standalone script to build Python wheel packages with customizable version numbers.
Follows industry best practices for Python packaging.

Usage:
    python scripts/build_wheel.py                    # Build with current version
    python scripts/build_wheel.py --version 1.0.0    # Build with specific version
    python scripts/build_wheel.py --version 1.0.0.dev1  # Development version
    python scripts/build_wheel.py --version 1.0.0 --bump  # Permanent version bump
    python scripts/build_wheel.py --git-hash         # Add git hash to version

Features:
    - Version override via --version (temporary, restores after build)
    - Permanent version bump with --bump flag
    - Git-aware versioning (--git-hash suffix)
    - Clean build environment (--clean)
    - Output directory customization (--outdir)
    - Frontend build integration (--build-frontend)
    - Post-build verification
"""

from __future__ import annotations

import argparse
import contextlib
import logging
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

# Configure logger for build script
logger = logging.getLogger("build_wheel")

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT_TOML = ROOT / "pyproject.toml"
WEB_DIR = ROOT / "jiuwenclaw" / "web"


@dataclass
class BuildResult:
    success: bool
    wheel_path: Path | None = None
    error_message: str | None = None


def get_current_version() -> str:
    """Extract current version from pyproject.toml."""
    content = PYPROJECT_TOML.read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', content, re.MULTILINE)
    if not match:
        raise RuntimeError(f"Cannot find version in {PYPROJECT_TOML}")
    return match.group(1)


def get_git_short_hash() -> str:
    """Get short git commit hash for version suffix."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()
    except subprocess.CalledProcessError:
        return ""


def parse_version_spec(version_str: str, add_git_hash: bool) -> str:
    """Parse and potentially augment version string.

    Supports PEP 440 version formats:
    - Standard: 1.0.0, 0.1.10
    - Pre-release: 1.0.0a1, 1.0.0b2, 1.0.0rc1
    - Dev release: 1.0.0.dev1, 0.1.11.dev20260513
    - Local version: 1.0.0+local, 1.0.0+gita2c7493
    """
    if add_git_hash:
        git_hash = get_git_short_hash()
        if git_hash:
            # Append as local version identifier per PEP 440
            if "+" in version_str:
                version_str = f"{version_str}.git{git_hash}"
            else:
                version_str = f"{version_str}+git{git_hash}"

    return version_str


@contextlib.contextmanager
def version_override(new_version: str):
    """Context manager to temporarily override pyproject.toml version.

    Saves original content, modifies version, yields, then restores.
    """
    original_content = PYPROJECT_TOML.read_text(encoding="utf-8")
    original_version = get_current_version()

    if new_version == original_version:
        # No override needed
        yield
        return

    # Modify version
    new_content = re.sub(
        r'^version\s*=\s*"([^"]+)"',
        f'version = "{new_version}"',
        original_content,
        count=1,
        flags=re.MULTILINE,
    )

    logger.info("Temporarily overriding version: %s -> %s", original_version, new_version)
    PYPROJECT_TOML.write_text(new_content, encoding="utf-8")

    try:
        yield
    finally:
        # Restore original version
        PYPROJECT_TOML.write_text(original_content, encoding="utf-8")
        logger.info("Restored original version: %s", original_version)


def bump_version(new_version: str) -> None:
    """Permanently update version in pyproject.toml."""
    original_version = get_current_version()

    content = PYPROJECT_TOML.read_text(encoding="utf-8")
    new_content = re.sub(
        r'^version\s*=\s*"([^"]+)"',
        f'version = "{new_version}"',
        content,
        count=1,
        flags=re.MULTILINE,
    )

    PYPROJECT_TOML.write_text(new_content, encoding="utf-8")
    logger.info("Bumped version: %s -> %s", original_version, new_version)


def clean_build_artifacts() -> None:
    """Remove build artifacts: build/, dist/, *.egg-info."""
    artifacts = ["build", "dist"]
    for artifact in artifacts:
        path = ROOT / artifact
        if path.exists():
            logger.info("Cleaning %s", path.relative_to(ROOT))
            shutil.rmtree(path)

    # Remove egg-info directories
    for egg_info in ROOT.glob("*.egg-info"):
        logger.info("Cleaning %s", egg_info.relative_to(ROOT))
        shutil.rmtree(egg_info)


def build_frontend() -> bool:
    """Build frontend assets with npm."""
    if not WEB_DIR.exists():
        logger.warning("Frontend directory not found: %s", WEB_DIR)
        return False

    node_modules = WEB_DIR / "node_modules"
    if not node_modules.exists():
        logger.info("Installing npm dependencies...")
        result = subprocess.run(
            ["npm", "install"],
            cwd=WEB_DIR,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            logger.error("npm install failed: %s", result.stderr)
            return False

    logger.info("Building frontend...")
    result = subprocess.run(
        ["npm", "run", "build"],
        cwd=WEB_DIR,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        logger.error("Frontend build failed: %s", result.stderr)
        return False

    dist_dir = WEB_DIR / "dist"
    if not dist_dir.exists() or not any(dist_dir.iterdir()):
        logger.warning("Frontend dist directory empty: %s", dist_dir)
        return False

    logger.info("Frontend built successfully")
    return True


def run_wheel_build(outdir: Path, no_isolation: bool, verbose: bool) -> BuildResult:
    """Execute wheel build using uv or python -m build.

    Returns BuildResult with success status and wheel path.
    """
    # Prefer uv build (project uses uv)
    uv_path = shutil.which("uv")
    if uv_path:
        try:
            subprocess.run([uv_path, "--version"], capture_output=True, check=True)
            builder = "uv"
            cmd = [
                uv_path,
                "build",
                "--wheel",
                "--out-dir",
                str(outdir),
            ]
        except subprocess.CalledProcessError:
            uv_path = None

    if not uv_path:
        builder = "build"
        cmd = [
            sys.executable,
            "-m",
            "build",
            "--wheel",
            "--outdir",
            str(outdir),
        ]
        if no_isolation:
            cmd.append("--no-isolation")

    if verbose:
        logger.info("Running: %s", " ".join(cmd))

    result = subprocess.run(
        cmd,
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        return BuildResult(
            success=False,
            error_message=f"Build failed with {builder}: {result.stderr}",
        )

    # Find built wheel
    wheels = list(outdir.glob("*.whl"))
    if not wheels:
        return BuildResult(
            success=False,
            error_message=f"No wheel found in {outdir}",
        )

    # Return most recent wheel
    wheel_path = max(wheels, key=lambda p: p.stat().st_mtime)
    return BuildResult(success=True, wheel_path=wheel_path)


def verify_wheel(wheel_path: Path) -> bool:
    """Verify the built wheel is valid."""
    logger.info("Verifying %s", wheel_path.name)

    size = wheel_path.stat().st_size
    if size < 1000:
        logger.warning("Wheel too small: %d bytes", size)
        return False

    # Try wheel unpack for validation
    check_dir = ROOT / "_wheel_verify"
    try:
        result = subprocess.run(
            [sys.executable, "-m", "wheel", "unpack", str(wheel_path), "--dest", str(check_dir)],
            capture_output=True,
            text=True,
        )
        if check_dir.exists():
            shutil.rmtree(check_dir)

        if result.returncode != 0:
            logger.error("Wheel verification failed: %s", result.stderr)
            return False

    except Exception:
        logger.warning("wheel module not available, skipping detailed check")

    logger.info("Wheel verified: %.1f KB", size / 1024)
    return True


def main() -> int:
    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format="[%(name)s] %(levelname)s: %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="Build JiuwenClaw wheel package with customizable version.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    parser.add_argument(
        "--version",
        metavar="VER",
        help="Override version number (e.g., 1.0.0, 0.2.0.dev1, 1.0.0+gita2c7)",
    )
    parser.add_argument(
        "--bump",
        action="store_true",
        help="Permanently update version in pyproject.toml (requires --version)",
    )
    parser.add_argument(
        "--git-hash",
        action="store_true",
        help="Append git commit hash to version (e.g., 0.1.10+gita2c7)",
    )
    parser.add_argument(
        "--outdir",
        type=Path,
        default=ROOT / "dist",
        help="Output directory for wheel (default: ./dist)",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Clean build artifacts before building",
    )
    parser.add_argument(
        "--no-isolation",
        action="store_true",
        help="Build without isolation (use current environment)",
    )
    parser.add_argument(
        "--build-frontend",
        action="store_true",
        help="Build frontend assets before packaging",
    )
    parser.add_argument(
        "--skip-frontend",
        action="store_true",
        help="Skip frontend check (--build-frontend or --skip-frontend required)",
    )
    parser.add_argument(
        "--no-verify",
        action="store_true",
        help="Skip wheel verification",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Show detailed build output",
    )

    args = parser.parse_args()

    # Validate arguments
    if args.bump and not args.version:
        parser.error("--bump requires --version")

    # Determine final version
    base_version = args.version or get_current_version()
    final_version = parse_version_spec(base_version, args.git_hash)

    logger.info("Target version: %s", final_version)

    # Clean if requested
    if args.clean:
        clean_build_artifacts()

    # Frontend handling
    if args.build_frontend:
        if not build_frontend():
            logger.error("Frontend build failed")
            return 1
    elif not args.skip_frontend:
        frontend_dist = WEB_DIR / "dist"
        if not frontend_dist.exists() or not any(frontend_dist.iterdir()):
            logger.warning("Frontend dist not found: %s", frontend_dist)
            logger.info("Use --build-frontend to build, or --skip-frontend to ignore")
            return 1

    # Ensure output directory
    args.outdir.mkdir(parents=True, exist_ok=True)

    # Execute build with version handling
    build_result: BuildResult

    if args.bump:
        # Permanent version bump
        bump_version(final_version)
        build_result = run_wheel_build(args.outdir, args.no_isolation, args.verbose)
    elif final_version != get_current_version():
        # Temporary version override
        with version_override(final_version):
            build_result = run_wheel_build(args.outdir, args.no_isolation, args.verbose)
    else:
        # Use current version
        build_result = run_wheel_build(args.outdir, args.no_isolation, args.verbose)

    if not build_result.success:
        logger.error("%s", build_result.error_message)
        return 1

    wheel_path = build_result.wheel_path

    # Verify wheel
    if not args.no_verify:
        if not verify_wheel(wheel_path):
            logger.error("Wheel verification failed")
            return 1

    # Summary
    logger.info("")
    logger.info("Build completed successfully!")
    logger.info("Package: %s", wheel_path.name)
    logger.info("Version: %s", final_version)
    logger.info("Path: %s", wheel_path)
    logger.info("Size: %.1f KB", wheel_path.stat().st_size / 1024)

    return 0


if __name__ == "__main__":
    sys.exit(main())