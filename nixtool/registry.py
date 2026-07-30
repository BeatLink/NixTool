"""Traversal helpers for the command tree defined in commands.py.

The tree is the single source of truth for both the TUI and the CLI. Every
runnable command and every category carries a stable ``id``; the CLI addresses
commands by that id (optionally qualified as ``category/id``) so display names
can change without breaking scripts.
"""

from .commands import all_commands


def is_category(node) -> bool:
    return bool(node.get("category"))


def iter_commands(node=None, path=()):
    """Yield ``(path, node)`` for every runnable (non-category) command.

    ``path`` is the tuple of category ids the command is nested under.
    """
    node = all_commands if node is None else node
    for child in node.get("commands", []):
        if not isinstance(child, dict) or "id" not in child:
            # Raw shell strings and callables inside a command's own body are
            # steps, not addressable commands.
            continue
        if is_category(child):
            yield from iter_commands(child, path + (child["id"],))
        else:
            yield path, child


def iter_categories(node=None, path=()):
    """Yield ``(path, node)`` for every category in the tree."""
    node = all_commands if node is None else node
    for child in node.get("commands", []):
        if isinstance(child, dict) and is_category(child):
            yield path + (child["id"],), child
            yield from iter_categories(child, path + (child["id"],))


def qualified_id(path, node) -> str:
    """The fully qualified address of a command, e.g. ``maintenance/rebuild``."""
    return "/".join(path + (node["id"],))


def find_command(identifier: str):
    """Resolve a command by bare id or by qualified ``category/id`` path.

    Returns the command dict, or raises LookupError with a message that lists
    the ambiguity or the closest available ids.
    """
    identifier = identifier.strip().strip("/")
    matches = []
    for path, node in iter_commands():
        qualified = qualified_id(path, node)
        if identifier in (qualified, node["id"]):
            matches.append((qualified, node))

    if len(matches) == 1:
        return matches[0][1]
    if len(matches) > 1:
        options = ", ".join(sorted(q for q, _ in matches))
        raise LookupError(
            f"'{identifier}' is ambiguous; qualify it as one of: {options}"
        )

    known = sorted(qualified_id(p, n) for p, n in iter_commands())
    close = [k for k in known if identifier in k]
    hint = close or known
    raise LookupError(
        f"unknown command '{identifier}'. Available: {', '.join(hint)}"
    )


def collect_variables(node) -> dict:
    """Every menu_variable required by a command, including nested sub-commands.

    Mirrors the TUI's recursive collection so both front ends prompt for and
    validate exactly the same variable set.
    """
    variables = {}
    if "menu_variables" in node:
        variables.update(node["menu_variables"])
    for child in node.get("commands", []):
        if isinstance(child, dict):
            variables.update(collect_variables(child))
    return variables


def needs_host(node) -> bool:
    """Whether a command references a host, directly or through a sub-command."""
    if node.get("run_on_remote"):
        return True
    for child in node.get("commands", []):
        if isinstance(child, str) and ("<HOSTNAME>" in child or "<HOSTURL>" in child):
            return True
        if isinstance(child, dict) and needs_host(child):
            return True
    return False


def is_destructive(node) -> bool:
    """Commands flagged destructive, or carrying warning instructions, are gated.

    Categories inherit nothing; a parent command is destructive if any of the
    sub-commands it runs is.
    """
    if node.get("destructive") or "instructions" in node:
        return True
    return any(
        isinstance(child, dict) and is_destructive(child)
        for child in node.get("commands", [])
    )
