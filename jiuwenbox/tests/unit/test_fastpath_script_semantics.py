# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Differential tests for the Phase 6B FastPath script child.

Running a script on the fast path means ``exec``-ing it inside an
already-warm forked interpreter instead of spawning ``python3 script.py``.
That is only acceptable if the script cannot tell the difference, so these
tests do not assert hand-written expectations -- they run the *same script*
both ways on the same machine and compare:

    real CPython:  subprocess.run([sys.executable, "script.py", ...])
    fast path:     worker ``_run_child(..., plan=...)``

A hand-written expectation would encode my belief about CPython; a
differential comparison encodes CPython itself. The properties covered are
the ones a real script can observe: ``__name__``, ``__file__``, ``sys.argv``,
``sys.path[0]``, cwd, env, stdout/stderr, exit status (0, non-zero,
``SystemExit`` with a string), traceback text including the filename, and
same-directory imports.

These tests require ``os.fork`` and so run on POSIX only -- which is where
the daemon runs. Run them with ``pytest -s``: the forked child writes to fd 1
and fd 2 directly, which pytest's default fd-level capture intercepts, so
``_run_child`` would read back empty pipes.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from dataclasses import dataclass

import pytest

from jiuwenbox.supervisor.sandbox_daemon import FORKSERVER_WORKER_SOURCE


pytestmark = pytest.mark.unit

_SKIP_NON_POSIX = pytest.mark.skipif(
    not hasattr(os, "fork"),
    reason="the FastPath child requires os.fork (POSIX)",
)


def _load_worker_ns() -> dict:
    ns: dict = {}
    exec(compile(FORKSERVER_WORKER_SOURCE, "<worker_src>", "exec"), ns)
    return ns


@dataclass(frozen=True)
class _ExecOpts:
    """Per-call options shared by the differential-test helpers.

    G.FNM.03 keeps ``_fastpath``/``_real`` at or below five arguments: the
    optional correlated inputs travel as a single value object. ``interp_name``
    is read by ``_fastpath`` only, ``interp`` by ``_real`` only.
    """

    args: tuple = ()
    cwd: str | None = None
    env_extra: dict | None = None
    timeout: float = 30.0
    interp_name: str = "python3"
    interp: str | None = None


def _fastpath(script: str, opts: _ExecOpts | None = None):
    """Run ``script`` through the FastPath child; return (rc, out, err)."""
    opts = opts or _ExecOpts()
    ns = _load_worker_ns()
    plan = {
        "mode": "script",
        "path": os.path.abspath(script),
        "dir": os.path.dirname(os.path.abspath(script)),
        "argv": [script] + list(opts.args),
        "cwd": opts.cwd or os.path.dirname(os.path.abspath(script)),
        "interp_name": opts.interp_name,
        "interp_path": sys.executable,
    }
    if opts.env_extra:
        plan["env_extra"] = opts.env_extra
    # Flush before the fork so the child does not re-emit pytest's captured
    # output from an inherited buffer.
    sys.stdout.flush()
    sys.stderr.flush()
    r, w = os.pipe()
    try:
        rc, out, err = ns["_run_child"](
            None, b"", plan["cwd"], {}, opts.timeout, r, plan)
    finally:
        for fd in (r, w):
            try:
                os.close(fd)
            except OSError:
                pass
    return rc, out.decode(), err.decode()


def _real(script: str, opts: _ExecOpts | None = None):
    """Run the same script under a real interpreter; return (rc, out, err).

    ``interp`` defaults to ``sys.executable``. Pass a bare name ("python3")
    to compare against the interpreter *as the daemon sees it invoked* --
    CPython echoes argv[0] verbatim in its "can't open file" message, so the
    spelling matters for that one comparison.
    """
    opts = opts or _ExecOpts()
    env = dict(os.environ)
    if opts.env_extra:
        env.update(opts.env_extra)
    proc = subprocess.run(
        [opts.interp or sys.executable, script] + list(opts.args),
        cwd=opts.cwd or os.path.dirname(os.path.abspath(script)),
        env=env, capture_output=True, timeout=opts.timeout,
    )
    return proc.returncode, proc.stdout.decode(), proc.stderr.decode()


def _both(script, **kw):
    """Run both ways and return (fastpath_result, real_result)."""
    opts = _ExecOpts(**kw)
    return _fastpath(script, opts), _real(script, opts)


def _write(tmp_path, name, body):
    path = tmp_path / name
    path.write_text(textwrap.dedent(body))
    return str(path)


# The probe script reports everything a script can observe about how it was
# started. Both execution modes must produce byte-identical JSON.
_PROBE = """
    import json, os, sys

    print(json.dumps({
        "name": __name__,
        "file": __file__,
        "file_is_abs": os.path.isabs(__file__),
        "argv": sys.argv,
        "path0": sys.path[0],
        "cwd": os.getcwd(),
        "spec_is_none": __spec__ is None,
        "package": __package__,
        "loader": type(__loader__).__name__,
        "doc": __doc__,
        "main_mod_file": sys.modules["__main__"].__file__,
        "main_mod_is_self": sys.modules["__main__"].__dict__ is globals(),
    }, sort_keys=True))
    """


