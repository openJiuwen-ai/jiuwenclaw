# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""连接器 CLI 必须走 sandbox.excluded_commands，在宿主进程执行。"""

import json
import os
from pathlib import Path

from jiuwenswarm.common.config import (
    _ensure_sandbox_runtime_shape,
    merge_connector_excluded_commands,
)
from jiuwenswarm.common.connectors import (
    bundled_cli_config_path,
    collect_connector_cli_names,
    command_uses_connector_cli,
    connector_cli_registry,
    connector_excluded_commands,
    connector_host_argv,
    expand_cli_exclude_globs,
    expand_connector_cli_tokens,
    is_connector_exclude_glob,
    resolve_connector_executable,
)
from jiuwenswarm.common.host_shell import (
    EMPTY_HOST_SUCCESS,
    decode_cli_bytes,
    format_host_cmd_output,
    host_environ,
    host_shell_argv,
)


def test_expand_cli_covers_bare_exe_and_path_forms() -> None:
    globs = expand_cli_exclude_globs("dws")
    assert "dws" in globs
    assert "dws *" in globs
    assert "dws.exe" in globs
    assert "*\\dws.exe *" in globs
    assert "*/dws *" in globs


def test_scan_cli_names_json_picks_up_new_connector(tmp_path: Path) -> None:
    feishu = tmp_path / "feishu"
    feishu.mkdir()
    (feishu / "cli-names.json").write_text('["lark", "lark-cli"]', encoding="utf-8")
    (feishu / "lark.exe").write_bytes(b"fake")

    names = collect_connector_cli_names(connectors_dir=tmp_path)
    assert "dws" in names  # connect_cli_config.json 兜底
    assert "lark" in names
    assert "lark-cli" in names

    globs = connector_excluded_commands(connectors_dir=tmp_path)
    assert "lark *" in globs
    assert "lark-cli.exe" in globs
    assert is_connector_exclude_glob("lark *", connectors_dir=tmp_path)
    assert not is_connector_exclude_glob("curl *", connectors_dir=tmp_path)


def test_merge_prepends_connector_globs_and_dedupes() -> None:
    builtins = connector_excluded_commands()
    out = merge_connector_excluded_commands(["git *", "dws *", "curl *"])
    assert out[: len(builtins)] == list(builtins)
    assert out.count("dws *") == 1
    assert out.index("git *") > out.index("dws *")
    assert "curl *" in out


def test_ensure_shape_injects_connector_globs_when_yaml_empty() -> None:
    shaped = _ensure_sandbox_runtime_shape({"enabled": True, "excluded_commands": []})
    for glob in connector_excluded_commands():
        assert glob in shaped["excluded_commands"]


def test_ensure_shape_none_runtime_still_has_connector_globs() -> None:
    shaped = _ensure_sandbox_runtime_shape(None)
    assert "dws" in shaped["excluded_commands"]
    assert "dws *" in shaped["excluded_commands"]
    assert "*\\dws.exe *" in shaped["excluded_commands"]
    assert "lark-cli *" in shaped["excluded_commands"]
    assert "wecom-cli *" in shaped["excluded_commands"]


def test_host_argv_resolves_dws_and_keeps_quoted_args(tmp_path: Path) -> None:
    dingtalk = tmp_path / "dingtalk"
    dingtalk.mkdir()
    exe = dingtalk / "dws.exe"
    exe.write_bytes(b"fake")

    cmd = (
        'dws calendar event create --title "吃饭" '
        '--start "2026-08-24T16:00:00+08:00" '
        '--end "2026-08-24T17:00:00+08:00" --format json'
    )
    argv = connector_host_argv(cmd, connectors_dir=tmp_path)
    assert argv is not None
    assert argv[0] == str(exe)
    assert argv[1:4] == ["calendar", "event", "create"]
    assert argv[argv.index("--title") + 1] == "吃饭"
    assert argv[argv.index("--start") + 1] == "2026-08-24T16:00:00+08:00"
    assert argv[-2:] == ["--format", "json"]


def test_host_argv_skips_pipelines(tmp_path: Path) -> None:
    dingtalk = tmp_path / "dingtalk"
    dingtalk.mkdir()
    (dingtalk / "dws.exe").write_bytes(b"fake")
    assert connector_host_argv("dws calendar +today | jq .", connectors_dir=tmp_path) is None
    assert connector_host_argv("curl https://example.com", connectors_dir=tmp_path) is None


