"""Drops ignored keys from a `dconf dump` on stdin.

Run by path rather than with ``-m``, so it needs nothing but the standard library:

    dconf dump /org/gtk/settings/ | python dconf_filter.py /org/gtk/settings/ /org/gtk/settings/file-chooser/window-size

An ignored path ending in ``/`` drops that whole directory and everything under it.
"""
import sys


def section_dir(root, header):
    """The absolute dconf directory a dump's ``[section]`` header names."""
    name = header.strip("/")
    return root if not name else f"{root}{name}/"


def is_ignored(path, ignored):
    """Whether a key or directory path falls under any ignored entry."""
    return any(
        path == entry or (entry.endswith("/") and path.startswith(entry))
        for entry in ignored
    )


def filter_dump(text, root, ignored):
    """The dump with ignored keys removed and any section left empty dropped."""
    root = "/" + root.strip("/") + "/" if root.strip("/") else "/"
    sections = []
    for line in text.splitlines():
        if line.startswith("[") and line.rstrip().endswith("]"):
            sections.append([section_dir(root, line.strip()[1:-1]), line, []])
        elif sections and "=" in line:
            directory = sections[-1][0]
            if not is_ignored(directory + line.split("=", 1)[0], ignored):
                sections[-1][2].append(line)

    kept = [
        "\n".join([header, *keys])
        for directory, header, keys in sections
        if keys and not is_ignored(directory, ignored)
    ]
    return "\n\n".join(kept) + "\n" if kept else ""


if __name__ == "__main__":
    sys.stdout.write(filter_dump(sys.stdin.read(), sys.argv[1], sys.argv[2:]))
