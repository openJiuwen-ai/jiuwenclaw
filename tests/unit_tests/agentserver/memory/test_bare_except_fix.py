# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Verify issue #1245 fix: no bare except: in memory/manager.py.

Bare ``except:`` captures ``BaseException`` subclasses including
``asyncio.CancelledError``, ``KeyboardInterrupt``, and ``SystemExit``,
which can cause process shutdown hangs and asyncio task cancellation failures.
"""

import asyncio
import ast
from pathlib import Path

import pytest

MANAGER_PATH = Path("jiuwenswarm/agents/harness/common/memory/manager.py")


@pytest.fixture(scope="module")
def manager_source() -> str:
    """Return the full source text of the memory manager module."""
    source = MANAGER_PATH.read_text(encoding="utf-8")
    assert len(source) > 0, f"{MANAGER_PATH} is empty"
    return source


# ---------------------------------------------------------------------------
# Static AST check – no bare ``except:`` remain
# ---------------------------------------------------------------------------

def _find_bare_excepts(source: str) -> list[tuple[int, str]]:
    """Return (line_number, line_text) for every bare ``except:`` node."""
    tree = ast.parse(source)
    results: list[tuple[int, str]] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            # Bare except: has no type and no 'as' name
            if node.type is None and not node.name:
                lineno = getattr(node, "lineno", 0)
                lines = source.splitlines()
                line_text = lines[lineno - 1] if 0 < lineno <= len(lines) else ""
                results.append((lineno, line_text.strip()))

    return results


def test_no_bare_except_ast(manager_source: str) -> None:
    """AST-level check: no bare ``except:`` handler exists."""
    bare = _find_bare_excepts(manager_source)
    assert bare == [], (
        f"Found {len(bare)} bare except: handler(s) at lines: "
        + ", ".join(f"L{n} ({txt})" for n, txt in bare)
    )


# ---------------------------------------------------------------------------
# Runtime behaviour – ``except Exception:`` does NOT catch BaseException
# ---------------------------------------------------------------------------

def test_except_exception_does_not_catch_cancelled_error() -> None:
    """``except Exception:`` should NOT catch ``asyncio.CancelledError``."""
    caught = False
    try:
        try:
            raise asyncio.CancelledError()
        except Exception:
            caught = True
    except BaseException:
        pass  # CancelledError propagated as expected
    assert not caught, "except Exception: incorrectly caught asyncio.CancelledError"


def test_except_exception_does_not_catch_keyboard_interrupt() -> None:
    """``except Exception:`` should NOT catch ``KeyboardInterrupt``."""
    caught = False
    try:
        try:
            raise KeyboardInterrupt()
        except Exception:
            caught = True
    except BaseException:
        pass  # KeyboardInterrupt propagated as expected
    assert not caught, "except Exception: incorrectly caught KeyboardInterrupt"


def test_except_exception_does_not_catch_system_exit() -> None:
    """``except Exception:`` should NOT catch ``SystemExit``."""
    caught = False
    try:
        try:
            raise SystemExit()
        except Exception:
            caught = True
    except BaseException:
        pass  # SystemExit propagated as expected
    assert not caught, "except Exception: incorrectly caught SystemExit"


# ---------------------------------------------------------------------------
# Content-level safeguard – quick grep for bare 'except:' lines
# ---------------------------------------------------------------------------

def test_no_bare_except_grep() -> None:
    """Quick grep safeguard: ensure no 'except:' without Exception follows."""
    text = MANAGER_PATH.read_text(encoding="utf-8")
    lines = text.splitlines()
    offending: list[int] = []
    for i, line in enumerate(lines, start=1):
        stripped = line.strip()
        # Match a bare 'except:' on its own (not 'except Exception:', 'except ValueError:', etc.)
        if stripped == "except:":
            offending.append(i)
    assert not offending, (
        f"Found {len(offending)} bare 'except:' line(s): L"
        + ", L".join(str(n) for n in offending)
    )
