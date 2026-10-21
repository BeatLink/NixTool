"""Tests for the nixtool command line interface."""

import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from nixtool import commands as commands_module
from nixtool import generations
from nixtool import registry, resolver, secrets
from nixtool.cli import EXIT_CONFIRM, EXIT_ERROR, EXIT_OK, EXIT_USAGE, main


@pytest.fixture
def config_file(tmp_path, monkeypatch):
    path = tmp_path / "nixtool-config.json"
    path.write_text(json.dumps({
        "flake_path": str(tmp_path / "flake"),
        "user": "admin",
        "hosts": {"alpha": "10.0.0.1", "beta": "10.0.0.2"},
    }))
    monkeypatch.setenv("NIXTOOL_CONFIG", str(path))
    return path


# --- dconf export ---------------------------------------------------------

def _dconf_flake(tmp_path, settings):
    """A flake tree holding one dconf-settings.json."""
    app = tmp_path / "flake" / "nix" / "app"
    app.mkdir(parents=True)
    (app / "dconf-settings.json").write_text(json.dumps(settings))
    return {
        "flake_path": str(tmp_path / "flake"),
        "user": "admin",
        "hosts": {"alpha": "10.0.0.1"},
    }


def test_dconf_export_without_a_host_dumps_locally(tmp_path):
    config = _dconf_flake(tmp_path, {"dconf_exports": ["/org/example/"]})
    commands = commands_module.get_dconf_commands(config)
    assert commands == [
        "dconf dump /org/example/ > ./nix/app/org.example.dconf"
    ]


def test_dconf_export_with_a_host_dumps_over_ssh_but_writes_locally(tmp_path):
    config = _dconf_flake(
        tmp_path, {"host": "alpha", "dconf_exports": ["/org/example/"]}
    )
    commands = commands_module.get_dconf_commands(config)
    # The redirect stays outside the ssh command, so the file lands in the flake
    # on this machine rather than on the remote host.
    assert commands == [
        "ssh admin@10.0.0.1 dconf dump /org/example/ > ./nix/app/org.example.dconf"
    ]


def test_dconf_export_with_an_unknown_host_does_not_fall_back_to_local(tmp_path):
    config = _dconf_flake(
        tmp_path, {"host": "nowhere", "dconf_exports": ["/org/example/"]}
    )
    commands = commands_module.get_dconf_commands(config)
    # Falling back to a local dump would overwrite a good export with this
    # machine's empty one, which is the failure this whole feature exists for.
    assert len(commands) == 1
    assert commands[0].startswith("echo ")
    assert "nowhere" in commands[0]
    assert "dconf dump" not in commands[0]


def test_dconf_export_pipes_through_the_filter_only_for_ignored_keys(tmp_path):
    config = _dconf_flake(
        tmp_path,
        {
            "dconf_exports": ["/org/example/", "/org/other/"],
            "dconf_ignore": ["/org/example/dialog/window-size"],
        },
    )
    filtered, plain = commands_module.get_dconf_commands(config)
    assert filtered.startswith("set -o pipefail; dconf dump /org/example/ | ")
    assert filtered.endswith(
        "dconf_filter.py /org/example/ /org/example/dialog/window-size"
        " > ./nix/app/org.example.dconf"
    )
    assert plain == "dconf dump /org/other/ > ./nix/app/org.other.dconf"


def test_dconf_filter_drops_keys_directories_and_emptied_sections():
    from nixtool.dconf_filter import filter_dump

    dump = (
        "[/]\ntop=1\n\n"
        "[dialog]\nshow-hidden=true\nwindow-size=(1915, 952)\n\n"
        "[cache]\nlast=2\n\n"
        "[cache/deep]\nmore=3\n\n"
        "[sizes]\nwindow-size=(1, 1)\n"
    )
    ignored = [
        "/org/example/dialog/window-size",
        "/org/example/cache/",
        "/org/example/sizes/window-size",
    ]
    assert filter_dump(dump, "/org/example/", ignored) == (
        "[/]\ntop=1\n\n[dialog]\nshow-hidden=true\n"
    )


def test_dconf_filter_runs_as_a_script(tmp_path):
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, str(commands_module.FILTER_SCRIPT), "/org/example/",
         "/org/example/a/b"],
        input="[a]\nb=1\nc=2\n", capture_output=True, text=True, check=True,
    )
    assert result.stdout == "[a]\nc=2\n"


