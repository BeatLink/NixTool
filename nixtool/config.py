"""Locating and loading nixtool-config.json."""

import json
import os
import pathlib

CONFIG_FILENAME = "nixtool-config.json"


class ConfigError(Exception):
    """Raised when the config file is missing or malformed."""


def default_search_paths():
    """Config locations tried in order, most specific first."""
    paths = []
    override = os.environ.get("NIXTOOL_CONFIG")
    if override:
        paths.append(pathlib.Path(override).expanduser())
    paths.append(pathlib.Path.cwd() / CONFIG_FILENAME)
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = pathlib.Path(xdg).expanduser() if xdg else pathlib.Path.home() / ".config"
    paths.append(base / "nixtool" / CONFIG_FILENAME)
    paths.append(pathlib.Path.home() / f".{CONFIG_FILENAME}")
    paths.append(pathlib.Path("/etc/nixtool") / CONFIG_FILENAME)
    return paths


def resolve_config_path(explicit: pathlib.Path | None = None) -> pathlib.Path | None:
    """The config path to use, or None if nothing was found.

    An explicit path is returned even when it does not exist, so callers can
    report the exact path the user asked for rather than a search failure.
    """
    if explicit is not None:
        return pathlib.Path(explicit).expanduser()
    for candidate in default_search_paths():
        if candidate.is_file():
            return candidate
    return None


def load_config(explicit: pathlib.Path | None = None) -> tuple[dict, pathlib.Path | None]:
    """Load the config, returning ``(config, path)``.

    A missing config is not fatal on its own — commands that need no flake path
    or hosts still run — so this returns an empty dict rather than raising.
    Malformed JSON is fatal, since silently ignoring it would resolve
    placeholders to empty strings and run the wrong command.
    """
    path = resolve_config_path(explicit)
    if path is None or not path.is_file():
        if explicit is not None:
            raise ConfigError(f"config file not found: {path}")
        return {}, None
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise ConfigError(f"invalid JSON in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: expected a JSON object at the top level")
    return data, path


def hosts(config: dict) -> dict:
    """The configured hostname -> URL mapping."""
    value = config.get("hosts", {})
    return value if isinstance(value, dict) else {}


def value_files(config: dict, hostname: str | None = None) -> dict:
    """Variable name -> path of a file holding that variable's value.

    Declaring these in the config keeps credentials out of both the command line
    and the environment: nixtool reads the file at run time, so the config only
    ever names a path. That path is typically a sops-nix secret under
    ``/run/secrets``, which lets a NixOS module own the credentials for an
    install while the config file stays free of plaintext.

    ``value_files`` applies to every host; ``host_value_files.<hostname>``
    overrides it per host, so a shared SSH password can sit alongside a
    per-machine disk encryption key.
    """
    return _merged(config, "value_files", "host_value_files", hostname)


def declared_values(config: dict, hostname: str | None = None) -> dict:
    """Variable name -> literal value, from the config file.

    For non-secret variables only, such as the SSH target of an install. A
    secret named here is refused by the caller rather than silently honoured,
    because the config file is not an encrypted store: putting a passphrase in
    it would defeat the point of naming a path in ``value_files`` instead.

    ``values`` applies to every host; ``host_values.<hostname>`` overrides it.
    """
    return _merged(config, "values", "host_values", hostname)


def _merged(config: dict, shared_key: str, host_key: str, hostname: str | None) -> dict:
    """A shared string mapping overlaid with its per-host counterpart."""
    resolved = {}
    shared = config.get(shared_key)
    if isinstance(shared, dict):
        resolved.update({k: v for k, v in shared.items() if isinstance(v, str)})
    per_host = config.get(host_key)
    if hostname and isinstance(per_host, dict):
        entry = per_host.get(hostname)
        if isinstance(entry, dict):
            resolved.update({k: v for k, v in entry.items() if isinstance(v, str)})
    return resolved
