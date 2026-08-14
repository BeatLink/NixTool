# NixTool

A tool for managing flake-based NixOS installations, built with Textual and Python.

NixTool has two front ends over one shared command tree:

- **A TUI** — menu-driven, for interactive use. Run `nixtool` with no arguments.
- **A CLI** — fully scriptable, for automation and remote runs. Run `nixtool <subcommand>`.

Both read the same configuration and resolve commands identically, so anything
you can do in the menus you can also do from a script.

---

## Contents

- [Installation](#installation)
- [Configuration](#configuration)
- [Quick start](#quick-start)
- [The TUI](#the-tui)
- [The CLI](#the-cli)
  - [Global options](#global-options)
  - [`nixtool list`](#nixtool-list)
  - [`nixtool show`](#nixtool-show)
  - [`nixtool hosts`](#nixtool-hosts)
  - [`nixtool config`](#nixtool-config)
  - [`nixtool run`](#nixtool-run)
  - [`nixtool tui`](#nixtool-tui)
- [Command reference](#command-reference)
- [Variables](#variables)
- [Secrets](#secrets)
- [Safety and destructive commands](#safety-and-destructive-commands)
- [Exit codes](#exit-codes)
- [Recipes](#recipes)
- [Development](#development)

---

## Installation

### With `nix run` (no installation)

The flake exposes NixTool as its default app, so you can run it without
installing anything:

```sh
nix run github:BeatLink/NixTool -- list
```

Everything after `--` is passed straight through to NixTool, so every
subcommand and flag in this README works unchanged:

```sh
# The TUI — no arguments after the separator
nix run github:BeatLink/NixTool

# Any CLI subcommand
nix run github:BeatLink/NixTool -- show rebuild
nix run github:BeatLink/NixTool -- run rebuild --host alpha --action switch

# Point at a config explicitly, since the flake runs from the store
nix run github:BeatLink/NixTool -- -c /etc/nixtool/nixtool-config.json hosts
```

The `--` separator is required. Without it, `nix` consumes the flags itself:
`nix run … --host alpha` fails with an unknown-flag error from Nix, not NixTool.

From a local checkout, use `.` as the flake reference:

```sh
nix run . -- list
nix run . -- run flake-update --dry-run
```

> **Note:** `nix run` on a git checkout only sees files that git tracks. After
> adding a new module, `git add` it or the build will silently use the previous
> file set and fail with `ModuleNotFoundError`.

To install it into a profile instead of running it ad hoc:

```sh
nix profile install github:BeatLink/NixTool
```

Or add it to a NixOS configuration:

```nix
{
  inputs.nixtool.url = "github:BeatLink/NixTool";

  # in your host's module:
  environment.systemPackages = [ inputs.nixtool.packages.${pkgs.system}.default ];
}
```

### With pip

Inside the provided Nix shell, which brings in the required system binaries
(`sgdisk`, `zfs`, `parted`, `lsblk`, `wget`, `sshpass`, `nix-inspect`):

```sh
nix-shell          # creates ./.venv and runs `pip install -e .`
```

Or install the package directly:

```sh
pip install -e .
```

Either way you get a `nixtool` executable; `python -m nixtool` is equivalent.

A development shell with the dependencies but no install is also available:

```sh
nix develop
```

---

## Configuration

NixTool reads a JSON config file describing your flake and your hosts:

```json
{
  "flake_path": "/etc/nixos",
  "user": "admin",
  "hosts": {
    "alpha": "10.0.0.1",
    "beta":  "10.0.0.2",
    "laptop": "192.168.1.50"
  }
}
```

| Key | Meaning |
|---|---|
| `flake_path` | Path to your flake. Substituted for `<FLAKEPATH>`, and used as the working directory for every command. |
| `user` | SSH user for remote operations. Substituted for `<USER>`. |
| `hosts` | Map of hostname → address. The hostname becomes the flake attribute (`<FLAKEPATH>#<HOSTNAME>`); the address is substituted for `<HOSTURL>`. |

### Where the config is found

The first file that exists wins:

1. `--config PATH` / `-c PATH`
2. `$NIXTOOL_CONFIG`
3. `./nixtool-config.json` (current directory)
4. `$XDG_CONFIG_HOME/nixtool/nixtool-config.json` (usually `~/.config/nixtool/`)
5. `~/.nixtool-config.json`
6. `/etc/nixtool/nixtool-config.json`

Check which one is in use with `nixtool config`. A missing config is only an
error when you passed `-c` explicitly, or when you run a command that needs a
host or flake path. Malformed JSON is always an error, so a typo can never
silently resolve placeholders to empty strings.

---

## Quick start

```sh
nixtool config                 # confirm which config file is loaded
nixtool hosts                  # list configured hosts
nixtool list                   # list every available command
nixtool show rebuild           # explain one command and its options

# Preview before running — always safe
nixtool run rebuild --host alpha --action switch --dry-run

# Actually run it
nixtool run rebuild --host alpha --action switch
```

---

## The TUI

Running `nixtool` with no subcommand opens the interactive interface. It walks
you through the same steps the CLI takes as flags:

```
┌─ NixTool ────────────────────────────────────────────────┐
│  CLI tool for managing flake based NixOS installations   │
├──────────────────────────────────────────────────────────┤
│ ┌ Type to filter commands… ────────────────────────────┐ │
│ └──────────────────────────────────────────────────────┘ │
│ ┌────────────────┐ ┌───────────────────────────────────┐ │
│ │ All            │ │ Run All Tasks                     │ │
│ │ Maintenance    │ │ Run Nix Flake Update              │ │
│ │ Installation   │ │ Export Dconf Settings             │ │
│ │ Formatting     │ │ Run Nixos Rebuild                 │ │
│ │                │ │ Run Nixos Rebuild (Attached Disk) │ │
│ │                │ │ Preview Old Generations           │ │
│ │                │ │ …                                 │ │
│ └────────────────┘ └───────────────────────────────────┘ │
│ enter run · ↑↓ move · tab categories · esc back          │
└──────────────────────────────────────────────────────────┘
```

The sidebar narrows the list to **Maintenance**, **Installation** or
**Formatting**; **All** shows every command at once. The filter box keeps
keyboard focus, so ↑/↓ move through the commands while you type — the match is a
subsequence, so `rgen` finds *Remove Old Generations*. Tab moves to the sidebar
to change category. Filtering searches the category name too, so typing
`install` narrows the list without leaving **All**.

After choosing a command the TUI collects, in order:

1. **Instructions** — a warning screen for destructive commands; press Continue.
2. **Host** — the host list, including **All Hosts** to run against every
   configured host in turn. It comes before the variables because a value can
   depend on which machine it is for.
3. **Variables** — one screen per variable: a menu for choices, a masked field
   for passwords, a multi-line field for keys, a live device list for disks, or
   the [generation picker](#the-generation-picker).
4. **The runner** — shows the exact commands that will run, waits for **Start**,
   then streams output live. A failure stops the queue. **Return** goes back to
   the menu.

The `Inspect Nix Config` entry suspends the TUI and hands the terminal to
[nix-inspect](https://github.com/bluskript/nix-inspect) for browsing your
configuration, restoring the menu on exit. It is pointed at `flake_path`, since
nix-inspect otherwise loads `/etc/nixos` — the running system's configuration
rather than the flake nixtool manages.

---

## The CLI

```
nixtool [-c CONFIG] <subcommand> [options]

  tui       launch the interactive interface (the default with no subcommand)
  list      list available commands
  show      describe a command, its variables and its options
  hosts     list configured hosts
  config    show the resolved configuration and where it came from
  run       run a command
```

### Global options

| Option | Meaning |
|---|---|
| `-c`, `--config PATH` | Config file to use. Valid before or after the subcommand. |
| `-h`, `--help` | Help for any level, e.g. `nixtool run rebuild --help`. |

### `nixtool list`

Lists every command, grouped by category, marking those that are destructive or
that target a host.

```
$ nixtool list

Maintenance  (maintenance)
  maintenance/run-all                Run All Tasks  [destructive, host]
  maintenance/flake-update           Run Nix Flake Update
  maintenance/export-dconf           Export Dconf Settings
  maintenance/rebuild                Run Nixos Rebuild  [host]
  maintenance/rebuild-offline        Run Nixos Rebuild (Attached Disk)  [destructive, host]
  maintenance/preview-generations    Preview Old Generations  [host]
  maintenance/purge-generations      Remove Old Generations  [destructive, host]
  maintenance/garbage-collect        Run Garbage Collection  [destructive, host]
  maintenance/manage-generations     Manage Old Generations  [destructive, host]
  maintenance/unpersisted            Report Unpersisted Data  [host]
  maintenance/inspect                Inspect Nix Config (nix-inspect)

Installation  (install)
  install/install-nixos              Install NixOS (Anywhere)  [destructive, host]
  install/install-local              Install NixOS (Local Disk)  [destructive, host]

Formatting  (formatting)
  formatting/format-data-drive       Format Data Drive (ZFS on GPT)  [destructive, host]
  formatting/flash-towboot           Flash Tow-Boot to SD Card  [destructive]
  formatting/format-sd-data          Format SD Card Data Partition (ZFS)  [destructive, host]
```

`nixtool list --json` emits the same data as JSON, including each command's
variables — useful for shell completion or wrapper scripts.

### `nixtool show`

```
$ nixtool show rebuild
Run Nixos Rebuild

Build and activate a host configuration from the flake.

Destructive: no
Targets a host: yes

Variables:
  ACTION             (list)  --action VALUE
      switch         switch - Activate config and save to bootloader
      test           test - Activate config but reset next boot
      boot           boot - Activate config on next boot
      dry-activate   dry-activate - Build config but only show changes
      build-vm       build-vm - Build Test VM
      rollback       rollback - Rollback to previous configuration
```

For destructive commands, `show` also prints the full warning text.

### `nixtool hosts`

```
$ nixtool hosts
Hosts from /etc/nixtool/nixtool-config.json:
  alpha                10.0.0.1
  beta                 10.0.0.2
```

### `nixtool config`

```
$ nixtool config
Config file: /etc/nixtool/nixtool-config.json
  flake_path: /etc/nixos
  user:       admin
  hosts:      2 configured
```

With no config found, it lists every path it searched.

### `nixtool run`

```
nixtool run <command> [--host NAME ...] [variable flags] [options]
```

Commands are addressed by id, either bare (`rebuild`) or qualified
(`maintenance/rebuild`). Qualify only if a bare id is ever ambiguous.

| Option | Meaning |
|---|---|
| `--host NAME` | Target host. Repeat for several: `--host alpha --host beta`. |
| `--all-hosts` | Run against every configured host, in turn. |
| `-n`, `--dry-run` | Print the resolved commands and exit without running anything. |
| `-y`, `--yes` | Skip the confirmation prompt for destructive commands. |
| `-q`, `--quiet` | Suppress the plan and progress banners; pass through command output only. |
| `--keep-going` | Continue after a failing command instead of stopping. |
| `--non-interactive` | Never prompt; fail if a value is missing. |
| `--set KEY=VALUE` | Set a variable by name (non-secret only). |
| `--set-file KEY=PATH` | Read a variable from a file, or stdin with `-`. |

Commands that target a host require `--host` or `--all-hosts`; commands that do
not reference a host ignore both.

### `nixtool tui`

Launches the interactive interface explicitly. Identical to running `nixtool`
with no arguments, but lets you pass `-c` unambiguously.

---

## Command reference

| Id | Does | Host | Destructive | Variables |
|---|---|:--:|:--:|---|
| `flake-update` | `nix flake update --refresh` | – | – | – |
| `export-dconf` | Dumps every dconf path listed in the flake's `dconf-settings.json` files back into the flake | – | – | – |
| `rebuild` | `nixos-rebuild` against `<FLAKEPATH>#<HOSTNAME>` over SSH | ✓ | – | `ACTION` |
| `rebuild-offline` | Activates a generation on a host whose disk is attached to this machine | ✓ | ✓ | `ENCRYPTION_KEY` |
| `preview-generations` | Lists system and user generations, changing nothing | ✓ | – | – |
| `purge-generations` | Deletes all but the current generation | ✓ | ✓ | – |
| `garbage-collect` | `nix-collect-garbage -d` | ✓ | ✓ | – |
| `manage-generations` | Lists a host's generations, removes the ones you pick, then optionally collects garbage | ✓ | ✓ | `SYSTEM_GENERATIONS`, `USER_GENERATIONS`, `RUN_GC` |
| `run-all` | Flake update → rebuild → preview → purge → GC | ✓ | ✓ | `ACTION` |
| `unpersisted` | Lists what sits on the rolled-back datasets and would not survive a reboot | ✓ | – | – |
| `inspect` | Launches the nix-inspect TUI against `flake_path` | – | – | – |
| `install-nixos` | Provisions a host with nixos-anywhere, wiping its disks | ✓ | ✓ | `SSH_TARGET`, `SSH_PASSWORD`, `SSH_HOST_KEY`, `SSH_INITRD_KEY`, `ENCRYPTION_KEY` |
| `install-local` | Installs a host onto a disk attached to this machine | ✓ | ✓ | `TARGET_DISK`, `SSH_HOST_KEY`, `SSH_INITRD_KEY`, `ENCRYPTION_KEY` |
| `format-data-drive` | Wipes a drive, creates an encrypted ZFS pool, optionally mirrored | ✓ | ✓ | `DATA_DRIVE`, `MIRROR_DRIVE`, `PASSPHRASE` |
| `flash-towboot` | Wipes an SD card and writes Tow-Boot to it, leaving the rest unpartitioned | – | ✓ | `DATA_DRIVE`, `TOWBOOT_VERSION` |
| `format-sd-data` | Creates the encrypted ZFS data pool on partition 2 of a Tow-Boot SD card | ✓ | ✓ | `DATA_DRIVE`, `PASSPHRASE` |

`run-all` is a composite: it expands into the sub-commands it contains,
resolved as a single queue.

### The generation picker

`manage-generations` does not delete by a rule you have to trust — it lists what
the host actually holds and lets you choose. In the TUI that is a screen:

```
┌ Generations on alpha ──────────────────────────────────────────┐
│ System profile — 4 generation(s), current is 4, 3 removable    │
│ ┌────────────────────────────┐ ┌─────────────────────────────┐ │
│ │ [ ]     1  2025-11-02 09:14│ │ [ ]     1  2025-11-02 09:14 │ │
│ │ [X]     2  2025-12-18 22:03│ │ [ ]     2  2025-12-18 22:03 │ │
│ │ [X]     3  2026-01-30 08:41│ │ [ ]     3  2026-01-30 08:41 │ │
│ └────────────────────────────┘ └─────────────────────────────┘ │
│   2 generation(s) selected for deletion                        │
│   space toggle · a all removable · n none                      │
│            [ Skip deletion ]  [ Continue ]                     │
└────────────────────────────────────────────────────────────────┘
```

The current generation is not listed: `nix-env` refuses to delete it, so
offering it could only mislead. Both profiles are filled by this one screen,
after which you are asked whether to collect garbage as well.

The CLI asks the same question with the same listing:

```
$ nixtool run manage-generations --host alpha

Select generations to remove (system profile on alpha)
      1   2025-11-02 09:14:33
      2   2025-12-18 22:03:11
      3   2026-01-30 08:41:07
      4   2026-03-11 17:55:02   <- current, cannot be removed
Generations to remove (numbers, 'old', '+N', or 'none'):
```

Anything `nix-env --delete-generations` accepts works, so a script can keep the
blunt form and skip the prompt entirely:

```sh
nixtool run manage-generations --host alpha \
  --system-generations old --user-generations none --run-gc yes --yes
```

Selecting **All Hosts** falls back to `old` for every host, since generation
numbers mean different things on different machines.

If a host cannot be reached, the picker says so and selects nothing for that
profile rather than guessing.

---

## Variables

Each variable becomes a flag derived from its name: `DATA_DRIVE` → `--data-drive`,
`ACTION` → `--action`. Values may come from, in priority order:

1. The generated flag (`--action switch`)
2. `$NIXTOOL_VAR_<NAME>` (e.g. `NIXTOOL_VAR_ACTION=switch`)
3. `--set NAME=VALUE`
4. An interactive prompt, when attached to a terminal

Variable types:

| Type | Behaviour |
|---|---|
| `list` | Restricted to declared options; invalid values are rejected up front. |
| `text` | Free text. |
| `disk` | A device path. Use `nixtool run ... --dry-run` to check what you typed; the TUI offers a live `lsblk` picker. `MIRROR_DRIVE` accepts `none`. |
| `password` | Secret — see below. |
| `textarea` | Multi-line secret (SSH keys) — see below. |
| `uuid` | Generated automatically. Pass the flag explicitly to pin it when re-running against an existing pool. |

Placeholders substituted into every command: `<FLAKEPATH>`, `<HOSTNAME>`,
`<HOSTURL>`, `<USER>`, plus `<NAME>` for each variable.

---

## Secrets

Passwords, SSH host keys and ZFS passphrases are **never accepted as flag
values**. A value on the command line would be recorded in your shell history
and visible in `ps` output to every user on the machine for as long as the
command runs. `--set PASSPHRASE=...` is rejected with an explanatory error.

Supply secrets one of three ways:

```sh
# From a file
nixtool run format-data-drive --host alpha \
  --data-drive /dev/sdb --mirror-drive none \
  --passphrase-file /run/secrets/zfs-passphrase

# From stdin, with '-'
pass show zfs/alpha | nixtool run format-data-drive --host alpha \
  --data-drive /dev/sdb --mirror-drive none --passphrase-file -

# From the environment
NIXTOOL_VAR_PASSPHRASE="$(pass show zfs/alpha)" \
  nixtool run format-data-drive --host alpha \
  --data-drive /dev/sdb --mirror-drive none
```

On a terminal with nothing supplied, NixTool prompts with hidden input and
asks for confirmation. Secrets are masked as `********` everywhere a plan is
printed, including `--dry-run`.

A single trailing newline is stripped from files and stdin, so
`echo secret > file` works as expected. Interior newlines are preserved, which
is what multi-line SSH keys need.

---

## Safety and destructive commands

Commands that erase disks, delete generations, or collect garbage are marked
destructive. Before any of them runs, NixTool prints the fully resolved command
list and then requires confirmation:

```
$ nixtool run garbage-collect --host alpha
Command: Run Garbage Collection

The following 1 command(s) will be executed:

  on alpha:
    1. sudo nix-collect-garbage -d

Proceed? Type 'yes' to continue:
```

Without a terminal — in a script, a CI job, or a cron entry — the prompt cannot
be answered, so the command refuses to run unless you pass `--yes`:

```
error: this command is destructive and requires --yes when not running on a terminal.
```

A composite command is destructive if any step it contains is, so `run-all` is
gated even though a flake update on its own is not.

Reading is never gated. Listing a host's generations is a query, not a change,
so the [picker](#the-generation-picker) runs it without asking; only the
deletion it produces goes through the confirmation and the runner.

`--dry-run` never executes anything and never prompts. Use it first, especially
for disk operations where a mistyped device path is unrecoverable.

Execution stops at the first failing command so a broken step cannot cascade;
`--keep-going` overrides this.

---

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Success. |
| `1` | A command failed, or the config was missing or malformed. |
| `2` | Usage error: unknown command, missing or invalid variable, unknown host. |
| `3` | Aborted at the confirmation prompt, or refused for want of `--yes`. |
| `130` | Interrupted with Ctrl-C. |

---

## Recipes

**Update everything, everywhere, unattended:**

```sh
nixtool run run-all --all-hosts --action switch --yes --quiet
```

**Rebuild two hosts and stop on the first failure:**

```sh
nixtool run rebuild --host alpha --host beta --action switch
```

**Check what a rebuild would change, without touching anything:**

```sh
nixtool run rebuild --host alpha --action dry-activate
```

**Nightly garbage collection from cron** — note `--yes`, since cron has no terminal:

```sh
0 3 * * *  nixtool -c /etc/nixtool/nixtool-config.json \
             run manage-generations --all-hosts \
             --system-generations old --user-generations old --run-gc yes \
             --yes --quiet
```

**Provision a new machine:**

```sh
nixtool run install-nixos --host newbox \
  --ssh-target root@192.168.1.99 \
  --ssh-password-file /run/secrets/install-pw \
  --ssh-host-key-file   ./keys/newbox_ed25519 \
  --ssh-initrd-key-file ./keys/newbox_initrd_ed25519 \
  --encryption-key-file /run/secrets/newbox-luks
```

**Inspect a plan before committing to it:**

```sh
nixtool run format-data-drive --host alpha \
  --data-drive /dev/disk/by-id/ata-... --mirror-drive none \
  --passphrase-file /run/secrets/pw --dry-run
```

**Discover commands programmatically:**

```sh
nixtool list --json | jq -r '.[] | select(.destructive) | .id'
```

---

## Development

Project layout:

| File | Role |
|---|---|
| `commands.py` | The command tree — the single source of truth for both front ends. |
| `registry.py` | Traversal: finding commands, collecting variables, detecting host use and destructiveness. |
| `resolver.py` | Turning a command plus variables into a concrete shell queue. |
| `executor.py` | Headless execution with streamed output. |
| `secrets.py` | Sourcing values while keeping secrets off the command line. |
| `config.py` | Locating and loading the config file. |
| `cli.py` | Argument parsing and the subcommands. |
| `main.py` | The Textual TUI. |

Run the tests:

```sh
nix-shell -p python3Packages.pytest python3Packages.textual --run "python -m pytest tests/ -q"
```

Build and run the flake package:

```sh
nix build .           # result/bin/nixtool
nix run . -- list     # build and run in one step
nix develop           # shell with dependencies, nothing installed
```

Remember that `nix build`/`nix run` on a git checkout only see tracked files, so
`git add` new modules before building.

### Adding a command

Add a dict to `commands.py` and list it in a category. It appears in the TUI
menus and gains a `nixtool run` subcommand with generated flags automatically:

```python
my_command = {
    "id": "my-command",                    # stable; the CLI address
    "name": "My Command",                  # shown in the TUI menu
    "description": "What it does.",        # shown in `list` and `show`
    "destructive": True,                   # gates it behind --yes
    "commands": ["echo <FLAKEPATH> <HOSTNAME> <MY_VAR>"],
    "menu_variables": {
        "MY_VAR": {"title": "Enter a value", "type": "text"},
    },
    "run_on_remote": True,                 # requires a host
}
```

Give every command an `id` and list it in a category — a command that only
exists nested inside another is reachable from neither front end. The test
suite checks this.
