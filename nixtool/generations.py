"""Reading and pruning nix profile generations.

``nix-env --list-generations`` is the only way to find out what exists, so the
front ends run it and parse it rather than asking the user to know. Everything
here is pure enough to test without a nix store: the commands are built as
strings and the output is parsed from text.
"""

import re
import shlex
import subprocess

# The system profile needs root; the user profile is the caller's own.
PROFILES = {
    "system": "sudo nix-env --profile /nix/var/nix/profiles/system",
    "user": "nix-env",
}

# "   42   2026-03-11 17:55:02   (current)"
GENERATION_LINE = re.compile(
    r"^\s*(\d+)\s+(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s*(\(current\))?\s*$"
)

# What nix-env --delete-generations accepts: an explicit list, everything but
# the current one, or everything older than the last N. Checked because the
# value reaches a shell, and on the CLI it comes from a flag.
SELECTION = re.compile(r"^(old|\+\d+|\d+( +\d+)*)$")

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


def read(profile: str, config: dict, hostname: str | None, timeout: int = 30):
    """Run the listing for one profile and parse it.

    Returns ``(generations, error)``: a failure to reach the host is reported
    rather than raised, so one unreachable profile still lets the other show.
    """
    command = list_command(profile, config, hostname)
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return [], f"timed out after {timeout}s: {command}"
    if result.returncode != 0:
        detail = (result.stderr or "").strip().splitlines()
        return [], (detail[-1] if detail else f"exit code {result.returncode}")
    return parse(result.stdout), None


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