def test_connector_cli_never_routed_to_sandbox(tmp_path: Path) -> None:
    dingtalk = tmp_path / "dingtalk"
    dingtalk.mkdir()
    exe = dingtalk / "dws.exe"
    exe.write_bytes(b"fake")

    simple = host_shell_argv(
        "dws calendar +tomorrow --format json",
        shell_type="auto",
        connectors_dir=tmp_path,
    )
    assert simple == [str(exe), "calendar", "+tomorrow", "--format", "json"]

    pipeline = host_shell_argv(
        "dws calendar +today | jq .",
        shell_type="auto",
        connectors_dir=tmp_path,
    )
    assert pipeline is not None
    script = pipeline[-1]
    assert str(exe) in script
    assert "jq" in script
    if os.name == "nt":
        assert "powershell" in Path(pipeline[0]).name.lower()

    probe = host_shell_argv(
        "Get-Command dws -ErrorAction SilentlyContinue",
        shell_type="auto",
        connectors_dir=tmp_path,
    )
    assert probe is not None
    assert str(exe) in probe[-1]
    assert command_uses_connector_cli(
        "Get-Command dws -ErrorAction SilentlyContinue",
        connectors_dir=tmp_path,
    )
    assert not command_uses_connector_cli("curl https://example.com", connectors_dir=tmp_path)
    assert host_shell_argv("ls -la", shell_type="auto", connectors_dir=tmp_path) is None


def test_unresolved_dws_still_stays_on_host(tmp_path: Path) -> None:
    argv = host_shell_argv("dws --version", shell_type="bash", connectors_dir=tmp_path)
    assert argv is not None
    assert argv[-1].endswith("dws --version")


def test_resolve_prefers_connectors_dir_over_missing_path(tmp_path: Path) -> None:
    feishu = tmp_path / "feishu"
    feishu.mkdir()
    exe = feishu / "lark-cli.exe"
    exe.write_bytes(b"fake")
    found = resolve_connector_executable("lark-cli", connectors_dir=tmp_path)
    assert found == exe


def test_host_shell_argv_runs_powershell_not_bash() -> None:
    cmd = (
        "Get-Command dws -ErrorAction SilentlyContinue | "
        "Select-Object -ExpandProperty Source"
    )
    argv = host_shell_argv(cmd, shell_type="powershell")
    assert argv is not None
    assert argv[0].lower().endswith("powershell.exe") or argv[0] in {"powershell", "pwsh"}
    assert argv[1:4] == ["-NoProfile", "-NonInteractive", "-Command"]
    assert "Get-Command" in argv[4]
    assert host_shell_argv("ls -la", shell_type="auto") is None
    assert host_shell_argv("$HOME", shell_type="bash") is None


def test_bundled_connect_cli_config_registers_builtin_commands() -> None:
    path = bundled_cli_config_path()
    assert path.is_file()
    data = json.loads(path.read_text(encoding="utf-8"))
    bundled = []
    for item in data["connectors"]:
        bundled.extend(item["commands"])
    assert bundled == ["dws", "lark-cli", "wecom-cli"]
    names = collect_connector_cli_names()
    assert "dws" in names
    assert "lark-cli" in names
    assert "wecom-cli" in names
    ids = {spec.id for spec in connector_cli_registry()}
    assert ids >= {"dingtalk", "feishu", "wecom"}


def test_overlay_connect_cli_config_adds_command_without_python_change(
    tmp_path: Path,
) -> None:
    (tmp_path / "connect_cli_config.json").write_text(
        json.dumps({"cli": ["acme-cli"]}),
        encoding="utf-8",
    )
    names = collect_connector_cli_names(connectors_dir=tmp_path)
    assert "acme-cli" in names
    assert "dws" in names