def test_dconf_export_reports_missing_flake_path():
    assert commands_module.get_dconf_commands({}) == [
        "echo 'No flake_path configured; cannot locate dconf targets.'"
    ]


# --- registry -------------------------------------------------------------

def test_every_command_has_a_unique_qualified_id():
    ids = [registry.qualified_id(p, n) for p, n in registry.iter_commands()]
    assert len(ids) == len(set(ids))


def test_every_command_is_reachable_from_a_category():
    """Guards against a command existing only as a nested step (see garbage-collect)."""
    reachable = {registry.qualified_id(p, n) for p, n in registry.iter_commands()}
    assert "maintenance/garbage-collect" in reachable
    assert all(len(i.split("/")) == 2 for i in reachable)


def test_find_command_accepts_bare_and_qualified_ids():
    assert registry.find_command("rebuild")["id"] == "rebuild"
    assert registry.find_command("maintenance/rebuild")["id"] == "rebuild"


def test_find_command_rejects_unknown_id():
    with pytest.raises(LookupError, match="unknown command"):
        registry.find_command("nope")


def test_destructive_flag_propagates_from_nested_commands():
    assert registry.is_destructive(registry.find_command("run-all"))
    assert not registry.is_destructive(registry.find_command("flake-update"))


def test_no_command_uses_a_pool_wide_zfs_form():
    """`zpool export -a` and friends act on the running host's own pools too.

    Any command that touches an attached foreign disk must name the pool. This
    has been got wrong by hand and cost a silently unmounted /persistent, so it
    is asserted across the whole tree rather than for one command.
    """
    forbidden = ("zpool export -a", "zpool import -a", "zfs unmount -a", "zfs umount -a")
    for path, node in registry.iter_commands():
        for step in node.get("commands", []):
            if not isinstance(step, str):
                continue
            for form in forbidden:
                assert form not in step, f"{registry.qualified_id(path, node)}: {form}"


def test_offline_rebuild_mounts_from_the_host_disko_config():
    """The mount layout must come from disko, not be restated here."""
    node = registry.find_command("maintenance/rebuild-offline")
    steps = [s for s in node["commands"] if isinstance(s, str)]
    assert any("system.build.mountScript" in s for s in steps)
    assert any("zpool export root-pool-<HOSTNAME>" in s for s in steps)
    assert registry.needs_host(node)


def test_needs_host_detects_placeholders():
    assert registry.needs_host(registry.find_command("rebuild"))
    assert not registry.needs_host(registry.find_command("flake-update"))


# --- resolver -------------------------------------------------------------

def test_placeholders_are_substituted():
    cfg = {"flake_path": "/f", "user": "u", "hosts": {"alpha": "1.2.3.4"}}
    out = resolver.resolve_placeholders(
        "<FLAKEPATH>#<HOSTNAME> <USER>@<HOSTURL> <ACTION>", cfg, "alpha", {"ACTION": "switch"}
    )
    assert out == "/f#alpha u@1.2.3.4 switch"


def test_inspect_is_pointed_at_the_configured_flake():
    """Without --path, nix-inspect loads /etc/nixos instead of the flake."""
    node = registry.find_command("maintenance/inspect")
    command = resolver.interactive_command(node, {"flake_path": "/f"})
    assert command == "nix-inspect --path /f"


def test_inspect_without_a_flake_path_omits_the_flag():
    node = registry.find_command("maintenance/inspect")
    assert resolver.interactive_command(node, {}) == "nix-inspect"


def test_all_hosts_expands_the_plan_per_host():
    cfg = {"flake_path": "/f", "user": "u", "hosts": {"alpha": "1.1.1.1", "beta": "2.2.2.2"}}
    node = registry.find_command("rebuild")
    hosts = resolver.target_hosts(node, cfg, None, all_hosts=True)
    plan = resolver.build_plan(node, cfg, hosts, {"ACTION": "switch"})
    assert [h for h, _ in plan] == ["alpha", "beta"]


def test_unknown_host_is_rejected():
    cfg = {"hosts": {"alpha": "1.1.1.1"}}
    with pytest.raises(resolver.ResolutionError, match="unknown host"):
        resolver.target_hosts(registry.find_command("rebuild"), cfg, ["ghost"], False)


