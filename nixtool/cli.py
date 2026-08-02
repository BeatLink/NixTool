"""The nixtool command line interface.

Every runnable command in the tree becomes a subcommand of ``nixtool run``,
with one generated flag per menu variable. Secrets never take a flag value;
see secrets.py.
"""

import argparse
import json
import pathlib
import sys

from . import config as config_mod
from . import executor, registry, resolver, secrets

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_CONFIRM = 3


def flag_for(name: str) -> str:
    """The CLI flag generated for a variable, e.g. DATA_DRIVE -> --data-drive."""
    return "--" + name.lower().replace("_", "-")


def dest_for(name: str) -> str:
    return "var_" + name.lower()


def add_variable_flags(parser: argparse.ArgumentParser, variables: dict) -> None:
    """Attach one flag per menu variable, routing secrets to file-based input."""
    for name, spec in sorted(variables.items()):
        if spec.get("type") == "uuid":
            # Generated automatically; still overridable for re-runs.
            parser.add_argument(
                flag_for(name),
                dest=dest_for(name),
                metavar="VALUE",
                help=f"{name} (generated automatically if omitted)",
            )
            continue

        title = spec.get("title") or name

        if secrets.is_secret(spec):
            parser.add_argument(
                flag_for(name) + "-file",
                dest=dest_for(name) + "_file",
                metavar="PATH",
                help=f"{title} — read from PATH ('-' for stdin); "
                     f"or set {secrets.env_var_name(name)}",
            )
            continue

        choices = list(spec.get("options", {})) if spec.get("type") == "list" else None
        parser.add_argument(
            flag_for(name),
            dest=dest_for(name),
            metavar="VALUE",
            choices=choices,
            help=title,
        )


def collect_values(
    node: dict,
    args: argparse.Namespace,
    interactive: bool,
    cfg: dict | None = None,
    hostname: str | None = None,
) -> dict:
    """Gather every variable value from flags, files, config, env, or prompts.

    Paths declared in the config file rank below anything given explicitly for
    this run, so a one-off flag or environment variable still wins, but above
    the interactive prompt, so a fully configured host runs unattended.
    """
    variables = registry.collect_variables(node)
    declared_files = config_mod.value_files(cfg or {}, hostname)
    declared_literals = config_mod.declared_values(cfg or {}, hostname)
    values = {}

    for name, spec in variables.items():
        if spec.get("type") == "uuid":
            explicit = getattr(args, dest_for(name), None)
            if explicit:
                values[name] = explicit
            continue

        if secrets.is_secret(spec):
            path = getattr(args, dest_for(name) + "_file", None)
            if path:
                values[name] = secrets.read_value_file(path)
                continue
        else:
            inline = getattr(args, dest_for(name), None)
            if inline is not None:
                resolver.validate_choice(name, spec, inline)
                values[name] = inline
                continue

        from_env = secrets.from_environment(name)
        if from_env is not None:
            resolver.validate_choice(name, spec, from_env)
            values[name] = from_env
            continue

        # --set/--set-file for names not covered by a generated flag, and as an
        # escape hatch for scripts that build flags dynamically.
        for key, value in getattr(args, "set_values", None) or []:
            if key == name:
                secrets.reject_inline_secret(name, spec)
                resolver.validate_choice(name, spec, value)
                values[name] = value
        for key, path in getattr(args, "set_files", None) or []:
            if key == name:
                values[name] = secrets.read_value_file(path)
        if name in values:
            continue

        declared = declared_files.get(name)
        if declared:
            value = secrets.read_value_file(declared)
            resolver.validate_choice(name, spec, value)
            values[name] = value
            continue

        literal = declared_literals.get(name)
        if literal is not None:
            secrets.reject_inline_secret(name, spec)
            resolver.validate_choice(name, spec, literal)
            values[name] = literal
            continue

        if interactive:
            value = secrets.prompt_for(name, spec)
            resolver.validate_choice(name, spec, value)
            values[name] = value

    return resolver.generate_auto_values(variables, values)


