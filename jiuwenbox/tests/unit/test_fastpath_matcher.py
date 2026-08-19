# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Unit tests for the FastPath recogniser.

The recogniser decides whether an exec request may run on the persistent
ForkServer instead of a fresh ``subprocess.Popen``. Converting a command that
is *not* equivalent is a correctness bug that silently changes user-visible
behaviour, so the governing rule is "rather miss a hit than convert one
wrongly". These tests encode both halves of that rule:

  * the shapes that must hit -- the real EDPA wrapper
    ``bash -lc 'cd <dir> && python <file>.py [args]'``, the direct
    ``python <file>.py`` form, and the already-released ``python3 -c CODE``;
  * the much longer list of shapes that must NOT hit -- pipes, redirects,
    ``;``, extra commands, command substitution, variable expansion, env
    prefixes, globs, escapes and backgrounding.

Interpreter resolution and the login-shell probe touch the host, so the
shape-level tests stub them; the guards themselves are tested separately.
"""

# 豁免 G.CLS.11 protected-access：本文件为 FastPath recogniser 的白盒单测，
# 需直接访问 sd._fastpath_plan / sd._fastpath_split_shell / sd._fastpath_interp_path /
# sd._fastpath_login_env_safe / sd._fastpath_shadow_conflict / sd._FASTPATH_PROBE_CACHE
# 等受保护成员；重命名为 public 会破坏封装语义，故仅在本测试侧豁免。
# pylint: disable=protected-access

from __future__ import annotations

import os
import posixpath

import pytest

from jiuwenbox.supervisor import sandbox_daemon as sd


pytestmark = pytest.mark.unit


@pytest.fixture
def stub_host(monkeypatch):
    """Neutralise the host-dependent guards so shapes can be tested anywhere.

    ``_fastpath_interp_path`` (interpreter identity) and
    ``_fastpath_login_env_safe`` (login-shell env neutrality) probe the real
    machine; ``_fastpath_shadow_conflict`` reads the script directory. Each is
    covered by its own test below.
    """
    monkeypatch.setattr(sd, "_fastpath_interp_path",
                        lambda name: posixpath.join("/usr/bin", name))
    monkeypatch.setattr(sd, "_fastpath_login_env_safe", lambda: True)
    monkeypatch.setattr(sd, "_fastpath_shadow_conflict", lambda d: False)


@pytest.fixture
def workdir(tmp_path):
    """A real directory holding ``app.py`` -- ``cd`` targets must exist.

    Returned in POSIX form. The recogniser only ever runs inside the Linux
    sandbox, and its tokeniser rejects backslashes (an escape character in
    shell), so a native Windows path would be rejected for the wrong reason
    while the developer machine runs the tests.
    """
    (tmp_path / "app.py").write_text("print('hi')\n")
    return tmp_path.as_posix()


def _plan(command, workdir=None):
    return sd._fastpath_plan(list(command), {"workdir": workdir})


def _same(a, b):
    """Path equality that tolerates the separator the host normalises to."""
    return os.path.normpath(a) == os.path.normpath(b)


# --------------------------------------------------------------------------
# _fastpath_split_shell: the tokeniser is the whole safety boundary.
# --------------------------------------------------------------------------

def test_split_shell_accepts_the_real_edpa_shape():
    assert sd._fastpath_split_shell('cd "/tmp/w" && python app.py a1 a2') == [
        ["cd", "/tmp/w"],
        ["python", "app.py", "a1", "a2"],
    ]


def test_split_shell_single_quotes_are_literal():
    assert sd._fastpath_split_shell("python app.py 'a b'") == [
        ["python", "app.py", "a b"],
    ]


@pytest.mark.parametrize("payload", [
    # -- shell operators that change what actually runs ---------------------
    "python app.py | tee out.txt",           # pipe
    "python app.py > out.txt",               # redirect out
    "python app.py 2>&1",                    # fd redirect
    "python app.py < in.txt",                # redirect in
    "python app.py; rm -rf /tmp/x",          # sequencing
    "python app.py & ",                      # background
    "python app.py || echo failed",          # or-list
    # (multi-``&&`` payloads tokenise cleanly; they are rejected one level up
    # by the segment-count rule -- see the plan tests.)
    # -- expansion: the value would be computed by the shell, not by us -----
    "python app.py $(date)",                 # command substitution
    "python app.py `date`",                  # legacy substitution
    "python app.py $HOME",                   # variable expansion
    "python app.py ${HOME}",                 # braced expansion
    'python app.py "$HOME"',                 # expansion inside double quotes
    'python app.py "a\\"b"',                 # escape inside double quotes
    "python app.py ~/x",                     # tilde expansion
    "python app.py *.txt",                   # glob
    "python app.py a?.txt",                  # glob
    "python app.py [ab].txt",                # glob
    "python app.py {a,b}",                   # brace expansion
    "python app.py a\\ b",                   # backslash escape
    "python app.py \nrm -rf /tmp/x",         # newline as separator
    "python app.py #comment",                # comment
    "(cd /tmp && python app.py)",            # subshell
    # -- malformed quoting --------------------------------------------------
    "python app.py 'unterminated",
    'python app.py "unterminated',
    # -- degenerate ---------------------------------------------------------
    "&& python app.py",                      # leading separator
    "",
])
def test_split_shell_rejects_everything_outside_the_subset(payload):
    assert sd._fastpath_split_shell(payload) is None, payload


# --------------------------------------------------------------------------
# _fastpath_plan: shapes that MUST hit.
# --------------------------------------------------------------------------

def test_edpa_wrapper_hits_and_carries_the_post_cd_cwd(stub_host, workdir):
    plan = _plan(["bash", "-lc", f'cd "{workdir}" && python app.py a1 a2'],
                 workdir="/")
    assert plan is not None
    assert plan["mode"] == "script"
    assert _same(plan["path"], os.path.join(workdir, "app.py"))
    assert _same(plan["dir"], workdir)
    # argv[0] keeps the token *as written*, matching CPython.
    assert plan["argv"] == ["app.py", "a1", "a2"]
    # The child must run in the post-``cd`` directory, not the request cwd.
    assert _same(plan["cwd"], workdir)
    assert plan["interp_name"] == "python"


def test_edpa_wrapper_reproduces_the_shell_env_delta(stub_host, workdir):
    plan = _plan(["bash", "-lc", f'cd "{workdir}" && python app.py'],
                 workdir="/srv")
    # Exactly the four variables the shell itself would have set -- measured
    # in-sandbox, not assumed.
    assert _same(plan["env_extra"].pop("PWD"), workdir)
    assert _same(plan["env_extra"].pop("OLDPWD"), "/srv")
    assert plan["env_extra"] == {"SHLVL": "0", "_": "/usr/bin/python"}


def test_wrapper_without_cd_uses_the_request_workdir(stub_host, workdir):
    plan = _plan(["bash", "-lc", "python app.py"], workdir=workdir)
    assert plan is not None
    assert _same(plan["cwd"], workdir)
    assert _same(plan["path"], os.path.join(workdir, "app.py"))


def test_direct_script_forms_hit(stub_host, workdir):
    for interp in ("python", "python3"):
        plan = _plan([interp, "app.py", "x"], workdir=workdir)
        assert plan is not None, interp
        assert plan["mode"] == "script"
        assert plan["interp_name"] == interp
        assert plan["argv"] == ["app.py", "x"]
        # A direct invocation has no shell, so no shell env delta.
        assert "env_extra" not in plan


def test_absolute_script_path_hits(stub_host, workdir):
    script = posixpath.join(workdir, "app.py")
    plan = _plan(["python3", script], workdir="/")
    assert _same(plan["path"], script)
    assert _same(plan["dir"], workdir)
    # argv[0] is the absolute token, again as written.
    assert plan["argv"] == [script]


def test_released_dash_c_shape_is_unchanged(stub_host):
    """The ``python3 -c`` path must keep hitting; ``python -c`` now hits too."""
    assert _plan(["python3", "-c", "print(1)"]) == {"mode": "code",
                                                    "code": "print(1)"}
    assert _plan(["python", "-c", "print(1)"]) == {"mode": "code",
                                                   "code": "print(1)"}


def test_dash_c_hits_even_when_script_mode_is_disabled(stub_host, monkeypatch):
    """Script mode is a separate opt-in layered on the released fast path."""
    monkeypatch.setenv(sd.FASTPATH_SCRIPT_ENV, "0")
    assert _plan(["python3", "-c", "print(1)"]) == {"mode": "code",
                                                   "code": "print(1)"}
    assert _plan(["python", "-c", "print(1)"]) == {"mode": "code",
                                                   "code": "print(1)"}
    assert _plan(["python3", "app.py"]) is None
    assert _plan(["python", "app.py"]) is None
    assert _plan(["bash", "-lc", "python app.py"]) is None


# The ForkServer worker is a warm ``python3 -c`` interpreter whose
# ``sys.flags`` and startup are fixed; it cannot reproduce any interpreter
# flag a per-request ``-c`` might carry. Each of these flags changes
# observable ``sys.flags`` (``-I``/``-S``/``-E``/``-B``) or stdout buffering
# (``-u``), so the request must fall back to a fresh ``subprocess`` rather
# than be silently run with the flag dropped. ``-c`` after a script token
# belongs to the script's argv, not to the interpreter, so it must not take
# the code fast path either.
@pytest.mark.parametrize("command", [
    ["python3", "-I", "-c", "print(1)"],
    ["python3", "-S", "-c", "print(1)"],
    ["python3", "-E", "-c", "print(1)"],
    ["python3", "-u", "-c", "print(1)"],
    ["python3", "-B", "-c", "print(1)"],
    ["python3", "-S", "-I", "-c", "print(1)"],
    ["python", "-I", "-c", "print(1)"],
    ["python", "-S", "-c", "print(1)"],
    ["python", "-E", "-c", "print(1)"],
    ["python", "-u", "-c", "print(1)"],
    ["python", "-B", "-c", "print(1)"],
    ["python3", "-c"],                # bare -c, no code
    ["python", "-c"],                 # bare -c, no code
])
def test_flagged_or_non_bare_dash_c_falls_back(stub_host, workdir, command):
    assert _plan(command, workdir=workdir) is None, command


def test_dash_c_as_script_argv_runs_the_script(stub_host, workdir):
    """A ``-c`` after a script token is the script's argv, not the interp's.

    Real ``python3 app.py -c x`` runs ``app.py`` with ``sys.argv`` including
    ``-c``; the previous greedy matcher misread this as ``-c x`` code. The
    bare-``-c`` rule leaves it to script mode, which runs the script.
    """
    plan = _plan(["python3", "app.py", "-c", "x"], workdir=workdir)
    assert plan is not None
    assert plan["mode"] == "script"
    assert plan["argv"] == ["app.py", "-c", "x"]


def test_python_dash_c_requires_identity_match(stub_host, monkeypatch):
    """``python -c`` runs in the python3 worker; only safe if python IS it."""
    # identity matches -> hits (the stub resolves every name to the worker).
    assert _plan(["python", "-c", "print(1)"]) == {"mode": "code",
                                                   "code": "print(1)"}
    # identity mismatch -> must not convert (would run python2/venv code
    # under python3). python3 -c is unaffected: it is the worker itself.
    monkeypatch.setattr(sd, "_fastpath_interp_path", lambda name: None)
    assert _plan(["python", "-c", "print(1)"]) is None
    assert _plan(["python3", "-c", "print(1)"]) == {"mode": "code",
                                                    "code": "print(1)"}


def test_dash_c_with_trailing_args_falls_back(stub_host):
    """Only the exact ``python[3] -c CODE`` shape hits.

    A trailing arg lands in a fresh interpreter's ``sys.argv`` but is dropped by
    the worker's ``-c`` path, so the request must fall back to Popen (which
    preserves ``sys.argv``) rather than be converted wrongly.
    """
    assert _plan(["python3", "-c", "print(1)", "arg1"]) is None
    assert _plan(["python", "-c", "print(1)", "a", "b"]) is None
    # The exact 3-token shape still hits.
    assert _plan(["python3", "-c", "print(1)"]) == {"mode": "code",
                                                    "code": "print(1)"}
    assert _plan(["python", "-c", "print(1)"]) == {"mode": "code",
                                                   "code": "print(1)"}


# --------------------------------------------------------------------------
# _fastpath_plan: shapes that MUST NOT hit.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("command", [
    # -- not a Python invocation at all -------------------------------------
    ["bash", "-lc", "ls -l"],
    ["bash", "-lc", "cd /tmp && ls"],
    ["sh", "-lc", "python app.py"],              # sh, not bash
    ["ls", "-l"],
    # -- env prefix: the shell would export vars we do not model ------------
    ["bash", "-lc", "FOO=1 python app.py"],
    ["bash", "-lc", "env FOO=1 python app.py"],
    ["bash", "-lc", "cd /tmp && FOO=1 python app.py"],
    # -- interpreter flags: not the recognised shape ------------------------
    ["bash", "-lc", "python -u app.py"],
    ["bash", "-lc", "python -m mymod"],
    ["python3", "-u", "app.py"],
    ["python3", "-m", "http.server"],
    # -- not a .py script ---------------------------------------------------
    ["bash", "-lc", "python app"],
    ["python3", "app.sh"],
    ["python3"],                                  # REPL
    # -- interpreter by path: identity not verified -------------------------
    ["bash", "-lc", "/usr/bin/python app.py"],
    ["/usr/bin/python3", "app.py"],
    ["bash", "-lc", "python3.11 app.py"],
    # -- wrapper arity: only the exact 3-token bash form is considered ------
    ["bash", "-c"],
    ["bash", "-lc", "python app.py", "extra"],
    ["bash", "-x", "-lc", "python app.py"],
    # -- prefix command other than a plain ``cd`` ---------------------------
    ["bash", "-lc", "cd /tmp /var && python app.py"],
    ["bash", "-lc", "pushd /tmp && python app.py"],
    ["bash", "-lc", "cd && python app.py"],
    # -- empty --------------------------------------------------------------
    [],
])
def test_plan_rejects_unrecognised_commands(stub_host, workdir, command):
    assert _plan(command, workdir=workdir) is None, command


def test_wrapper_with_nonexistent_cd_target_falls_back(stub_host, workdir):
    """A ``cd`` that would fail must not be silently skipped."""
    missing = posixpath.join(workdir, "does-not-exist")
    assert _plan(["bash", "-lc", f'cd "{missing}" && python app.py']) is None


def test_more_than_one_command_after_cd_falls_back(stub_host, workdir):
    """The payload tokenises, but running only the Python part would drop work."""
    assert _plan(["bash", "-lc",
                  f'cd "{workdir}" && python app.py && echo done']) is None
    assert _plan(["bash", "-lc",
                  f'cd "{workdir}" && cd /tmp && python app.py']) is None


# --------------------------------------------------------------------------
# The guards.
# --------------------------------------------------------------------------

def test_interpreter_identity_mismatch_falls_back(monkeypatch, workdir):
    """If ``python`` on PATH is not the worker interpreter, do not convert."""
    monkeypatch.setattr(sd, "_fastpath_interp_path", lambda name: None)
    monkeypatch.setattr(sd, "_fastpath_login_env_safe", lambda: True)
    monkeypatch.setattr(sd, "_fastpath_shadow_conflict", lambda d: False)
    assert _plan(["bash", "-lc", f'cd "{workdir}" && python app.py']) is None
    assert _plan(["python3", "app.py"], workdir=workdir) is None


def test_login_shell_env_side_effects_disable_the_wrapper(monkeypatch, workdir):
    monkeypatch.setattr(sd, "_fastpath_interp_path",
                        lambda name: posixpath.join("/usr/bin", name))
    monkeypatch.setattr(sd, "_fastpath_shadow_conflict", lambda d: False)
    monkeypatch.setattr(sd, "_fastpath_login_env_safe", lambda: False)
    # ``-lc`` is a login shell: blocked.
    assert _plan(["bash", "-lc", f'cd "{workdir}" && python app.py']) is None
    # ``-c`` is not, so it is unaffected by the login-env verdict.
    assert _plan(["bash", "-c", f'cd "{workdir}" && python app.py']) is not None


def test_shadowing_script_dir_falls_back(monkeypatch, tmp_path):
    """A local ``json.py`` imports differently under a warm worker."""
    monkeypatch.setattr(sd, "_fastpath_interp_path",
                        lambda name: posixpath.join("/usr/bin", name))
    monkeypatch.setattr(sd, "_fastpath_login_env_safe", lambda: True)
    (tmp_path / "app.py").write_text("import json\n")
    (tmp_path / "json.py").write_text("VALUE = 'shadow'\n")
    assert sd._fastpath_shadow_conflict(str(tmp_path)) is True
    assert _plan(["python3", "app.py"], workdir=tmp_path.as_posix()) is None


def test_shadow_conflict_detects_package_directories(tmp_path):
    pkg = tmp_path / "json"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    assert sd._fastpath_shadow_conflict(str(tmp_path)) is True


def test_shadow_conflict_allows_ordinary_script_dirs(tmp_path):
    (tmp_path / "app.py").write_text("")
    (tmp_path / "helpers.py").write_text("")
    (tmp_path / "data.txt").write_text("")
    assert sd._fastpath_shadow_conflict(str(tmp_path)) is False


def test_shadow_conflict_on_unreadable_dir_is_conservative():
    """Cannot verify -> must not convert."""
    assert sd._fastpath_shadow_conflict("/nonexistent/dir/xyz") is True


# --------------------------------------------------------------------------
# The login-env probe itself. Every test above stubs it, so it needs its own
# coverage -- a bug here disables the wrapper fast path silently, in
# production only, while every fresh-process replay reports "safe".
# --------------------------------------------------------------------------

@pytest.fixture
def _clear_probe_cache():
    """The probe memoises its verdict; each test needs a fresh one."""
    sd._FASTPATH_PROBE_CACHE.pop("login_env_safe", None)
    yield
    sd._FASTPATH_PROBE_CACHE.pop("login_env_safe", None)


def _fake_probe(monkeypatch, login_env, returncode=0):
    """Make the probe subprocess return ``login_env`` without running bash."""
    import json as _json
    import subprocess as _sp

    def fake_run(*_a, **_kw):
        return _sp.CompletedProcess(
            args=[], returncode=returncode,
            stdout=_json.dumps(login_env).encode(), stderr=b"")

    monkeypatch.setattr(sd.subprocess, "run", fake_run)


def test_login_env_probe_ignores_the_daemon_listener_fd(
        monkeypatch, _clear_probe_cache):
    """The listener fd var is daemon plumbing, not a login-shell side effect.

    The daemon always has ``JIUWENBOX_CONTROL_LISTENER_FD`` set (bwrap injects
    it), so ``bash -lc`` inherits and re-exports it. Counting that as an
    env difference would permanently disable the wrapper fast path in every
    real deployment while looking fine in any fresh-process replay.
    """
    monkeypatch.setattr(os, "environ",
                        {"PATH": "/usr/bin", sd.LISTENER_FD_ENV: "16"})
    # The login shell reports the same env, listener fd included.
    _fake_probe(monkeypatch, {"PATH": "/usr/bin", sd.LISTENER_FD_ENV: "16"})
    assert sd._fastpath_login_env_safe() is True


def test_login_env_probe_allows_the_shell_own_variables(
        monkeypatch, _clear_probe_cache):
    """``PWD``/``OLDPWD``/``SHLVL``/``_`` are set by the shell, and modelled."""
    monkeypatch.setattr(os, "environ", {"PATH": "/usr/bin"})
    _fake_probe(monkeypatch, {"PATH": "/usr/bin", "PWD": "/", "SHLVL": "1",
                              "OLDPWD": "/x", "_": "/usr/bin/python3"})
    assert sd._fastpath_login_env_safe() is True


def test_login_env_probe_rejects_a_profile_that_exports(
        monkeypatch, _clear_probe_cache):
    """A real ``/etc/profile.d`` export makes the wrapper non-equivalent."""
    monkeypatch.setattr(os, "environ", {"PATH": "/usr/bin"})
    _fake_probe(monkeypatch, {"PATH": "/usr/bin", "PROXY": "http://x"})
    assert sd._fastpath_login_env_safe() is False


def test_login_env_probe_rejects_a_modified_value(
        monkeypatch, _clear_probe_cache):
    """A profile that *edits* PATH is just as disqualifying as a new var."""
    monkeypatch.setattr(os, "environ", {"PATH": "/usr/bin"})
    _fake_probe(monkeypatch, {"PATH": "/opt/bin:/usr/bin"})
    assert sd._fastpath_login_env_safe() is False


def test_login_env_probe_failure_is_conservative(
        monkeypatch, _clear_probe_cache):
    """A probe that cannot run must not be read as "safe"."""
    monkeypatch.setattr(os, "environ", {"PATH": "/usr/bin"})
    _fake_probe(monkeypatch, {}, returncode=1)
    assert sd._fastpath_login_env_safe() is False