def test_host_command_without_a_host_is_rejected():
    with pytest.raises(resolver.ResolutionError, match="targets a host"):
        resolver.target_hosts(registry.find_command("rebuild"), {"hosts": {}}, None, False)


def test_uuid_variables_are_generated_and_overridable():
    variables = {"POOL_UUID": {"type": "uuid"}}
    assert len(resolver.generate_auto_values(variables, {})["POOL_UUID"]) == 8
    assert resolver.generate_auto_values(variables, {"POOL_UUID": "fixed"})["POOL_UUID"] == "fixed"


def test_invalid_list_choice_is_rejected():
    spec = {"type": "list", "options": {"switch": "s", "boot": "b"}}
    with pytest.raises(resolver.ResolutionError, match="invalid value"):
        resolver.validate_choice("ACTION", spec, "bogus")


# --- secrets --------------------------------------------------------------

def test_secret_types_are_recognised():
    assert secrets.is_secret({"type": "password"})
    assert secrets.is_secret({"type": "textarea"})
    assert not secrets.is_secret({"type": "text"})


def test_inline_secret_is_refused():
    with pytest.raises(secrets.SecretError, match="cannot be passed inline"):
        secrets.reject_inline_secret("PASSPHRASE", {"type": "password"})


def test_secret_values_are_redacted_for_display():
    assert secrets.redact("P", {"type": "password"}, "hunter2") == "********"
    assert secrets.redact("D", {"type": "disk"}, "/dev/sdb") == "/dev/sdb"


def test_value_file_strips_only_the_trailing_newline(tmp_path):
    target = tmp_path / "key"
    target.write_text("line one\nline two\n")
    assert secrets.read_value_file(str(target)) == "line one\nline two"


# --- CLI end to end -------------------------------------------------------

def test_list_json_is_wellformed(config_file, capsys):
    assert main(["list", "--json"]) == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert {"id", "name", "destructive", "needs_host"} <= set(payload[0])


def test_dry_run_prints_plan_without_executing(config_file, capsys):
    code = main(["run", "rebuild", "--host", "alpha", "--action", "switch", "-n"])
    out = capsys.readouterr().out
    assert code == EXIT_OK
    assert "nixos-rebuild" in out and "#alpha" in out


def test_destructive_command_requires_yes(config_file, capsys, monkeypatch):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False, raising=False)
    code = main(["run", "garbage-collect", "--host", "alpha"])
    assert code == EXIT_CONFIRM
    assert "requires --yes" in capsys.readouterr().err


def test_secret_flag_value_is_refused(config_file, capsys):
    code = main([
        "run", "format-data-drive", "--host", "alpha",
        "--set", "PASSPHRASE=hunter2", "-n",
    ])
    assert code == EXIT_USAGE
    assert "cannot be passed inline" in capsys.readouterr().err


def test_secret_from_file_is_masked_in_the_plan(config_file, tmp_path, capsys):
    secret = tmp_path / "pass"
    secret.write_text("swordfish\n")
    code = main([
        "run", "format-data-drive", "--host", "alpha",
        "--data-drive", "/dev/sdb", "--mirror-drive", "none",
        "--passphrase-file", str(secret), "-n",
    ])
    out = capsys.readouterr().out
    assert code == EXIT_OK
    assert "********" in out
    assert "swordfish" not in out.split("The following")[0]


def test_secret_from_environment(config_file, monkeypatch, capsys):
    monkeypatch.setenv("NIXTOOL_VAR_PASSPHRASE", "fromenv")
    code = main([
        "run", "format-data-drive", "--host", "alpha",
        "--data-drive", "/dev/sdb", "--mirror-drive", "none", "-n",
    ])
    assert code == EXIT_OK
    assert "PASSPHRASE = ********" in capsys.readouterr().out


def test_secret_is_never_substituted_into_a_resolved_command():
    node = {
        "name": "leaky",
        "commands": ["printf '%s' <PASSPHRASE> > /tmp/out"],
        "menu_variables": {"PASSPHRASE": {"type": "password"}},
    }
    plan = resolver.build_plan(node, {}, [None], {"PASSPHRASE": "swordfish"})
    command = plan[0][1]
    assert "swordfish" not in command
    assert '"$NIXTOOL_SECRET_PASSPHRASE"' in command