def parse_assignment(text: str) -> tuple[str, str]:
    if "=" not in text:
        raise argparse.ArgumentTypeError(
            f"expected KEY=VALUE, got '{text}'"
        )
    key, _, value = text.partition("=")
    key = key.strip()
    if not key:
        raise argparse.ArgumentTypeError(f"missing key in '{text}'")
    return key, value


def print_plan(plan, node, values_by_host, stream=None) -> None:
    """Show the resolved commands, with secret values masked.

    Values are per host, since config-declared credential files may differ
    between them. Identical values across hosts are printed once so the common
    single-host case reads the same as it always has.
    """
    # Resolved at call time, not import time, so redirected output is honoured.
    stream = sys.stdout if stream is None else stream
    variables = registry.collect_variables(node)
    print(f"Command: {node['name']}", file=stream)

    def show(values, label=None) -> None:
        if not values:
            return
        heading = "Variables:" if label is None else f"Variables ({label}):"
        print(heading, file=stream)
        for name in sorted(values):
            spec = variables.get(name, {})
            shown = secrets.redact(name, spec, values[name])
            print(f"  {name} = {shown}", file=stream)

    distinct = {tuple(sorted(v.items())) for v in values_by_host.values()}
    if len(distinct) <= 1:
        show(next(iter(values_by_host.values()), {}))
    else:
        for hostname, values in values_by_host.items():
            show(values, hostname)
    print(f"\nThe following {len(plan)} command(s) will be executed:", file=stream)
    current_host = object()
    for index, (hostname, command) in enumerate(plan, start=1):
        if hostname != current_host:
            current_host = hostname
            if hostname:
                print(f"\n  on {hostname}:", file=stream)
        print(f"  {index:>3}. {command}", file=stream)


def confirm(node: dict, assume_yes: bool, stream=None) -> bool:
    """Gate destructive commands behind --yes or a TTY confirmation."""
    stream = sys.stderr if stream is None else stream
    if assume_yes or not registry.is_destructive(node):
        return True
    if not sys.stdin.isatty():
        print(
            "\nerror: this command is destructive and requires --yes "
            "when not running on a terminal.",
            file=stream,
        )
        return False
    answer = input("\nProceed? Type 'yes' to continue: ").strip().lower()
    return answer == "yes"


def cmd_list(args) -> int:
    """List available commands."""
    if args.json:
        payload = []
        for path, node in registry.iter_commands():
            payload.append({
                "id": registry.qualified_id(path, node),
                "name": node["name"],
                "description": node.get("description", ""),
                "category": "/".join(path),
                "destructive": registry.is_destructive(node),
                "needs_host": registry.needs_host(node),
                "interactive": bool(node.get("interactive")),
                "variables": sorted(registry.collect_variables(node)),
            })
        print(json.dumps(payload, indent=2))
        return EXIT_OK

    for cat_path, category in registry.iter_categories():
        print(f"\n{category['name']}  ({'/'.join(cat_path)})")
        for path, node in registry.iter_commands(category, cat_path):
            marks = []
            if registry.is_destructive(node):
                marks.append("destructive")
            if registry.needs_host(node):
                marks.append("host")
            suffix = f"  [{', '.join(marks)}]" if marks else ""
            print(f"  {registry.qualified_id(path, node):<34} {node['name']}{suffix}")
    print()
    return EXIT_OK


def cmd_show(args) -> int:
    """Describe one command in detail."""
    try:
        node = registry.find_command(args.command)
    except LookupError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE

    variables = registry.collect_variables(node)
    print(f"{node['name']}")
    if node.get("description"):
        print(f"\n{node['description']}")
    print(f"\nDestructive: {'yes' if registry.is_destructive(node) else 'no'}")
    print(f"Targets a host: {'yes' if registry.needs_host(node) else 'no'}")

    if variables:
        print("\nVariables:")
        for name, spec in sorted(variables.items()):
            kind = spec.get("type", "list")
            if kind == "uuid":
                how = "generated automatically"
            elif secrets.is_secret(spec):
                how = f"{flag_for(name)}-file PATH | {secrets.env_var_name(name)}"
            else:
                how = f"{flag_for(name)} VALUE"
            print(f"  {name:<18} ({kind})  {how}")
            options = spec.get("options", {})
            for key, label in options.items():
                print(f"      {key:<14} {label}")

    if node.get("instructions"):
        print(f"\n{node['instructions'].strip()}")
    return EXIT_OK


