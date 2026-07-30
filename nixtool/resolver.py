"""Turning a command definition plus variables into a concrete shell queue.

The TUI does this inline in main.py against widget state; the CLI needs the same
substitution rules without an app instance, so the logic lives here and both
front ends call it.
"""

import shlex
import uuid

from . import registry


class ResolutionError(Exception):
    """Raised when a command cannot be turned into a runnable queue."""


def generate_auto_values(variables: dict, supplied: dict) -> dict:
    """Fill in variables the tool generates itself, e.g. ``uuid``.

    Explicitly supplied values win, so a caller can pin a pool UUID to re-run a
    command against an existing pool.
    """
    values = dict(supplied)
    for name, spec in variables.items():
        if name in values:
            continue
        if spec.get("type") == "uuid":
            values[name] = str(uuid.uuid4())[:8]
    return values


def missing_variables(variables: dict, values: dict) -> list[str]:
    """Names of required variables that still have no value."""
    return [name for name in variables if name not in values]


def validate_choice(name: str, spec: dict, value: str) -> None:
    """Reject values outside a list variable's declared options."""
    if spec.get("type") != "list":
        return
    options = spec.get("options", {})
    if options and value not in options:
        allowed = ", ".join(sorted(options))
        raise ResolutionError(
            f"invalid value '{value}' for {name}; expected one of: {allowed}"
        )


def resolve_placeholders(text: str, config: dict, hostname: str | None, values: dict) -> str:
    """Substitute <FLAKEPATH>, <HOSTNAME>, <USER>, <HOSTURL> and variables.

    Every substituted value is shell-quoted. Placeholders are expanded into
    strings that are handed to ``sh -c``, so an unquoted value containing a
    quote, space, ``$`` or ``;`` would either break the command or execute
    attacker-chosen shell. Quoting each value independently is still correct
    inside composite words: ``<FLAKEPATH>#<HOSTNAME>`` becomes ``'/f'#'alpha'``,
    which the shell concatenates back to ``/f#alpha``.
    """
    host_map = config.get("hosts", {}) if isinstance(config.get("hosts"), dict) else {}
    replacements = {
        "<FLAKEPATH>": config.get("flake_path", ""),
        "<HOSTNAME>": hostname or "",
        "<USER>": config.get("user", ""),
        "<HOSTURL>": host_map.get(hostname, "") if hostname else "",
    }
    for key, value in values.items():
        replacements[f"<{key}>"] = value
    for key, value in replacements.items():
        text = text.replace(key, shell_quote(str(value)))
    return text


def shell_quote(value: str) -> str:
    """Quote a value for safe interpolation into a shell command.

    ``shlex.quote`` leaves values it considers safe bare, which keeps the
    common case (``switch``, ``/dev/sdb``) readable in the plan preview.
    """
    return shlex.quote(value)


def resolve_command(node: dict, config: dict, hostname: str | None, values: dict) -> list[str]:
    """Flatten a command (and any nested sub-commands) into shell strings."""
    queue = []
    for item in node.get("commands", []):
        if isinstance(item, str):
            queue.append(resolve_placeholders(item, config, hostname, values))
        elif callable(item):
            queue.extend(item(config.get("flake_path")))
        elif isinstance(item, dict):
            queue.extend(resolve_command(item, config, hostname, values))
    return queue


def build_plan(node: dict, config: dict, hostnames: list[str | None], values: dict) -> list[tuple[str | None, str]]:
    """The full execution plan as ``(hostname, command)`` pairs.

    Running against several hosts resolves the command once per host, matching
    the TUI's "All Hosts" batch behaviour.
    """
    plan = []
    for hostname in hostnames or [None]:
        for command in resolve_command(node, config, hostname, values):
            plan.append((hostname, command))
    return plan


def target_hosts(node: dict, config: dict, requested: list[str] | None, all_hosts: bool) -> list[str | None]:
    """Which hosts a command should run against.

    Commands that reference no host resolve once with no hostname, even when
    hosts are configured.
    """
    configured = config.get("hosts", {}) if isinstance(config.get("hosts"), dict) else {}

    if not registry.needs_host(node):
        return [None]

    if all_hosts:
        names = [name for name, url in configured.items() if url != "all"]
        if not names:
            raise ResolutionError("--all-hosts given but no hosts are configured")
        return names

    if requested:
        unknown = [name for name in requested if name not in configured]
        if unknown:
            known = ", ".join(sorted(configured)) or "(none configured)"
            raise ResolutionError(
                f"unknown host(s): {', '.join(unknown)}. Configured hosts: {known}"
            )
        return list(requested)

    raise ResolutionError(
        "this command targets a host; pass --host NAME (repeatable) or --all-hosts"
    )