def test_non_secret_is_still_substituted_literally():
    node = {
        "name": "plain",
        "commands": ["echo <TARGET>"],
        "menu_variables": {"TARGET": {"type": "text"}},
    }
    plan = resolver.build_plan(node, {}, [None], {"TARGET": "root@host"})
    assert "root@host" in plan[0][1]
    assert "NIXTOOL_SECRET" not in plan[0][1]


def test_secret_reaches_the_command_through_the_environment(tmp_path):
    from nixtool import executor

    out = tmp_path / "written"
    node = {
        "name": "writer",
        "commands": [f"printf '%s' <PASSPHRASE> > {out}"],
        "menu_variables": {"PASSPHRASE": {"type": "password"}},
    }
    values = {"PASSPHRASE": "swordfish"}
    variables = registry.collect_variables(node)
    plan = resolver.build_plan(node, {}, [None], values)
    result = executor.run_plan(
        plan,
        quiet=True,
        env_by_host={None: resolver.secret_environment(variables, values)},
    )
    assert result.ok
    # The value never appeared in the command, but still arrived intact.
    assert out.read_text() == "swordfish"


def test_secret_environment_only_carries_secrets():
    variables = {
        "PASSPHRASE": {"type": "password"},
        "TARGET": {"type": "text"},
    }
    env = resolver.secret_environment(variables, {"PASSPHRASE": "s3cret", "TARGET": "host"})
    assert env == {"NIXTOOL_SECRET_PASSPHRASE": "s3cret"}


def test_secret_with_shell_metacharacters_survives_intact(tmp_path):
    from nixtool import executor

    out = tmp_path / "written"
    hostile = "a b$(touch /tmp/pwned);'\"\\`"
    node = {
        "name": "writer",
        "commands": [f"printf '%s' <PASSPHRASE> > {out}"],
        "menu_variables": {"PASSPHRASE": {"type": "password"}},
    }
    values = {"PASSPHRASE": hostile}
    plan = resolver.build_plan(node, {}, [None], values)
    result = executor.run_plan(
        plan,
        quiet=True,
        env_by_host={
            None: resolver.secret_environment(registry.collect_variables(node), values)
        },
    )
    assert result.ok
    assert out.read_text() == hostile
    assert not pathlib.Path("/tmp/pwned").exists()


def test_missing_variable_is_reported(config_file, capsys, monkeypatch):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False, raising=False)
    code = main(["run", "format-data-drive", "--host", "alpha", "-n"])
    assert code == EXIT_USAGE
    assert "missing required variable" in capsys.readouterr().err


def test_value_file_from_config_is_used(tmp_path, capsys, monkeypatch):
    secret = tmp_path / "pass"
    secret.write_text("fromconfig\n")
    path = tmp_path / "nixtool-config.json"
    path.write_text(json.dumps({
        "flake_path": str(tmp_path / "flake"),
        "hosts": {"alpha": "10.0.0.1"},
        "value_files": {"PASSPHRASE": str(secret)},
    }))
    monkeypatch.setenv("NIXTOOL_CONFIG", str(path))
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False, raising=False)
    code = main([
        "run", "format-data-drive", "--host", "alpha",
        "--data-drive", "/dev/sdb", "--mirror-drive", "none", "-n",
    ])
    out = capsys.readouterr().out
    assert code == EXIT_OK
    assert "PASSPHRASE = ********" in out
    assert "fromconfig" not in out.split("The following")[0]


def test_host_value_file_overrides_the_shared_one(tmp_path, capsys, monkeypatch):
    shared = tmp_path / "shared"
    shared.write_text("shared-secret\n")
    per_host = tmp_path / "alpha"
    per_host.write_text("alpha-secret\n")
    path = tmp_path / "nixtool-config.json"
    path.write_text(json.dumps({
        "flake_path": str(tmp_path / "flake"),
        "hosts": {"alpha": "10.0.0.1"},
        "value_files": {"PASSPHRASE": str(shared)},
        "host_value_files": {"alpha": {"PASSPHRASE": str(per_host)}},
    }))
    monkeypatch.setenv("NIXTOOL_CONFIG", str(path))
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False, raising=False)
    from nixtool import config as config_mod
    resolved = config_mod.value_files(json.loads(path.read_text()), "alpha")
    assert resolved["PASSPHRASE"] == str(per_host)