def cmd_hosts(args) -> int:
    """List configured hosts."""
    try:
        cfg, path = config_mod.load_config(args.config)
    except config_mod.ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR

    host_map = config_mod.hosts(cfg)
    if args.json:
        print(json.dumps(host_map, indent=2))
        return EXIT_OK

    if not host_map:
        print("No hosts configured." + (f" (config: {path})" if path else ""))
        return EXIT_OK
    print(f"Hosts from {path}:" if path else "Hosts:")
    for name, url in host_map.items():
        print(f"  {name:<20} {url}")
    return EXIT_OK


def cmd_config(args) -> int:
    """Show the resolved configuration and where it came from."""
    try:
        cfg, path = config_mod.load_config(args.config)
    except config_mod.ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR

    if args.json:
        print(json.dumps({"path": str(path) if path else None, "config": cfg}, indent=2))
        return EXIT_OK

    if path is None:
        print("No config file found. Searched:")
        for candidate in config_mod.default_search_paths():
            print(f"  {candidate}")
        return EXIT_OK

    print(f"Config file: {path}")
    print(f"  flake_path: {cfg.get('flake_path', '(unset)')}")
    print(f"  user:       {cfg.get('user', '(unset)')}")
    print(f"  hosts:      {len(config_mod.hosts(cfg))} configured")
    return EXIT_OK