def test_env_connect_cli_config_adds_connector_type(tmp_path: Path, monkeypatch) -> None:
    cfg = tmp_path / "extra.json"
    cfg.write_text(
        json.dumps(
            {"connectors": [{"id": "acme", "commands": ["acme-cli", "acme"]}]}
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("JIUWENSWARM_CONNECTOR_CLI_CONFIG", str(cfg))
    names = collect_connector_cli_names(connectors_dir=tmp_path)
    assert "acme-cli" in names
    assert "acme" in names
    assert "dws" in names
    ids = {spec.id for spec in connector_cli_registry(connectors_dir=tmp_path)}
    assert "acme" in ids


def test_config_only_cli_is_host_routed(tmp_path: Path) -> None:
    (tmp_path / "connect_cli_config.json").write_text(
        json.dumps({"cli": ["acme-cli"]}),
        encoding="utf-8",
    )
    acme = tmp_path / "acme"
    acme.mkdir()
    exe = acme / "acme-cli.exe"
    exe.write_bytes(b"fake")
    argv = connector_host_argv("acme-cli ping", connectors_dir=tmp_path)
    assert argv == [str(exe), "ping"]


def test_resolves_dws_from_sibling_of_jiuwenswarm_data_dir(
    tmp_path: Path, monkeypatch
) -> None:
    data = tmp_path / "jiuwenswarm"
    data.mkdir()
    exe = tmp_path / "connectors" / "dingtalk" / "dws.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"fake")
    monkeypatch.delenv("JIUWENSWARM_CONNECTORS_DIR", raising=False)
    monkeypatch.delenv("CLAW_CONNECTORS_DIR", raising=False)
    monkeypatch.setenv("JIUWENSWARM_DATA_DIR", str(data))
    found = resolve_connector_executable("dws")
    assert found == exe
    argv = connector_host_argv("dws calendar +tomorrow --format json")
    assert argv == [str(exe), "calendar", "+tomorrow", "--format", "json"]


def test_resolves_dws_from_appdata_claw_desktop(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("JIUWENSWARM_CONNECTORS_DIR", raising=False)
    monkeypatch.delenv("CLAW_CONNECTORS_DIR", raising=False)
    monkeypatch.delenv("JIUWENSWARM_DATA_DIR", raising=False)
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    exe = tmp_path / "claw-desktop" / "connectors" / "dingtalk" / "dws.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"fake")
    found = resolve_connector_executable("dws")
    assert found == exe


def test_host_environ_prepends_connector_dir(tmp_path: Path, monkeypatch) -> None:
    dingtalk = tmp_path / "dingtalk"
    dingtalk.mkdir()
    (dingtalk / "dws.exe").write_bytes(b"fake")
    monkeypatch.setenv("JIUWENSWARM_CONNECTORS_DIR", str(tmp_path))
    env = host_environ()
    parts = env["PATH"].split(os.pathsep)
    assert str(dingtalk) in parts
    assert parts[0] in {str(tmp_path), str(dingtalk)}


def test_expand_connector_cli_tokens_replaces_dws_with_exe(tmp_path: Path) -> None:
    dingtalk = tmp_path / "dingtalk"
    dingtalk.mkdir()
    exe = dingtalk / "dws.exe"
    exe.write_bytes(b"fake")
    out = expand_connector_cli_tokens(
        'dws calendar event create --title "学习/工作事项" | jq .',
        connectors_dir=tmp_path,
    )
    assert str(exe) in out
    assert "学习/工作事项" in out
    assert "jq" in out


def test_decode_cli_bytes_keeps_utf8_json_title() -> None:
    payload = '{"title":"学习/工作事项","ok":true}'.encode("utf-8")
    assert "学习/工作事项" in decode_cli_bytes(payload)


def test_format_host_output_promotes_stderr_and_empty_success() -> None:
    out, err = format_host_cmd_output("", '{"id":"evt-1"}', 0)
    assert out == '{"id":"evt-1"}'
    assert err == ""
    empty, _ = format_host_cmd_output("", "", 0)
    assert empty == EMPTY_HOST_SUCCESS
    assert "do not retry" in empty


def test_wecom_cli_host_argv(tmp_path: Path) -> None:
    wecom = tmp_path / "wecom"
    wecom.mkdir()
    exe = wecom / "wecom-cli.exe"
    exe.write_bytes(b"fake")
    assert resolve_connector_executable("wecom-cli", connectors_dir=tmp_path) == exe
    argv = connector_host_argv("wecom-cli --help", connectors_dir=tmp_path)
    assert argv == [str(exe), "--help"]
    wrapped = host_shell_argv("wecom-cli --help", shell_type="bash", connectors_dir=tmp_path)
    assert wrapped == [str(exe), "--help"]


def test_which_or_where_probe_stays_on_host_posix_shell(
    tmp_path: Path, monkeypatch
) -> None:
    feishu = tmp_path / "feishu"
    feishu.mkdir()
    (feishu / "lark-cli.exe").write_bytes(b"fake")
    monkeypatch.setattr(
        "jiuwenswarm.common.host_shell.host_bash_exe",
        lambda: r"C:\Program Files\Git\bin\bash.exe",
    )
    cmd = 'which lark-cli 2>/dev/null || where lark-cli 2>/dev/null || echo "not found"'
    assert command_uses_connector_cli(cmd, connectors_dir=tmp_path)
    assert connector_host_argv(cmd, connectors_dir=tmp_path) is None
    argv = host_shell_argv(cmd, shell_type="bash", connectors_dir=tmp_path)
    assert argv is not None
    assert "bash" in Path(argv[0]).name.lower()
    assert argv[1] in {"-c", "-lc"}
    assert argv[-1] == cmd
    if os.name == "nt":
        assert argv[1] == "-c"
        assert "powershell" not in Path(argv[0]).name.lower()
    pipeline = host_shell_argv(
        "lark-cli calendar +today | jq .",
        shell_type="auto",
        connectors_dir=tmp_path,
    )
    assert pipeline is not None
    assert command_uses_connector_cli(
        "lark-cli calendar +today | jq .", connectors_dir=tmp_path
    )
