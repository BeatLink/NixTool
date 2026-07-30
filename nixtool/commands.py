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
        "printf '%s' <SSH_HOST_KEY> > /tmp/nixtool-install-<HOSTNAME>/install/persistent/etc/ssh/ssh_host_ed25519_key",
        "printf '%s' <SSH_INITRD_KEY> > /tmp/nixtool-install-<HOSTNAME>/install/persistent/etc/ssh/ssh_initrd_host_ed25519_key",
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
        "sudo zpool create -f -d -o ashift=12 -o autotrim=on -o feature@zstd_compress=enabled -m none data-pool-<HOSTNAME>-<POOL_UUID> $(lsblk -rno NAME <DATA_DRIVE> | sed -n 2p | sed 's|^|/dev/|')",
        "sudo zpool upgrade data-pool-<HOSTNAME>-<POOL_UUID>",
        "printf '%s' <PASSPHRASE> | sudo zfs create -o encryption=on -o keyformat=passphrase -o keylocation=prompt -o compression=zstd -o xattr=sa -o acltype=posix -o relatime=on -o com.sun:auto-snapshot=true -o mountpoint=legacy data-pool-<HOSTNAME>-<POOL_UUID>/storage",
        "if [ <MIRROR_DRIVE> != none ]; then sudo zpool attach data-pool-<HOSTNAME>-<POOL_UUID> $(lsblk -rno NAME <DATA_DRIVE> | sed -n 2p | sed 's|^|/dev/|') $(lsblk -rno NAME <MIRROR_DRIVE> | sed -n 2p | sed 's|^|/dev/|'); fi"
    ],
    "menu_variables": {
        "DATA_DRIVE": {"title": "Select Drive to Format", "type": "disk"},
        "MIRROR_DRIVE": {"title": "Select Secondary Mirror Drive", "type": "disk", "allow_none": True},
        "PASSPHRASE": {"title": "ZFS Pool Passphrase", "type": "password"},
        "POOL_UUID": {"type": "uuid"}
    },
    "run_on_remote": True
}

# Inspired by https://github.com/danboid/creating-ZFS-disks-under-Linux/blob/master/README.md
format_sd_card_phone = {
    "id": "format-sd-card",
    "name": "Format SD Card for Phone (TowBoot + ZFS)",
    "description": "Wipe an SD card, flash TowBoot, and create an encrypted ZFS pool.",
    "destructive": True,
    "instructions": """
# Format SD Card for Phone (TowBoot + ZFS)

This command formats and prepares an SD Card to store TowBoot as well as the
backup files and other stateful information for a PinePhone.

The drive will consist of a GPT partition table containing the TowBoot image
(partition 1) and an encrypted ZFS data storage pool (partition 2).

### ⚠️ WARNING
The selected drive will be erased and formatted in its entirety. Double-check
your device path to ensure there is no important information on the drive.
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
        "echo \"write\" | sudo sfdisk --append <DATA_DRIVE>",
        # Create the ZFS partition (partition 2)
        "sudo sgdisk --new=2:0:0 --typecode=2:BF00 --change-name=2:zfs-data-partition <DATA_DRIVE> && sudo partprobe <DATA_DRIVE> && sudo udevadm settle",
        # Create the ZFS pool on partition 2 (the second child partition)
        "sudo zpool create -f -d -o ashift=12 -o autotrim=on -o feature@zstd_compress=enabled -m none data-pool-<HOSTNAME>-<POOL_UUID> $(lsblk -rno NAME <DATA_DRIVE> | sed -n 3p | sed 's|^|/dev/|')",
        "sudo zpool upgrade data-pool-<HOSTNAME>-<POOL_UUID>",
        # Create the encrypted storage dataset
        "printf '%s' <PASSPHRASE> | sudo zfs create -o encryption=on -o keyformat=passphrase -o keylocation=prompt -o compression=zstd -o xattr=sa -o acltype=posix -o relatime=on -o com.sun:auto-snapshot=true -o mountpoint=legacy data-pool-<HOSTNAME>-<POOL_UUID>/storage"
    ],
    "menu_variables": {
        "DATA_DRIVE": {"title": "Select SD Card to Format", "type": "disk"},
        "TOWBOOT_VERSION": {"title": "TowBoot Version", "type": "text"},
        "PASSPHRASE": {"title": "ZFS Pool Passphrase", "type": "password"},
        "POOL_UUID": {"type": "uuid"}
    },
    "run_on_remote": True
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
        format_data_drive,
        format_sd_card_phone,
    ]
}

all_commands = {
    "title": "Select a category",
    "commands": [
        maintenance_commands,
        install_commands,
    ]
}