def cmd_run(args) -> int:
    """Resolve and execute a command."""
    try:
        node = registry.find_command(args.command)
    except LookupError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE

    try:
        cfg, _ = config_mod.load_config(args.config)
    except config_mod.ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR

    work_dir = cfg.get("flake_path")

    if node.get("interactive"):
        if args.dry_run:
            print(node["command"])
            return EXIT_OK
        return executor.run_interactive(node["command"], work_dir)

    interactive_ok = sys.stdin.isatty() and not args.non_interactive
    variables = registry.collect_variables(node)

    try:
        hostnames = resolver.target_hosts(node, cfg, args.host, args.all_hosts)
    except resolver.ResolutionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE

    # Values are gathered per host so config-declared credential files can name
    # a different key or password for each machine.
    plan = []
    values_by_host = {}
    for hostname in hostnames:
        try:
            values = collect_values(node, args, interactive_ok, cfg, hostname)
        except (secrets.SecretError, resolver.ResolutionError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return EXIT_USAGE

        missing = resolver.missing_variables(variables, values)
        if missing:
            details = []
            for name in sorted(missing):
                spec = variables[name]
                if secrets.is_secret(spec):
                    details.append(f"{name} ({flag_for(name)}-file PATH)")
                else:
                    details.append(f"{name} ({flag_for(name)} VALUE)")
            where = f" for {hostname}" if hostname else ""
            print(
                f"error: missing required variable(s){where}: {', '.join(details)}",
                file=sys.stderr,
            )
            return EXIT_USAGE

        values_by_host[hostname] = values
        try:
            plan.extend(resolver.build_plan(node, cfg, [hostname], values))
        except resolver.ResolutionError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return EXIT_USAGE

    if not plan:
        print("error: command resolved to an empty plan", file=sys.stderr)
        return EXIT_ERROR

    if args.dry_run:
        print_plan(plan, node, values_by_host)
        return EXIT_OK

    if not args.quiet:
        print_plan(plan, node, values_by_host)

    if not confirm(node, args.yes):
        print("Aborted.", file=sys.stderr)
        return EXIT_CONFIRM

    result = executor.run_plan(
        plan,
        work_dir=work_dir,
        quiet=args.quiet,
        keep_going=args.keep_going,
    )

    if not args.quiet:
        if result.ok:
            print(f"\nAll {result.total} command(s) succeeded.")
        else:
            print(
                f"\nFailed after {result.completed} of {result.total} command(s): "
                f"{result.failed_command}",
                file=sys.stderr,
            )
    return EXIT_OK if result.ok else EXIT_ERROR


def cmd_tui(args) -> int:
    """Launch the interactive Textual interface."""
    from . import run as run_tui

    path = config_mod.resolve_config_path(args.config)
    run_tui(config_path=path)
    return EXIT_OK


def add_common_flags(parser: argparse.ArgumentParser) -> None:
    # SUPPRESS keeps subparsers from overwriting a --config given before the
    # subcommand with their own None default, so `nixtool -c X run ...` and
    # `nixtool run ... -c X` behave identically.
    parser.add_argument(
        "-c", "--config",
        type=pathlib.Path,
        default=argparse.SUPPRESS,
        help="path to nixtool-config.json (default: search cwd, XDG config, /etc)",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nixtool",
        description="Manage flake-based NixOS installations.",
    )
    add_common_flags(parser)
    subparsers = parser.add_subparsers(dest="subcommand")

    p_tui = subparsers.add_parser("tui", help="launch the interactive interface")
    add_common_flags(p_tui)
    p_tui.set_defaults(func=cmd_tui)

    p_list = subparsers.add_parser("list", help="list available commands")
    p_list.add_argument("--json", action="store_true", help="output as JSON")
    p_list.set_defaults(func=cmd_list)

    p_show = subparsers.add_parser("show", help="describe a command")
    p_show.add_argument("command", help="command id, e.g. rebuild")
    p_show.set_defaults(func=cmd_show)

    p_hosts = subparsers.add_parser("hosts", help="list configured hosts")
    add_common_flags(p_hosts)
    p_hosts.add_argument("--json", action="store_true", help="output as JSON")
    p_hosts.set_defaults(func=cmd_hosts)

    p_config = subparsers.add_parser("config", help="show the resolved configuration")
    add_common_flags(p_config)
    p_config.add_argument("--json", action="store_true", help="output as JSON")
    p_config.set_defaults(func=cmd_config)

    p_run = subparsers.add_parser("run", help="run a command")
    run_subparsers = p_run.add_subparsers(dest="command", metavar="COMMAND")

    for path, node in registry.iter_commands():
        qualified = registry.qualified_id(path, node)
        aliases = [node["id"]] if node["id"] != qualified else []
        sub = run_subparsers.add_parser(
            qualified,
            aliases=aliases,
            help=node.get("description", node["name"]),
            description=node.get("description", node["name"]),
        )
        add_common_flags(sub)
        add_run_flags(sub)
        add_variable_flags(sub, registry.collect_variables(node))
        sub.set_defaults(func=cmd_run, command=qualified)

    # Kept so `nixtool run` with no command can print its own help.
    parser.run_parser = p_run
    return parser


def add_run_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--host", action="append", metavar="NAME",
        help="target host; repeat for several hosts",
    )
    parser.add_argument(
        "--all-hosts", action="store_true",
        help="run against every configured host",
    )
    parser.add_argument(
        "-y", "--yes", action="store_true",
        help="skip the confirmation prompt for destructive commands",
    )
    parser.add_argument(
        "-n", "--dry-run", action="store_true",
        help="print the resolved commands without running them",
    )
    parser.add_argument(
        "-q", "--quiet", action="store_true",
        help="suppress the plan and progress output",
    )
    parser.add_argument(
        "--keep-going", action="store_true",
        help="continue after a failing command instead of stopping",
    )
    parser.add_argument(
        "--non-interactive", action="store_true",
        help="never prompt; fail if a value is missing",
    )
    parser.add_argument(
        "--set", action="append", dest="set_values", type=parse_assignment,
        metavar="KEY=VALUE", help="set a variable (non-secret only)",
    )
    parser.add_argument(
        "--set-file", action="append", dest="set_files", type=parse_assignment,
        metavar="KEY=PATH", help="read a variable from a file ('-' for stdin)",
    )


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    # --config is SUPPRESSed so subparsers cannot clobber it; restore the
    # default here once, after both levels have been parsed.
    if not hasattr(args, "config"):
        args.config = None

    if args.subcommand is None:
        # Bare `nixtool` keeps its original behaviour: launch the TUI.
        return cmd_tui(args)

    if args.subcommand == "run" and getattr(args, "command", None) is None:
        parser.run_parser.print_help(sys.stderr)
        return EXIT_USAGE

    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
