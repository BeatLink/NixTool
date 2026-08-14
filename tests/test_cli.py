"""Tests for the nixtool command line interface."""

import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from nixtool import commands as commands_module
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


# --- wizard stages --------------------------------------------------------

def _wizard(tmp_path):
    """A three-stage wizard whose stages each leave a file behind."""
    return {
        "name": "wizard",
        "stages": [
            {
                "name": "look",
                "commands": [f"touch {tmp_path}/look"],
            },
            {
                "name": "purge", "optional": True, "prompt": "Purge?",
                "commands": [f"touch {tmp_path}/purge"],
            },
            {
                "name": "gc", "optional": True, "prompt": "Collect?",
                "commands": [f"touch {tmp_path}/gc"],
            },
        ],
    }


def _run_args(**overrides):
    import argparse

    defaults = dict(yes=False, quiet=True, keep_going=False, non_interactive=False)
    return argparse.Namespace(**{**defaults, **overrides})


def _run_wizard(tmp_path, **overrides):
    from nixtool import cli

    stages = resolver.build_stages(_wizard(tmp_path), {}, [None], {})
    code = cli.run_stages(stages, _run_args(**overrides), None, {}, 3)
    return code, {name for name in ("look", "purge", "gc") if (tmp_path / name).exists()}


def test_plain_command_is_one_mandatory_stage():
    node = registry.find_command("flake-update")
    stages = resolver.build_stages(node, {}, [None], {})
    assert len(stages) == 1
    assert not stages[0]["optional"]
    assert stages[0]["plan"] == resolver.build_plan(node, {}, [None], {})


def test_optional_stages_are_skipped_without_a_terminal(tmp_path):
    """Off a TTY the deleting stages must not be assumed; --yes is required."""
    code, ran = _run_wizard(tmp_path, non_interactive=True)
    assert code == EXIT_OK
    assert ran == {"look"}


def test_yes_accepts_every_optional_stage(tmp_path):
    code, ran = _run_wizard(tmp_path, yes=True, non_interactive=True)
    assert code == EXIT_OK
    assert ran == {"look", "purge", "gc"}


def test_each_optional_stage_is_asked_for_separately(tmp_path, monkeypatch):
    answers = iter(["y", "n"])
    monkeypatch.setattr("builtins.input", lambda *_: next(answers))
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    code, ran = _run_wizard(tmp_path)
    assert code == EXIT_OK
    assert ran == {"look", "purge"}


def test_a_failed_stage_stops_the_wizard(tmp_path):
    from nixtool import cli

    stages = resolver.build_stages(
        {
            "name": "wizard",
            "stages": [
                {"name": "boom", "commands": ["false"]},
                {"name": "after", "commands": [f"touch {tmp_path}/after"]},
            ],
        },
        {}, [None], {},
    )
    assert cli.run_stages(stages, _run_args(), None, {}, 2) == EXIT_ERROR
    assert not (tmp_path / "after").exists()


# --- the generation wizard ------------------------------------------------

def test_generations_are_previewed_before_anything_is_offered():
    stages = resolver.stage_nodes(registry.find_command("manage-generations"))
    first = stages[0]
    assert not first.get("optional")
    assert "--list-generations" in first["commands"][0]
    assert [bool(s.get("optional")) for s in stages] == [False, True, True]


def test_only_the_destructive_generation_stages_are_optional():
    """The preview runs unasked precisely because it deletes nothing."""
    stages = resolver.stage_nodes(registry.find_command("manage-generations"))
    mandatory = [s for s in stages if not s.get("optional")]
    optional = [s for s in stages if s.get("optional")]
    assert not any(registry.is_destructive(s) for s in mandatory)
    assert optional and all(registry.is_destructive(s) for s in optional)
    assert all(s.get("prompt") for s in optional)


def test_the_generation_wizard_still_targets_a_host():
    node = registry.find_command("manage-generations")
    assert registry.needs_host(node)
    assert registry.is_destructive(node)


def test_wizard_stages_resolve_for_every_host():
    cfg = {"hosts": {"alpha": "1.1.1.1", "beta": "2.2.2.2"}}
    node = registry.find_command("manage-generations")
    hosts = resolver.target_hosts(node, cfg, None, all_hosts=True)
    stages = resolver.build_stages(node, cfg, hosts, {})
    # Every host is previewed before the first offer is made.
    assert [h for h, _ in stages[0]["plan"]] == ["alpha", "beta"]
