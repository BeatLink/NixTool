import json
import pathlib
import shlex

nix_flake_update = {
    "id": "flake-update",
    "name": "Run Nix Flake Update",
    "description": "Refresh all flake inputs to their latest revisions.",
    "commands": [
        "nix flake update --refresh"
    ],
    "run_on_remote": False
}

def get_dconf_commands(flake_path):
    queue = []
    if not flake_path:
        return ["echo 'No flake_path configured; cannot locate dconf targets.'"]
    flake_root = pathlib.Path(flake_path)
    for config_path in flake_root.rglob("dconf-settings.json"):
        try:
            data = json.loads(config_path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            # Surfaced as a step rather than raised: one unreadable file should
            # not stop the other exports, and the message lands in the log.
            queue.append(f"echo {shlex.quote(f'Error processing {config_path}: {exc}')}")
            continue
        for dconf_path in data.get("dconf_exports", []):
            output_name = f"{dconf_path.strip('/').replace('/', '.')}.dconf"
            target_file = (config_path.parent / output_name).relative_to(flake_root)
            queue.append(
                f"dconf dump {shlex.quote(dconf_path)} > ./{shlex.quote(str(target_file))}"
            )
    return queue if queue else ["echo 'No localized dconf targets found.'"]

export_dconf = {
    "id": "export-dconf",
    "name": "Export Dconf Settings",
    "description": "Dump dconf paths listed in dconf-settings.json files into the flake.",
    "commands": [
        get_dconf_commands
    ],
    "run_on_remote": False
}

nix_rebuild = {
    "id": "rebuild",
    "name": "Run Nixos Rebuild",
    "description": "Build and activate a host configuration from the flake.",
    "commands": [
        "nixos-rebuild --sudo --no-reexec --show-trace --flake <FLAKEPATH>#<HOSTNAME> --target-host <USER>@<HOSTURL> <ACTION>"
    ],
    "menu_variables": {
        "ACTION": {
            "title": "Select a NixOS Rebuild action",
            "type": "list",
            "options": {
                "switch": "switch - Activate config and save to bootloader",
                "test": "test - Activate config but reset next boot",
                "boot": "boot - Activate config on next boot",
                "dry-activate": "dry-activate - Build config but only show changes",
                "build-vm": "build-vm - Build Test VM",
                "rollback": "rollback - Rollback to previous configuration"
            }
        }
    },
    "run_on_remote": True
}

nix_rebuild_offline = {
    "id": "rebuild-offline",
    "name": "Run Nixos Rebuild (Attached Disk)",
    "description": "Activate a configuration on a host whose disk is attached to this machine, for hosts that cannot be reached over SSH.",
    "instructions": """
# Run Nixos Rebuild (Attached Disk)

Activates a new generation on a host that cannot be reached over the network, by
writing directly to its disk while it is attached to **this** machine — a phone
exposed over USB mass storage, or a drive in a dock.

Nothing is wiped. The pool is imported, the closure is copied in, and
`switch-to-configuration boot` runs under `nixos-enter`, so the host comes up on
the new generation at its next boot.

### Where the mount layout comes from

It is not configured here. The pool name and every mountpoint are read from the
host's own disko configuration, via `config.system.build.mountScript`. That keeps
one declaration of the layout rather than a copy in this tool that can drift from
the one the host actually installs with.

That script imports with `-R /mnt` (altroot), so the import cannot land on top of
this machine's own filesystems.

The mount script is rebuilt with `hostPlatform` forced to this machine's system.
Built for the host's architecture it carries the host's `zfs` binaries, and a
foreign-architecture `zpool` under binfmt aborts — it issues ioctls to the running
kernel's ZFS module across an ABI it does not match. Only the tools change; the
layout is architecture-independent.

### Teardown

The pool is exported **by name**. Never use `-a` forms while a foreign disk is
attached: `zpool export -a` and `zfs unmount -a` act on this machine's own pools
too, and will silently unmount things the running system depends on.

### ⚠️ Before starting, for a PinePhone over Tow-Boot UMS

- **Take the phone out of the keyboard case.** The case supplies 5V over the pogo
  pins, which stops the USB-C port entering peripheral mode. Tow-Boot reports
  `Allwinner mUSB OTG (Peripheral)` while the host sees nothing.
- **Connect the cable before starting UMS.** Tow-Boot binds the gadget when the
  command starts; attaching a host afterwards does not re-enumerate.
""",
    "commands": [
        "printf '%s' <ENCRYPTION_KEY> > /tmp/encryption.key",
        "chmod 600 /tmp/encryption.key",
        "nix build --no-link --print-out-paths <FLAKEPATH>#nixosConfigurations.<HOSTNAME>.config.system.build.toplevel",
        "sudo \"$(nix build --impure --no-link --print-out-paths --expr '((builtins.getFlake \"<FLAKEPATH>\").nixosConfigurations.\"<HOSTNAME>\".extendModules { modules = [ ({ lib, ... }: { nixpkgs.hostPlatform = lib.mkForce builtins.currentSystem; }) ]; }).config.system.build.mountScript')\"",
        "sudo nix copy --no-check-sigs --to /mnt \"$(nix build --no-link --print-out-paths <FLAKEPATH>#nixosConfigurations.<HOSTNAME>.config.system.build.toplevel)\"",
        "sudo nix-env --profile /mnt/nix/var/nix/profiles/system --set \"$(nix build --no-link --print-out-paths <FLAKEPATH>#nixosConfigurations.<HOSTNAME>.config.system.build.toplevel)\"",
        "sudo nixos-enter --root /mnt -- /nix/var/nix/profiles/system/bin/switch-to-configuration boot",
        "sudo umount -R /mnt || true",
        "sudo zpool export root-pool-<HOSTNAME> || true",
        "sudo rm -f /tmp/encryption.key"
    ],
    "menu_variables": {
        "ENCRYPTION_KEY": {"title": "Enter Disk Encryption Key", "type": "password"}
    },
    # Uses <HOSTNAME> to pick the configuration, but every command runs here.
    "run_on_remote": False
}

nix_preview_generations = {
    "id": "preview-generations",
    "name": "Preview Old Generations",
    "description": "List system and user generations without deleting anything.",
    "commands": [
        'echo "---- <HOSTNAME> (system generations) ----" && sudo nix-env --profile /nix/var/nix/profiles/system --list-generations && echo "---- <HOSTNAME> (user generations) ----" && nix-env --list-generations'
    ],
    "run_on_remote": True
}

nix_purge_generations = {
    "id": "purge-generations",
    "name": "Remove Old Generations",
    "description": "Delete all but the current system and user generations.",
    "destructive": True,
    "commands": [
        "sudo nix-env --profile /nix/var/nix/profiles/system --delete-generations old",
        "nix-env --delete-generations old"
    ],
    "run_on_remote": True
}

nix_gc = {
    "id": "garbage-collect",
    "name": "Run Garbage Collection",
    "description": "Delete unreachable store paths.",
    "destructive": True,
    "commands": [
        "sudo nix-collect-garbage -d"
    ],
    "run_on_remote": True
}

nix_purge_generations_gc = {
    "id": "purge-generations-gc",
    "name": "Remove Old Generations & GC",
    "description": "Preview generations, delete old ones, then garbage collect.",
    "destructive": True,
    "commands": [
        nix_preview_generations,
        nix_purge_generations,
        nix_gc
    ]
}

run_all = {
    "id": "run-all",
    "name": "Run All Tasks",
    "description": "Flake update, rebuild, then prune generations and garbage collect.",
    "destructive": True,
    "commands": [
        nix_flake_update,
        nix_rebuild,
        nix_preview_generations,
        nix_purge_generations,
        nix_gc
    ],
    "run_on_remote": True
}

nixos_install = {
    "id": "install-nixos",
    "name": "Install NixOS (Anywhere)",
    "description": "Provision a host over SSH with nixos-anywhere, wiping its disks.",
    "destructive": True,
    # Secrets are written with `printf %s` into a directory created mode 700,
    # never with `echo` into a world-readable path: the keys must not be
    # readable by other local users for the window between write and chmod.
    "commands": [
        "rm -rf /tmp/nixtool-install-<HOSTNAME>",
        "mkdir -m 700 -p /tmp/nixtool-install-<HOSTNAME>/install/persistent/etc/ssh",
        "printf '%s\n' <SSH_HOST_KEY> > /tmp/nixtool-install-<HOSTNAME>/install/persistent/etc/ssh/ssh_host_ed25519_key",
        "printf '%s\n' <SSH_INITRD_KEY> > /tmp/nixtool-install-<HOSTNAME>/install/persistent/etc/ssh/ssh_initrd_host_ed25519_key",
        "printf '%s' <ENCRYPTION_KEY> > /tmp/nixtool-install-<HOSTNAME>/encryption.key",
        "chmod 600 /tmp/nixtool-install-<HOSTNAME>/install/persistent/etc/ssh/* /tmp/nixtool-install-<HOSTNAME>/encryption.key",
        "SSHPASS=<SSH_PASSWORD> nix run github:nix-community/nixos-anywhere -- --env-password --ssh-option \"UserKnownHostsFile=/dev/null\" --ssh-option \"GlobalKnownHostsFile=/dev/null\" --ssh-option \"StrictHostKeyChecking=no\" --extra-files /tmp/nixtool-install-<HOSTNAME>/install --disk-encryption-keys /tmp/encryption.key /tmp/nixtool-install-<HOSTNAME>/encryption.key --phases kexec,disko,install --no-substitute-on-destination --flake <FLAKEPATH>#<HOSTNAME> <SSH_TARGET>",
        "rm -rf /tmp/nixtool-install-<HOSTNAME>"
    ],
    "menu_variables": {
        "SSH_TARGET": {"title": "Enter SSH Target (root@ip)", "type": "text"},
        "SSH_PASSWORD": {"title": "SSH Password", "type": "password"},
        "SSH_HOST_KEY": {"title": "Enter SSH Host Key", "type": "textarea"},
        "SSH_INITRD_KEY": {"title": "Enter SSH InitRD Host Key", "type": "textarea"},
        "ENCRYPTION_KEY": {"title": "Enter Disk Encryption Key", "type": "password"}
    },
    # This command uses <HOSTNAME> and <SSH_ADDRESS>, and runs locally 
    # to orchestrate the remote install.
    "run_on_remote": False
}


nixos_install_local = {
    "id": "install-local",
    "name": "Install NixOS (Local Disk)",
    "description": "Install onto a disk attached to this machine, for targets that cannot be installed over SSH.",
    "destructive": True,
    "instructions": """
# Install NixOS (Local Disk)

Installs a host onto a block device attached to **this** machine, rather than
over SSH. Use this when the target cannot run `nixos-anywhere` — a phone exposed
over USB mass storage, a drive in a dock, or any device whose own OS lacks
`kexec`.

The host's disko configuration names the disk it will have once installed, which
is not the path it appears at while attached here. That device is overridden for
the partitioning step only; the system that gets installed is built from the
unmodified configuration, so it still refers to its own disk at runtime.

It then seeds `/persistent/etc/ssh` with the host keys, so the installed system
comes up with the identity it is expected to have, and runs `nixos-install`.

### ⚠️ WARNING
The selected disk is erased in its entirety. Check the device path carefully —
it is a disk on this machine, so a wrong path destroys local data.
""",
    "commands": [
        "rm -rf /tmp/nixtool-local-<HOSTNAME>",
        "mkdir -m 700 -p /tmp/nixtool-local-<HOSTNAME>/persistent/etc/ssh",
        "printf '%s\n' <SSH_HOST_KEY> > /tmp/nixtool-local-<HOSTNAME>/persistent/etc/ssh/ssh_host_ed25519_key",
        "printf '%s\n' <SSH_INITRD_KEY> > /tmp/nixtool-local-<HOSTNAME>/persistent/etc/ssh/ssh_initrd_host_ed25519_key",
        # disko reads the passphrase from this exact path, named by keylocation in
        # the host's zfs dataset options, so it cannot live under the temp dir.
        "printf '%s' <ENCRYPTION_KEY> > /tmp/encryption.key",
        "chmod 600 /tmp/encryption.key /tmp/nixtool-local-<HOSTNAME>/persistent/etc/ssh/ssh_host_ed25519_key /tmp/nixtool-local-<HOSTNAME>/persistent/etc/ssh/ssh_initrd_host_ed25519_key",
        # Build the whole system BEFORE touching the disk. Building can take hours
        # when the target is a foreign architecture, and anything attached over
        # USB may not survive that long -- a dropped disk mid-build suspends the
        # pool and loses the work. Doing it first means the disk is only needed
        # for the copy, and a retry after a disconnect reuses everything here.
        "nix build --no-link --print-out-paths <FLAKEPATH>#nixosConfigurations.<HOSTNAME>.config.system.build.toplevel",
        # Two overrides, both mkForce because the host config already defines them.
        #
        # The device, because the target disk is attached here, not where the host
        # will find it once installed.
        #
        # hostPlatform, because the partitioning runs on THIS machine. Built from
        # the host's own platform the script carries the host's binaries, and a
        # foreign-architecture `zpool` under binfmt aborts: it issues ioctls to the
        # running kernel's ZFS module across an ABI it does not match. The layout
        # itself is architecture-independent, so only the tools change.
        "sudo \"$(nix build --impure --no-link --print-out-paths --expr '((builtins.getFlake \"<FLAKEPATH>\").nixosConfigurations.\"<HOSTNAME>\".extendModules { modules = [ ({ lib, ... }: { nixpkgs.hostPlatform = lib.mkForce builtins.currentSystem; disko.devices.disk.root-drive.device = lib.mkForce \"<TARGET_DISK>\"; }) ]; }).config.system.build.diskoScript')\"",
        "sudo mkdir -p /mnt/persistent/etc/ssh",
        "sudo cp -a /tmp/nixtool-local-<HOSTNAME>/persistent/etc/ssh/. /mnt/persistent/etc/ssh/",
        "sudo chmod 600 /mnt/persistent/etc/ssh/ssh_host_ed25519_key /mnt/persistent/etc/ssh/ssh_initrd_host_ed25519_key",
        # --system rather than --flake: the closure was built above, so this is a
        # copy and a bootloader install with no evaluation or compilation left to
        # do while the disk has to stay attached.
        "sudo nixos-install --root /mnt --no-root-passwd --system \"$(nix build --no-link --print-out-paths <FLAKEPATH>#nixosConfigurations.<HOSTNAME>.config.system.build.toplevel)\"",
        "sudo umount -R /mnt || true",
        # By name, never `export -a`: this machine has its own pools imported and
        # exporting them would unmount the running system's storage.
        "sudo zpool export root-pool-<HOSTNAME> || true",
        "rm -rf /tmp/nixtool-local-<HOSTNAME> /tmp/encryption.key"
    ],
    "menu_variables": {
        "TARGET_DISK": {"title": "Target Disk (as attached to this machine)", "type": "disk"},
        "SSH_HOST_KEY": {"title": "Enter SSH Host Key", "type": "textarea"},
        "SSH_INITRD_KEY": {"title": "Enter SSH InitRD Host Key", "type": "textarea"},
        "ENCRYPTION_KEY": {"title": "Enter Disk Encryption Key", "type": "password"}
    },
    # Uses <HOSTNAME> to pick the configuration, but every command runs here.
    "run_on_remote": False
}


# Inspired by https://github.com/danboid/creating-ZFS-disks-under-Linux/blob/master/README.md
format_data_drive = {
    "id": "format-data-drive",
    "name": "Format Data Drive (ZFS on GPT)",
    "description": "Wipe a drive and create an encrypted ZFS data pool, optionally mirrored.",
    "destructive": True,
    "instructions": """
# Format Data Drive (ZFS on GPT)

This command will format the specified drive(s) to GPT and install a ZFS data pool for stateful data.

### RAID 1 (Mirroring)
If you provide a `MIRROR_DRIVE` path, the system will automatically configure a RAID-1 mirror. Leave the field blank for a single-drive setup.

### ⚠️ WARNING
Data on the selected disk(s) will be permanently erased. Double-check your device paths.
""",
    "commands": [
        "sudo sgdisk --zap-all <DATA_DRIVE>",
        "sudo sgdisk --new=1:0:0 --typecode=1:BF00 --change-name=1:zfs-data-partition <DATA_DRIVE>",
        "sudo partprobe <DATA_DRIVE> && sudo udevadm settle",
        "if [ <MIRROR_DRIVE> != none ]; then sudo sgdisk --zap-all <MIRROR_DRIVE> && sudo sgdisk --new=1:0:0 --typecode=1:BF00 --change-name=1:zfs-data-partition <MIRROR_DRIVE> && sudo partprobe <MIRROR_DRIVE> && sudo udevadm settle; fi",
        "sudo zpool create -f -d -o ashift=12 -o autotrim=on -o feature@zstd_compress=enabled -m none data-pool-<HOSTNAME> $(lsblk -rno NAME <DATA_DRIVE> | sed -n 2p | sed 's|^|/dev/|')",
        "sudo zpool upgrade data-pool-<HOSTNAME>",
        "printf '%s' <PASSPHRASE> | sudo zfs create -o encryption=on -o keyformat=passphrase -o keylocation=prompt -o compression=zstd -o xattr=sa -o acltype=posix -o relatime=on -o com.sun:auto-snapshot=true -o mountpoint=/Storage data-pool-<HOSTNAME>/storage",
        "if [ <MIRROR_DRIVE> != none ]; then sudo zpool attach data-pool-<HOSTNAME> $(lsblk -rno NAME <DATA_DRIVE> | sed -n 2p | sed 's|^|/dev/|') $(lsblk -rno NAME <MIRROR_DRIVE> | sed -n 2p | sed 's|^|/dev/|'); fi"
    ],
    "menu_variables": {
        "DATA_DRIVE": {"title": "Select Drive to Format", "type": "disk"},
        "MIRROR_DRIVE": {"title": "Select Secondary Mirror Drive", "type": "disk", "allow_none": True},
        "PASSPHRASE": {"title": "ZFS Pool Passphrase", "type": "password"}
    },
    "run_on_remote": True
}

# Inspired by https://github.com/danboid/creating-ZFS-disks-under-Linux/blob/master/README.md
flash_towboot = {
    "id": "flash-towboot",
    "name": "Flash Tow-Boot to SD Card",
    "description": "Wipe an SD card and write Tow-Boot to it, leaving the rest of the card unpartitioned.",
    "destructive": True,
    "instructions": """
# Flash Tow-Boot to SD Card

Writes Tow-Boot to the start of an SD card and expands the GPT to cover the rest
of it, leaving that space unpartitioned.

This is only the firmware half. The data pool is a separate command --
`install/format-sd-data` -- because the two have very different lifetimes: the
firmware is written once and then left alone, while the data partition gets
rebuilt whenever its passphrase needs to change. Doing both at once means
touching Tow-Boot every time the pool is recreated, and re-flashing firmware is
not something to do by accident.

Run `install/format-sd-data` against the same card afterwards.

### ⚠️ WARNING
The selected drive is erased in its entirety, partition table included.
Double-check the device path.
""",
    "commands": [
        # Wipe the partition table
        "sudo sgdisk --zap-all <DATA_DRIVE> && sudo partprobe <DATA_DRIVE> && sudo udevadm settle",
        # Wipe any residual TowBoot content
        "sudo dd if=/dev/zero of=<DATA_DRIVE> bs=32k seek=4 count=1 && sync",
        # Download and extract TowBoot into a temporary workdir, then flash it
        "WORKDIR=$(mktemp -d) && cd \"$WORKDIR\" && "
        "wget https://github.com/Tow-Boot/Tow-Boot/releases/download/release-<TOWBOOT_VERSION>/pine64-pinephoneA64-<TOWBOOT_VERSION>.tar.xz && "
        "tar -xvf pine64-pinephoneA64-<TOWBOOT_VERSION>.tar.xz && "
        "sudo dd if=pine64-pinephoneA64-<TOWBOOT_VERSION>/shared.disk-image.img of=<DATA_DRIVE> bs=1M oflag=direct,sync status=progress && "
        "rm -rf \"$WORKDIR\"",
        # Expand the GPT partition table to the rest of the SD Card
        "echo \"write\" | sudo sfdisk --append <DATA_DRIVE>"
    ],
    "menu_variables": {
        "DATA_DRIVE": {"title": "Select SD Card to Flash", "type": "disk"},
        "TOWBOOT_VERSION": {"title": "TowBoot Version", "type": "text"}
    },
    # Uses <HOSTNAME> to name the pool, but the card is attached to THIS
    # machine, so every command runs here -- the same as install-local. Running
    # these on the target would format a card the target does not have.
    "run_on_remote": False
}

format_sd_data = {
    "id": "format-sd-data",
    "name": "Format SD Card Data Partition (ZFS)",
    "description": "Create the encrypted ZFS data pool on partition 2 of a Tow-Boot SD card.",
    "destructive": True,
    "instructions": """
# Format SD Card Data Partition (ZFS)

Creates partition 2 of a Tow-Boot SD card and lays down `data-pool-<HOSTNAME>`
with an encrypted `storage` dataset, mounted at /Storage by
`technet.dataDrive`.

Partition 1 and the Tow-Boot image in it are never written by this command --
only `sgdisk --new=2` and the pool itself. Run `install/flash-towboot` first if
the card has no firmware yet.

### The passphrase has to match the host's zfs_passphrase

Clevis binds **one** secret per host and feeds it to every dataset in
`technet.clevis.datasets`. A data pool created with its own separate passphrase
cannot be unlocked at boot by any amount of configuration -- clevis will decrypt
the right secret from tang and ZFS will reject it.

So `PASSPHRASE` must be byte-identical to the `zfs_passphrase` in that host's
sops file. Declare it in nixtool's config as a `value_files` entry pointing at
the decrypted secret rather than typing it, which is both exact and keeps it off
the command line.

If a pool already exists with the wrong passphrase and you still know it,
`zfs change-key` is cheaper than reformatting:

    zfs load-key data-pool-<HOSTNAME>/storage
    zfs change-key -o keyformat=passphrase \\
        -o keylocation=file:///run/secrets/zfs_passphrase \\
        data-pool-<HOSTNAME>/storage
    zfs set keylocation=prompt data-pool-<HOSTNAME>/storage

### Pool naming and mountpoint

`data-pool-<HOSTNAME>`, with no UUID suffix. `technet.dataDrive.dataset`
defaults to `data-pool-${hostName}/storage` and will not find a pool named
anything else.

`mountpoint=/Storage`, not `legacy`. `technet.dataDrive` mounts with the
`zfsutil` option so ZFS supplies the mount options, and `zfsutil` runs
`zfs mount`, which refuses a legacy dataset outright:

    filesystem 'data-pool-<HOSTNAME>/storage' cannot be mounted using 'zfs mount'.
    Use 'zfs set mountpoint=/Storage' or 'mount -t zfs ...'

This command used to create the dataset as `legacy`, which made Storage.mount
fail on every boot. The pools that predate it are all `/Storage`.

### ⚠️ WARNING
Any existing partition 2 and the pool on it are destroyed. Partition 1 is not.
""",
    "commands": [
        # Partition 2 only. Partition 1 (Tow-Boot) is left exactly as it is.
        "sudo sgdisk --new=2:0:0 --typecode=2:BF00 --change-name=2:zfs-data-partition <DATA_DRIVE> && sudo partprobe <DATA_DRIVE> && sudo udevadm settle",
        "sudo zpool create -f -d -o ashift=12 -o autotrim=on -o feature@zstd_compress=enabled -m none data-pool-<HOSTNAME> $(lsblk -rno PATH <DATA_DRIVE> | sed -n 3p)",
        "sudo zpool upgrade data-pool-<HOSTNAME>",
        # keylocation=prompt because clevis supplies the passphrase at boot.
        "printf '%s' <PASSPHRASE> | sudo zfs create -o encryption=on -o keyformat=passphrase -o keylocation=prompt -o compression=zstd -o xattr=sa -o acltype=posix -o relatime=on -o com.sun:auto-snapshot=true -o mountpoint=/Storage data-pool-<HOSTNAME>/storage"
    ],
    "menu_variables": {
        "DATA_DRIVE": {"title": "Select SD Card", "type": "disk"},
        "PASSPHRASE": {"title": "ZFS Pool Passphrase (must equal the host's zfs_passphrase)", "type": "password"}
    },
    # Uses <HOSTNAME> to name the pool, but the card is attached to THIS
    # machine, so every command runs here -- the same as install-local. Running
    # these on the target would format a card the target does not have.
    "run_on_remote": False
}

nix_inspect = {
    "id": "inspect",
    "name": "Inspect Nix Config (nix-inspect)",
    "description": "Launch the nix-inspect TUI against the configured flake.",
    "command": "nix run github:bluskript/nix-inspect --",
    "interactive": True,
    "run_on_remote": False
}

maintenance_commands = {
    "id": "maintenance",
    "name": "Maintenance",
    "title": "Select a maintenance command",
    "category": True,
    "commands": [
        run_all,
        nix_flake_update,
        export_dconf,
        nix_rebuild,
        nix_rebuild_offline,
        nix_preview_generations,
        nix_purge_generations,
        nix_gc,
        nix_purge_generations_gc,
        nix_inspect,
    ]
}

install_commands = {
    "id": "install",
    "name": "Installation & Formatting",
    "title": "Select an installation or formatting command",
    "category": True,
    "commands": [
        nixos_install,
        nixos_install_local,
        format_data_drive,
        flash_towboot,
        format_sd_data,
    ]
}

all_commands = {
    "title": "Select a category",
    "commands": [
        maintenance_commands,
        install_commands,
    ]
}
