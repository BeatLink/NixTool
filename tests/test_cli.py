"""Tests for the nixtool command line interface."""

import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

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


def test_missing_variable_is_reported(config_file, capsys, monkeypatch):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False, raising=False)
    code = main(["run", "format-data-drive", "--host", "alpha", "-n"])
    assert code == EXIT_USAGE
    assert "missing required variable" in capsys.readouterr().err


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