def _fake_sops(tmp_path, monkeypatch, script):
    """Put a stand-in `sops` on PATH that runs the given shell body."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    sops = bin_dir / "sops"
    sops.write_text("#!/bin/sh\n" + script)
    sops.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")


def test_sops_value_from_config_is_used(tmp_path, capsys, monkeypatch):
    flake = tmp_path / "flake"
    _fake_sops(tmp_path, monkeypatch, 'echo "$4 $3" > "$0.args"; printf fromsops\n')
    path = tmp_path / "nixtool-config.json"
    path.write_text(json.dumps({
        "flake_path": str(flake),
        "hosts": {"alpha": "10.0.0.1"},
        "host_sops_values": {"alpha": {"PASSPHRASE": {"file": "secrets/alpha.yaml", "key": "zfs_passphrase"}}},
    }))
    monkeypatch.setenv("NIXTOOL_CONFIG", str(path))
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False, raising=False)
    code = main([
        "run", "format-data-drive", "--host", "alpha",
        "--data-drive", "/dev/sdb", "--mirror-drive", "none", "-n",
    ])
    out = capsys.readouterr().out
    assert code == EXIT_OK
    assert "PASSPHRASE = ********" in out
    assert (tmp_path / "bin" / "sops.args").read_text().split() == [str(flake / "secrets/alpha.yaml"), '["zfs_passphrase"]']


def test_sops_value_strips_only_the_trailing_newline(tmp_path, monkeypatch):
    _fake_sops(tmp_path, monkeypatch, "printf 'line one\\nline two\\n'\n")
    assert secrets.read_sops_value("/abs/file.yaml", "key") == "line one\nline two"


def test_sops_failure_is_reported(tmp_path, monkeypatch):
    _fake_sops(tmp_path, monkeypatch, "echo 'Error getting data key: 0 successful groups required' >&2; exit 128\n")
    with pytest.raises(secrets.SecretError, match="0 successful groups"):
        secrets.read_sops_value("/abs/file.yaml", "key")


def test_value_file_beats_a_sops_value(tmp_path, monkeypatch):
    declared = tmp_path / "declared"
    declared.write_text("from-file\n")
    _fake_sops(tmp_path, monkeypatch, "printf from-sops\n")
    config = {
        "hosts": {"alpha": "10.0.0.1"},
        "value_files": {"PASSPHRASE": str(declared)},
        "sops_values": {"PASSPHRASE": {"file": "/abs/file.yaml", "key": "k"}},
    }
    from nixtool import config as config_mod
    assert config_mod.value_files(config, "alpha")["PASSPHRASE"] == str(declared)
    assert config_mod.sops_values(config, "alpha")["PASSPHRASE"]["key"] == "k"


def test_explicit_flag_beats_a_config_value_file(tmp_path, capsys, monkeypatch):
    declared = tmp_path / "declared"
    declared.write_text("from-config\n")
    explicit = tmp_path / "explicit"
    explicit.write_text("from-flag\n")
    path = tmp_path / "nixtool-config.json"
    path.write_text(json.dumps({
        "flake_path": str(tmp_path / "flake"),
        "hosts": {"alpha": "10.0.0.1"},
        "value_files": {"PASSPHRASE": str(declared)},
    }))
    monkeypatch.setenv("NIXTOOL_CONFIG", str(path))
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False, raising=False)
    code = main([
        "run", "format-data-drive", "--host", "alpha",
        "--data-drive", "/dev/sdb", "--mirror-drive", "none",
        "--passphrase-file", str(explicit), "-n",
    ])
    assert code == EXIT_OK
    # Both are masked in the plan, so assert on the resolved value directly.
    from nixtool import config as config_mod
    assert config_mod.value_files(json.loads(path.read_text()))["PASSPHRASE"] == str(declared)


def test_missing_config_value_file_is_an_error(tmp_path, capsys, monkeypatch):
    path = tmp_path / "nixtool-config.json"
    path.write_text(json.dumps({
        "flake_path": str(tmp_path / "flake"),
        "hosts": {"alpha": "10.0.0.1"},
        "value_files": {"PASSPHRASE": str(tmp_path / "does-not-exist")},
    }))
    monkeypatch.setenv("NIXTOOL_CONFIG", str(path))
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False, raising=False)
    code = main([
        "run", "format-data-drive", "--host", "alpha",
        "--data-drive", "/dev/sdb", "--mirror-drive", "none", "-n",
    ])
    assert code == EXIT_USAGE
    assert "cannot read value file" in capsys.readouterr().err


def test_global_config_flag_reaches_subparsers(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("NIXTOOL_CONFIG", raising=False)
    path = tmp_path / "c.json"
    path.write_text(json.dumps({"hosts": {"gamma": "9.9.9.9"}}))
    assert main(["-c", str(path), "hosts"]) == EXIT_OK
    assert "gamma" in capsys.readouterr().out


def test_missing_explicit_config_is_an_error(capsys, monkeypatch):
    monkeypatch.delenv("NIXTOOL_CONFIG", raising=False)
    assert main(["-c", "/nonexistent/c.json", "config"]) == EXIT_ERROR
    assert "not found" in capsys.readouterr().err


def test_malformed_config_is_an_error(tmp_path, capsys, monkeypatch):
    path = tmp_path / "bad.json"
    path.write_text("{not json")
    monkeypatch.setenv("NIXTOOL_CONFIG", str(path))
    assert main(["config"]) == EXIT_ERROR
    assert "invalid JSON" in capsys.readouterr().err


def test_successful_execution_returns_zero(config_file, capsys):
    code = main(["run", "flake-update", "-n"])
    assert code == EXIT_OK


# --- generations ----------------------------------------------------------

LISTING = """   1   2025-11-02 09:14:33
   2   2025-12-18 22:03:11
   3   2026-01-30 08:41:07
   4   2026-03-11 17:55:02   (current)
