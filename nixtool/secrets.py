"""Sourcing variable values, with secrets kept off the command line.

Password and textarea variables hold SSH host keys and disk-encryption
passphrases. Accepting those as flag values would leak them into shell history
and into ``ps`` output for the lifetime of the run, so they may only arrive by
file, stdin, environment, or an interactive prompt.
"""

import getpass
import os
import pathlib
import sys

SECRET_TYPES = {"password", "textarea"}
ENV_PREFIX = "NIXTOOL_VAR_"


class SecretError(Exception):
    """Raised when a value cannot be sourced, or is sourced unsafely."""


def is_secret(spec: dict) -> bool:
    return spec.get("type") in SECRET_TYPES


def env_var_name(name: str) -> str:
    return f"{ENV_PREFIX}{name}"


def read_value_file(path: str) -> str:
    """Read a value from a file, or from stdin when the path is ``-``.

    A single trailing newline is stripped so ``--set-file K=<(echo secret)``
    and heredocs behave as the user intends; interior newlines are preserved
    for multi-line values such as SSH keys.
    """
    if path == "-":
        data = sys.stdin.read()
    else:
        target = pathlib.Path(path).expanduser()
        try:
            data = target.read_text()
        except OSError as exc:
            raise SecretError(f"cannot read value file '{path}': {exc}") from exc
    return data[:-1] if data.endswith("\n") else data


def from_environment(name: str) -> str | None:
    """A value supplied via ``NIXTOOL_VAR_<NAME>``, if present."""
    return os.environ.get(env_var_name(name))


def prompt_for(name: str, spec: dict) -> str:
    """Interactively prompt for a value, hiding input for secrets."""
    if not sys.stdin.isatty():
        raise SecretError(
            f"no value for {name} and stdin is not a terminal; "
            f"pass --set-file {name}=PATH or set {env_var_name(name)}"
        )

    title = spec.get("title") or f"Enter {name}"

    if spec.get("type") == "password":
        value = getpass.getpass(f"{title}: ")
        confirm = getpass.getpass(f"{title} (confirm): ")
        if value != confirm:
            raise SecretError(f"{name}: entries did not match")
        return value

    if spec.get("type") == "textarea":
        print(f"{title} (end with Ctrl-D on a blank line):", file=sys.stderr)
        return sys.stdin.read().rstrip("\n")

    if spec.get("type") == "list":
        options = spec.get("options", {})
        keys = list(options)
        for index, key in enumerate(keys, start=1):
            print(f"  {index}) {options[key]}", file=sys.stderr)
        while True:
            answer = input(f"{title} [1-{len(keys)}]: ").strip()
            if answer in options:
                return answer
            if answer.isdigit() and 1 <= int(answer) <= len(keys):
                return keys[int(answer) - 1]
            print("Invalid selection.", file=sys.stderr)

    return input(f"{title}: ")


def reject_inline_secret(name: str, spec: dict) -> None:
    """Refuse a secret passed as a plain flag value."""
    if is_secret(spec):
        raise SecretError(
            f"{name} is a secret and cannot be passed inline. "
            f"Use --set-file {name}=PATH (or '-' for stdin), "
            f"or set {env_var_name(name)}."
        )


def redact(name: str, spec: dict, value: str) -> str:
    """The display form of a value, masked when it is a secret."""
    return "********" if is_secret(spec) else value