@_SKIP_NON_POSIX
def test_core_semantics_match_real_cpython(tmp_path):
    """``__name__``/``__file__``/argv/``sys.path[0]``/cwd/``__main__`` identity."""
    script = _write(tmp_path, "probe.py", _PROBE)
    (fp_rc, fp_out, fp_err), (rl_rc, rl_out, rl_err) = _both(
        script, args=["a1", "a2"])

    assert fp_rc == rl_rc == 0, (fp_err, rl_err)
    fp, rl = json.loads(fp_out), json.loads(rl_out)
    assert fp == rl, f"fastpath={fp}\nreal    ={rl}"
    # Spelled out, so a future regression names the property it broke.
    assert fp["name"] == "__main__"
    assert fp["file_is_abs"] is True
    assert fp["argv"] == [script, "a1", "a2"]
    assert fp["path0"] == str(tmp_path)
    assert fp["cwd"] == str(tmp_path)
    assert fp["spec_is_none"] is True
    assert fp["package"] is None
    assert fp["loader"] == "SourceFileLoader"
    assert fp["main_mod_is_self"] is True


@_SKIP_NON_POSIX
def test_relative_argv0_is_preserved_verbatim(tmp_path):
    """``sys.argv[0]`` is the token as written, while ``__file__`` is absolute.

    These genuinely differ under CPython when the script is named relatively,
    which is exactly how the real EDPA wrapper invokes it
    (``cd <dir> && python app.py``).
    """
    _write(tmp_path, "probe.py", _PROBE)
    cwd = str(tmp_path)
    ns = _load_worker_ns()
    plan = {
        "mode": "script", "path": os.path.join(cwd, "probe.py"), "dir": cwd,
        "argv": ["probe.py", "x"], "cwd": cwd,
        "interp_name": "python3", "interp_path": sys.executable,
    }
    sys.stdout.flush()
    sys.stderr.flush()
    r, _w = os.pipe()
    try:
        rc, out, err = ns["_run_child"](None, b"", cwd, {}, 30.0, r, plan)
    finally:
        os.close(r)
        os.close(_w)
    assert rc == 0, err
    fp = json.loads(out.decode())

    rl_rc, rl_out, rl_err = _real("probe.py", _ExecOpts(args=["x"], cwd=cwd))
    assert rl_rc == 0, rl_err
    rl = json.loads(rl_out)

    assert fp == rl
    assert fp["argv"] == ["probe.py", "x"]
    assert fp["file"] == os.path.join(cwd, "probe.py")


@_SKIP_NON_POSIX
def test_same_directory_import_works(tmp_path):
    """``sys.path[0]`` must make sibling modules importable."""
    _write(tmp_path, "helper.py", "VALUE = 'from-helper'\n")
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("NAME = 'pkg'\n")
    script = _write(tmp_path, "imp.py", """
        import helper
        import pkg
        print(helper.VALUE, pkg.NAME, helper.__file__)
        """)
    assert _fastpath(script) == _real(script)
    rc, out, _ = _fastpath(script)
    assert rc == 0
    assert out.startswith("from-helper pkg ")


@_SKIP_NON_POSIX
def test_stdout_and_stderr_interleaving_and_exit_zero(tmp_path):
    script = _write(tmp_path, "io.py", """
        import sys
        sys.stdout.write("out-1\\n")
        sys.stderr.write("err-1\\n")
        print("out-2")
        print("err-2", file=sys.stderr)
        """)
    assert _fastpath(script) == _real(script)
    rc, out, err = _fastpath(script)
    assert rc == 0
    assert out == "out-1\nout-2\n"
    assert err == "err-1\nerr-2\n"


@_SKIP_NON_POSIX
def test_explicit_nonzero_exit_code(tmp_path):
    script = _write(tmp_path, "exit7.py", """
        import sys
        print("before")
        sys.exit(7)
        """)
    assert _fastpath(script) == _real(script)
    assert _fastpath(script)[0] == 7


@_SKIP_NON_POSIX
def test_systemexit_with_string_message(tmp_path):
    """CPython prints a string exit code to stderr and exits 1."""
    script = _write(tmp_path, "exitstr.py", """
        import sys
        sys.exit("fatal: bad input")
        """)
    assert _fastpath(script) == _real(script)
    rc, _out, err = _fastpath(script)
    assert rc == 1
    assert err == "fatal: bad input\n"


