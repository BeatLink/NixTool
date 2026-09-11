"""Reading and pruning nix profile generations.

``nix-env --list-generations`` is the only way to find out what exists, so the
front ends run it and parse it rather than asking the user to know. Everything
here is pure enough to test without a nix store: the commands are built as
strings and the output is parsed from text.
"""

import json
import re
import shlex
import subprocess

SYSTEM_PROFILE = "/nix/var/nix/profiles/system"

# A generations variable can also name one generation to copy forward rather
# than a set to delete. That reads the system profile, so it lists like one.
DUPLICATE = "duplicate"

# The system profile needs root; the user profile is the caller's own.
PROFILES = {
    "system": f"sudo nix-env --profile {SYSTEM_PROFILE}",
    "user": "nix-env",
    DUPLICATE: f"sudo nix-env --profile {SYSTEM_PROFILE}",
}

# "   42   2026-03-11 17:55:02   (current)"
GENERATION_LINE = re.compile(
    r"^\s*(\d+)\s+(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s*(\(current\))?\s*$"
)

# What nix-env --delete-generations accepts: an explicit list, everything but
# the current one, or everything older than the last N. Checked because the
# value reaches a shell, and on the CLI it comes from a flag.
SELECTION = re.compile(r"^(old|\+\d+|\d+( +\d+)*)$")

# Duplication copies exactly one generation, so the blunt rules do not apply.
SINGLE = re.compile(r"^\d+$")

NONE = "none"


class GenerationError(Exception):
    """Raised when generations cannot be listed or a selection is malformed."""


def remote_prefix(config: dict, hostname: str | None) -> str:
    """``ssh user@host `` for a configured host, or "" when there is none.

    Generation commands used to run bare, so picking a host listed and deleted
    the generations of the machine nixtool was running on.
    """
    host_map = config.get("hosts", {}) if isinstance(config.get("hosts"), dict) else {}
    url = host_map.get(hostname) if hostname else None
    if not url:
        return ""
    user = config.get("user", "")
    target = f"{user}@{url}" if user else url
    return f"ssh {shlex.quote(target)} "


def list_command(profile: str, config: dict, hostname: str | None) -> str:
    return f"{remote_prefix(config, hostname)}{PROFILES[profile]} --list-generations"


def nixos_list_command(config: dict, hostname: str | None) -> str:
    """``nixos-rebuild list-generations --json``, which nix-env has no answer to.

    It reads the profile links rather than the store, so unlike the nix-env
    listing it needs no sudo.
    """
    return f"{remote_prefix(config, hostname)}nixos-rebuild list-generations --json"


def delete_command(
    profile: str, selection: str, config: dict, hostname: str | None
) -> str:
    """The delete for one profile, or "" when nothing was selected."""
    selection = normalise(selection)
    if not selection:
        return ""
    return (
        f"{remote_prefix(config, hostname)}{PROFILES[profile]}"
        f" --delete-generations {selection}"
    )


def duplicate_command(selection, config: dict, hostname: str | None) -> str:
    """A new system generation holding an older one, or "" when none was picked.

    ``--set`` appends a generation rather than moving the pointer, so the one
    that was copied stays where it is and a later ``--delete-generations old``
    still has something to remove. The generation link is passed as-is:
    nix-env resolves it to the store path itself, which keeps the command free
    of a substitution that would otherwise expand on the wrong side of an ssh.
    """
    selection = normalise_one(selection)
    if not selection:
        return ""
    return (
        f"{remote_prefix(config, hostname)}{PROFILES[DUPLICATE]}"
        f" --set {SYSTEM_PROFILE}-{selection}-link"
    )


def collect_garbage_command(config: dict, hostname: str | None) -> str:
    return f"{remote_prefix(config, hostname)}sudo nix-collect-garbage -d"


def normalise(selection) -> str:
    """A selection as a shell-safe argument list, or "" for nothing to do."""
    if selection is None:
        return ""
    if isinstance(selection, (list, tuple, set)):
        selection = " ".join(str(item) for item in sorted(selection, key=int))
    selection = str(selection).strip()
    if not selection or selection.lower() == NONE:
        return ""
    if not SELECTION.match(selection):
        raise GenerationError(
            f"invalid generation selection '{selection}'; expected generation "
            "numbers, 'old', '+N', or 'none'"
        )
    return selection


def normalise_one(selection) -> str:
    """One generation number as a shell-safe argument, or "" for nothing to do."""
    if isinstance(selection, (list, tuple, set)):
        selection = " ".join(str(item) for item in selection)
    selection = str("" if selection is None else selection).strip()
    if not selection or selection.lower() == NONE:
        return ""
    if not SINGLE.match(selection):
        raise GenerationError(
            f"invalid generation to duplicate '{selection}'; expected a single "
            "generation number, or 'none'"
        )
    return selection


def parse(text: str) -> list[dict]:
    """``[{"id": 42, "date": "...", "current": True}]`` from --list-generations."""
    found = []
    for line in (text or "").splitlines():
        match = GENERATION_LINE.match(line)
        if match:
            found.append({
                "id": int(match.group(1)),
                "date": match.group(2),
                "current": bool(match.group(3)),
            })
    return found


def parse_nixos_json(text: str) -> list[dict]:
    """The same shape as ``parse``, plus the columns nix-env cannot report."""
    try:
        entries = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(entries, list):
        return []
    found = []
    for entry in entries:
        if not isinstance(entry, dict) or "generation" not in entry:
            continue
        found.append({
            "id": int(entry["generation"]),
            "date": str(entry.get("date", "")),
            "current": bool(entry.get("current")),
            "nixos": str(entry.get("nixosVersion", "")),
            "kernel": str(entry.get("kernelVersion", "")),
        })
    return sorted(found, key=lambda entry: entry["id"])


def _run(command: str, timeout: int):
    """``(stdout, error)`` from one listing, with failure reported not raised."""
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return None, f"timed out after {timeout}s: {command}"
    if result.returncode != 0:
        detail = (result.stderr or "").strip().splitlines()
        return None, (detail[-1] if detail else f"exit code {result.returncode}")
    return result.stdout, None


def read(profile: str, config: dict, hostname: str | None, timeout: int = 30):
    """Run the listing for one profile and parse it.

    The system profile is read through nixos-rebuild first, because a date and
    a number do not say which generation is which; the NixOS version and the
    kernel do. Anything that does not come back as usable JSON falls through to
    nix-env, which every profile understands and which needs no NixOS at all.

    Returns ``(generations, error)``: a failure to reach the host is reported
    rather than raised, so one unreachable profile still lets the other show.
    """
    if profile in ("system", DUPLICATE):
        output, error = _run(nixos_list_command(config, hostname), timeout)
        if error is None:
            parsed = parse_nixos_json(output)
            if parsed:
                return parsed, None

    output, error = _run(list_command(profile, config, hostname), timeout)
    if error is not None:
        return [], error
    return parse(output), None


def describe(generations: list[dict]) -> str:
    """A one-line summary for a prompt or a header."""
    if not generations:
        return "no generations found"
    current = next((g["id"] for g in generations if g["current"]), None)
    old = [g for g in generations if not g["current"]]
    part = f"{len(generations)} generation(s)"
    if current is not None:
        part += f", current is {current}"
    return f"{part}, {len(old)} removable"
