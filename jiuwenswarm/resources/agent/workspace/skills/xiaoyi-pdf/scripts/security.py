"""Path security helpers — prevent directory traversal via filename injection."""

import os


def validate_safe_path(
    file_path: str,
    allowed_extensions: set | None = None,
    base_dir: str | None = None,
) -> str:
    """Resolve *file_path* and reject unsafe traversals.

    Returns the resolved absolute path on success; raises ``ValueError``
    otherwise.

    Parameters
    ----------
    file_path:
        The user-supplied file path to validate.
    allowed_extensions:
        Set of lowercase extensions including the dot, e.g. ``{'.pdf'}``.
        When *None*, any extension is allowed.
    base_dir:
        Directory the resolved path must live under.  When *None* the
        boundary check is skipped.  Pass ``os.getcwd()`` at CLI entry
        points to lock file access to the working tree.
    """
    # 1. Resolve symlinks, ".", "..", and normalise to an absolute path
    real = os.path.realpath(file_path)

    # 2. Extension allowlist
    if allowed_extensions is not None:
        ext = os.path.splitext(real)[1].lower()
        if ext not in allowed_extensions:
            raise ValueError(
                f"Blocked: file extension '{ext}' is not allowed. "
                f"Allowed: {sorted(allowed_extensions)}"
            )

    # 3. Directory-boundary check (opt-in)
    if base_dir is not None:
        real_base = os.path.realpath(base_dir)
        # Normalise separators for reliable prefix matching on Windows
        if os.path.sep == "\\":
            real = real.replace("/", "\\")
            real_base = real_base.replace("/", "\\")

        if not (real == real_base or real.startswith(real_base + os.path.sep)):
            raise ValueError(
                f"Blocked: path escapes the allowed directory.\n"
                f"  Path: {real}\n"
                f"  Base: {real_base}"
            )

    return real