"""


def test_generation_listing_is_parsed():
    parsed = generations.parse(LISTING)
    assert [g["id"] for g in parsed] == [1, 2, 3, 4]
    assert parsed[3]["current"] and not parsed[0]["current"]
    assert parsed[0]["date"] == "2025-11-02 09:14:33"


def test_generation_listing_ignores_noise():
    assert generations.parse("error: broken\nnot a generation") == []
    assert generations.parse("") == []


NIXOS_LISTING = """[
  {"generation": 249, "date": "2026-09-09 09:54:39", "nixosVersion": "26.11.20260908.d6524aa",
   "kernelVersion": "6.18.50", "current": false},
  {"generation": 250, "date": "2026-09-09 11:47:53", "nixosVersion": "26.11.20260908.d6524aa",
   "kernelVersion": "6.18.51", "current": true}
]"""


def test_the_nixos_listing_carries_version_and_kernel():
    parsed = generations.parse_nixos_json(NIXOS_LISTING)
    assert [g["id"] for g in parsed] == [249, 250]
    assert parsed[1]["current"] and not parsed[0]["current"]
    assert parsed[1]["kernel"] == "6.18.51"
    assert parsed[0]["nixos"] == "26.11.20260908.d6524aa"


def test_a_listing_that_is_not_usable_json_falls_back_rather_than_crashing():
    # nixos-rebuild is missing, too old, or answering with something else.
    assert generations.parse_nixos_json("not json") == []
    assert generations.parse_nixos_json('{"generation": 1}') == []
    assert generations.parse_nixos_json("[]") == []


def test_the_system_profile_prefers_nixos_rebuild(monkeypatch):
    asked = []

    def fake_run(command, timeout):
        asked.append(command)
        return NIXOS_LISTING, None

    monkeypatch.setattr(generations, "_run", fake_run)
    found, error = generations.read("system", {}, None)
    assert error is None
    assert asked == ["nixos-rebuild list-generations --json"]
    assert found[0]["kernel"] == "6.18.50"


def test_the_user_profile_and_a_silent_host_fall_back_to_nix_env(monkeypatch):
    asked = []

    def fake_run(command, timeout):
        asked.append(command)
        if "nixos-rebuild" in command:
            return None, "command not found"
        return LISTING, None

    monkeypatch.setattr(generations, "_run", fake_run)
    found, _ = generations.read("user", {}, None)
    assert [g["id"] for g in found] == [1, 2, 3, 4]
    assert not any("nixos-rebuild" in command for command in asked)

    found, _ = generations.read("system", {}, None)
    assert [g["id"] for g in found] == [1, 2, 3, 4]
    assert asked[-2] == "nixos-rebuild list-generations --json"


def test_generation_selection_accepts_what_nix_env_accepts():
    assert generations.normalise([3, 1, 2]) == "1 2 3"
    assert generations.normalise("old") == "old"
    assert generations.normalise("+5") == "+5"
    assert generations.normalise("none") == ""
    assert generations.normalise("") == ""
    assert generations.normalise(None) == ""


def test_generation_selection_rejects_shell_injection():
    """The value reaches a shell, and on the CLI it comes from a flag."""
    for hostile in ("1; rm -rf /", "$(touch /tmp/pwned)", "old; reboot", "`id`"):
        with pytest.raises(generations.GenerationError):
            generations.normalise(hostile)


def test_generation_commands_reach_the_selected_host():
    """Regression: these ran bare, so picking a host still acted locally."""
    cfg = {"user": "admin", "hosts": {"alpha": "10.0.0.1"}}
    assert generations.list_command("system", cfg, "alpha").startswith(
        "ssh admin@10.0.0.1 "
    )
    assert generations.collect_garbage_command(cfg, "alpha") == (
        "ssh admin@10.0.0.1 sudo nix-collect-garbage -d"
    )


def test_generation_commands_stay_local_for_an_unconfigured_host():
    assert generations.list_command("system", {}, None) == (
        "sudo nix-env --profile /nix/var/nix/profiles/system --list-generations"
    )


def test_nothing_selected_deletes_nothing():
    commands = commands_module.get_generation_commands(
        {}, {"SYSTEM_GENERATIONS": "none", "USER_GENERATIONS": "none", "RUN_GC": "no"}
    )
    assert len(commands) == 1
    assert commands[0].startswith("echo ")
    assert "delete-generations" not in commands[0]
    assert "collect-garbage" not in commands[0]


def test_only_the_selected_profile_is_deleted():
    commands = commands_module.get_generation_commands(
        {}, {"SYSTEM_GENERATIONS": "1 2", "USER_GENERATIONS": "none", "RUN_GC": "no"}
    )
    assert commands == [
        "sudo nix-env --profile /nix/var/nix/profiles/system"
        " --delete-generations 1 2"
    ]


def test_duplication_copies_a_generation_forward():
    """--set adds a generation; it does not move the pointer to the old one."""
    cfg = {"user": "admin", "hosts": {"alpha": "10.0.0.1"}}
    assert generations.duplicate_command("128", cfg, "alpha") == (
        "ssh admin@10.0.0.1 sudo nix-env --profile /nix/var/nix/profiles/system"
        " --set /nix/var/nix/profiles/system-128-link"
    )
    assert generations.duplicate_command("none", cfg, "alpha") == ""
    assert generations.duplicate_command(None, cfg, "alpha") == ""


def test_duplication_takes_one_generation_and_not_a_rule():
    assert generations.normalise_one("128") == "128"
    assert generations.normalise_one("none") == ""
    assert generations.normalise_one("") == ""
    for rejected in ("old", "+5", "1 2", "1; rm -rf /"):
        with pytest.raises(generations.GenerationError):
            generations.normalise_one(rejected)


def test_duplication_runs_before_any_deletion():
    """Otherwise the delete could take the closure the copy was made from."""
    commands = commands_module.get_generation_commands(
        {},
        {
            "DUPLICATE_GENERATION": "3",
            "SYSTEM_GENERATIONS": "old",
            "USER_GENERATIONS": "none",
            "RUN_GC": "yes",
        },
    )
    assert "--set /nix/var/nix/profiles/system-3-link" in commands[0]
    assert "--delete-generations old" in commands[1]
    assert commands[-1] == "sudo nix-collect-garbage -d"


def test_garbage_collection_is_appended_when_asked():
    commands = commands_module.get_generation_commands(
        {}, {"SYSTEM_GENERATIONS": "old", "USER_GENERATIONS": "none", "RUN_GC": "yes"}
    )
    assert commands[-1] == "sudo nix-collect-garbage -d"


def test_the_generation_command_resolves_through_the_normal_plan():
    cfg = {"user": "admin", "hosts": {"alpha": "10.0.0.1"}}
    node = registry.find_command("manage-generations")
    plan = resolver.build_plan(node, cfg, ["alpha"], {
        "SYSTEM_GENERATIONS": "2 3", "USER_GENERATIONS": "none", "RUN_GC": "yes",
    })
    assert [host for host, _ in plan] == ["alpha", "alpha"]
    assert all(command.startswith("ssh admin@10.0.0.1 ") for _, command in plan)
    assert "--delete-generations 2 3" in plan[0][1]
    assert plan[1][1].endswith("nix-collect-garbage -d")


def test_the_generation_command_still_targets_a_host():
    node = registry.find_command("manage-generations")
    assert registry.needs_host(node)
    assert registry.is_destructive(node)
    assert sorted(registry.collect_variables(node)) == [
        "DUPLICATE_GENERATION", "RUN_GC", "SYSTEM_GENERATIONS", "USER_GENERATIONS",
    ]


def test_a_declared_default_keeps_the_other_flags_optional(config_file, capsys):
    """The documented duplication run names one flag and nothing else."""
    code = main([
        "run", "manage-generations", "--host", "alpha",
        "--duplicate-generation", "128", "-n",
    ])
    assert code == EXIT_OK
    out = capsys.readouterr().out
    assert "--set /nix/var/nix/profiles/system-128-link" in out
    assert "delete-generations" not in out
    assert "nix-collect-garbage" not in out


def test_a_bad_duplication_is_a_usage_error(config_file, capsys):
    code = main([
        "run", "manage-generations", "--host", "alpha",
        "--duplicate-generation", "old", "-n",
    ])
    assert code != EXIT_OK
    assert "expected a single generation number" in capsys.readouterr().err


def test_generations_can_be_driven_from_flags(config_file, capsys):
    code = main([
        "run", "manage-generations", "--host", "alpha",
        "--system-generations", "old", "--user-generations", "none",
        "--run-gc", "yes", "-n",
    ])
    assert code == EXIT_OK
    out = capsys.readouterr().out
    assert "--delete-generations old" in out
    assert "nix-collect-garbage -d" in out


# --- the trimmed menu ------------------------------------------------------

def test_preview_and_purge_are_off_the_menu():
    """Generation work goes through manage-generations now."""
    listed = {registry.qualified_id(p, n) for p, n in registry.iter_commands()}
    assert "maintenance/preview-generations" not in listed
    assert "maintenance/purge-generations" not in listed
    assert "maintenance/manage-generations" in listed


def test_run_all_still_prunes_generations():
    """They were removed from the menu, not from the composite that used them."""
    node = registry.find_command("run-all")
    plan = resolver.build_plan(
        node, {"user": "admin", "hosts": {"alpha": "10.0.0.1"}}, ["alpha"],
        {"ACTION": "switch"},
    )
    joined = " ".join(command for _, command in plan)
    assert "--list-generations" in joined
    assert "--delete-generations old" in joined
    assert "nix-collect-garbage -d" in joined


def test_the_unpersisted_report_runs_on_the_selected_host():
    cfg = {"user": "admin", "hosts": {"alpha": "10.0.0.1"}}
    node = registry.find_command("unpersisted")
    plan = resolver.build_plan(node, cfg, ["alpha"], {})
    assert [host for host, _ in plan] == ["alpha"] * 4
    # Every step reads the host's own mounts, so a bare one would report this machine.
    assert all(command.startswith("ssh admin@10.0.0.1 ") for _, command in plan)


def test_the_unpersisted_report_covers_both_storage_backends():
    commands = commands_module.get_unpersisted_commands({}, {}, None)
    roots = commands[0]
    assert "zfs list -o name,used,mountpoint" in roots
    assert "btrfs subvolume list" in roots
    # The roots come off the mounts; a pool name would only fit the ZFS hosts.
    assert "root-pool-" not in roots


def test_the_unpersisted_tree_totals_are_not_eaten_by_the_shell():
    # sed -n "1,40p;$p" expands $p to nothing, dropping the "used in" line.
    commands = commands_module.get_unpersisted_commands({}, {}, None)
    assert all("sed -n '1,40p;$p'" in command for command in commands[1:3])
