# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Unit tests for the server-side FastPath candidate pre-filter.

``_python_fastpath_candidate`` is the cheap *offer* gate in the server: it
decides which exec requests are worth forwarding to the in-sandbox daemon's
authoritative ``_fastpath_plan``. Marking a request only lets the daemon
consider it; the daemon still falls back on anything it does not fully
understand. So the pre-filter may be narrow but must not exclude a shape the
daemon can actually convert.

Note: ``python -c CODE`` was previously excluded here (the gate
returned ``head == PYTHON_EXECUTABLE`` for the ``-c`` shape, i.e. python3
only), so ``python -c`` never reached the daemon and always ran via a fresh
``subprocess``. The gate now offers the bare ``python[3] -c CODE`` shape for
both interpreters; the daemon's identity check decides whether ``python``
is safe.
"""

from __future__ import annotations

import os

import pytest

# ``jiuwenbox.server.runtime.process`` imports ``pwd``/``grp`` (POSIX only).
_SKIP_NON_POSIX = pytest.mark.skipif(
    not hasattr(os, "fork"),
    reason="server.runtime.process imports POSIX-only modules",
)

pytestmark = [pytest.mark.unit, _SKIP_NON_POSIX]


def _candidate(command):
    from jiuwenbox.server.runtime.process import _python_fastpath_candidate
    return _python_fastpath_candidate(list(command))


@pytest.mark.parametrize("command,expected", [
    # bare -c: both interpreters are offered (python and python3).
    (["python3", "-c", "print(1)"], True),
    (["python", "-c", "print(1)"], True),
    # bare -c with no code: not offered (let it error on subprocess).
    (["python3", "-c"], False),
    (["python", "-c"], False),
    # direct script form.
    (["python3", "app.py"], True),
    (["python", "app.py", "x"], True),
    # interpreter flags before -c: NOT offered -- the warm worker cannot
    # reproduce any flag, so the daemon would reject; skip the round trip.
    (["python3", "-I", "-c", "print(1)"], False),
    (["python3", "-S", "-c", "print(1)"], False),
    (["python3", "-E", "-c", "print(1)"], False),
    (["python3", "-u", "-c", "print(1)"], False),
    (["python3", "-B", "-c", "print(1)"], False),
    (["python", "-I", "-c", "print(1)"], False),
    (["python", "-S", "-c", "print(1)"], False),
    # flagged direct script: not the bare -c / .py shape.
    (["python3", "-u", "app.py"], False),
    (["python3", "-m", "http.server"], False),
    (["python3"], False),                 # REPL
    ([], False),
    (["ls", "-l"], False),
    # bash wrapper still offered when it mentions python (daemon is strict).
    (["bash", "-lc", "python app.py"], True),
    (["bash", "-lc", "ls -l"], False),
])
def test_candidate_gate(command, expected):
    assert _candidate(command) is expected, command