@_SKIP_NON_POSIX
def test_uncaught_exception_traceback_matches(tmp_path):
    """The traceback must name the script and must NOT leak worker frames."""
    script = _write(tmp_path, "boom.py", """
        def inner():
            raise ValueError("boom")

        def outer():
            inner()

        outer()
        """)
    (fp_rc, fp_out, fp_err), (rl_rc, rl_out, rl_err) = _both(script)
    assert fp_rc == rl_rc == 1
    assert fp_out == rl_out == ""
    assert fp_err == rl_err, f"fastpath:\n{fp_err}\nreal:\n{rl_err}"
    assert fp_err.startswith("Traceback (most recent call last):")
    assert script in fp_err
    assert 'ValueError: boom' in fp_err
    # No trace of the fast-path machinery.
    for leak in ("_exec_script_in_child", "_run_child", "<worker_src>",
                 "<fastpath>", "sandbox_daemon"):
        assert leak not in fp_err, leak


@_SKIP_NON_POSIX
def test_syntax_error_matches(tmp_path):
    script = _write(tmp_path, "bad.py", "def f(:\n    pass\n")
    (fp_rc, _fp_out, fp_err), (rl_rc, _rl_out, rl_err) = _both(script)
    assert fp_rc == rl_rc == 1
    assert fp_err == rl_err, f"fastpath:\n{fp_err}\nreal:\n{rl_err}"
    assert "SyntaxError" in fp_err


@_SKIP_NON_POSIX
def test_env_extra_is_visible_to_the_script(tmp_path):
    """The wrapper's shell env delta must be observable, as under bash."""
    script = _write(tmp_path, "env.py", """
        import os
        print(os.environ.get("PWD"), os.environ.get("SHLVL"),
              os.environ.get("MY_MARKER"))
        """)
    extra = {"PWD": str(tmp_path), "SHLVL": "0", "MY_MARKER": "marker-1"}
    assert _fastpath(script, _ExecOpts(env_extra=extra)) == _real(script, _ExecOpts(env_extra=extra))
    rc, out, _ = _fastpath(script, _ExecOpts(env_extra=extra))
    assert rc == 0
    assert out == f"{tmp_path} 0 marker-1\n"


@_SKIP_NON_POSIX
def test_missing_script_reports_like_cpython(tmp_path):
    """CPython echoes argv[0] verbatim, so compare against the bare name.

    The recogniser only ever accepts a bare ``python``/``python3`` token (an
    interpreter given by path is rejected), so the bare-name invocation is
    the one the fast path has to match.
    """
    missing = str(tmp_path / "nope.py")
    fp_rc, _fp_out, fp_err = _fastpath(missing, _ExecOpts(cwd=str(tmp_path)))
    rl_rc, _rl_out, rl_err = _real(missing, _ExecOpts(cwd=str(tmp_path),
                                   interp="python3"))
    assert fp_rc == rl_rc == 2
    assert fp_err == rl_err, f"fastpath:\n{fp_err}\nreal:\n{rl_err}"


@_SKIP_NON_POSIX
def test_missing_script_uses_the_interpreter_name_as_invoked(tmp_path):
    """``python`` and ``python3`` prefix the message differently."""
    missing = str(tmp_path / "nope.py")
    _rc, _out, err = _fastpath(missing, _ExecOpts(cwd=str(tmp_path),
                               interp_name="python"))
    assert err.startswith("python: can't open file")


@_SKIP_NON_POSIX
def test_timeout_still_returns_124(tmp_path):
    """Script mode reuses the existing timeout path."""
    script = _write(tmp_path, "sleep.py", "import time; time.sleep(30)\n")
    rc, _out, _err = _fastpath(script, _ExecOpts(timeout=1.0))
    assert rc == 124


@_SKIP_NON_POSIX
def test_script_mode_does_not_disturb_the_worker(tmp_path):
    """Consecutive requests must be independent (the child is a fork)."""
    script = _write(tmp_path, "mutate.py", """
        import sys
        sys.argv.append("mutated")
        sys.path.insert(0, "/injected")
        print(len(sys.argv), sys.path[0])
        """)
    ns = _load_worker_ns()
    before_argv, before_path0 = list(sys.argv), sys.path[0]
    for _ in range(3):
        plan = {
            "mode": "script", "path": os.path.abspath(script),
            "dir": str(tmp_path), "argv": [script], "cwd": str(tmp_path),
            "interp_name": "python3", "interp_path": sys.executable,
        }
        sys.stdout.flush()
        sys.stderr.flush()
        r, w = os.pipe()
        try:
            rc, out, err = ns["_run_child"](
                None, b"", str(tmp_path), {}, 30.0, r, plan)
        finally:
            os.close(r)
            os.close(w)
        assert rc == 0, err
        # Same result every time: no state leaked from the previous child.
        assert out.decode() == "2 /injected\n"
    # And nothing leaked back into this process either.
    assert sys.argv == before_argv
    assert sys.path[0] == before_path0
