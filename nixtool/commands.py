import pathlib
import json

nix_flake_update = {
    "name": "Run Nix Flake Update",
    "commands": [
        "nix flake update --refresh"
    ],
    "run_on_remote": False
}

def get_dconf_commands(flake_path):
    queue = []
    flake_root = pathlib.Path(flake_path)
    for config_path in flake_root.rglob("dconf-settings.json"):
        try:
            data = json.loads(config_path.read_text())
            for dconf_path in data.get("dconf_exports", []):
                output_name = f"{dconf_path.strip('/').replace('/', '.')}.dconf"
                target_file = (config_path.parent / output_name).relative_to(flake_root)
                queue.append(f"dconf dump {dconf_path} > ./{target_file}")
        except Exception as e:
            queue.append(f"echo 'Error processing {config_path.name}: {str(e)}'")
    return queue if queue else ["echo 'No localized dconf targets found.'"]

export_dconf = {
    "name": "Export Dconf Settings",
    "commands": [
        get_dconf_commands
    ],
    "run_on_remote": False
}

nix_rebuild = {
    "name": "Run Nixos Rebuild",
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
    "name": "Preview Old Generations",
    "commands": [
        'echo "---- <HOSTNAME> (system generations) ----" && sudo nix-env --profile /nix/var/nix/profiles/system --list-generations && echo "---- <HOSTNAME> (user generations) ----" && nix-env --list-generations'
    ],
    "run_on_remote": True
}

nix_purge_generations = {
    "name": "Remove Old Generations",
    "commands": [
        "sudo nix-env --profile /nix/var/nix/profiles/system --delete-generations old",
        "nix-env --delete-generations old"
    ],
    "run_on_remote": True
}

nix_gc = {
    "name": "Run Garbage Collection",
    "commands": [
        "sudo nix-collect-garbage -d"
    ],
    "run_on_remote": True
}

nix_purge_generations_gc = {
    "name": "Remove Old Generations & GC",
    "commands": [
        nix_preview_generations,
        nix_purge_generations,
        nix_gc
    ]
}

run_all = {
    "name": "Run All Tasks",
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
    "name": "Install NixOS (Anywhere)",
    "commands": [
        "mkdir -p /tmp/nixtool-install-<HOSTNAME>/install/persistent/etc/ssh",
        "echo '<SSH_HOST_KEY>' > /tmp/nixtool-install-<HOSTNAME>/install/persistent/etc/ssh/ssh_host_ed25519_key",
        "echo '<SSH_INITRD_KEY>' > /tmp/nixtool-install-<HOSTNAME>/install/persistent/etc/ssh/ssh_initrd_host_ed25519_key",
        "echo '<ENCRYPTION_KEY>' > /tmp/nixtool-install-<HOSTNAME>/encryption.key",
        "chmod 600 /tmp/nixtool-install-<HOSTNAME>/install/persistent/etc/ssh/*",
        "nix run github:nix-community/nixos-anywhere -- --extra-files '/tmp/nixtool-install-<HOSTNAME>/install' --disk-encryption-keys /tmp/encryption.key '/tmp/nixtool-install-<HOSTNAME>/encryption.key' --phases kexec,disko,install --no-substitute-on-destination --flake <FLAKEPATH>#<HOSTNAME> <SSH_ADDRESS>",
        "rm -rf /tmp/nixtool-install-<HOSTNAME>"
    ],
    "menu_variables": {
        "SSH_ADDRESS": {"title": "Enter SSH Address (root@ip)", "type": "text"},
        "SSH_HOST_KEY": {"title": "Enter SSH Host Key", "type": "text"},
        "SSH_INITRD_KEY": {"title": "Enter SSH InitRD Host Key", "type": "text"},
        "ENCRYPTION_KEY": {"title": "Enter Disk Encryption Key", "type": "password"}
    },
    # This command uses <HOSTNAME> and <SSH_ADDRESS>, and runs locally 
    # to orchestrate the remote install.
    "run_on_remote": False
}

format_data_drive = {
    "name": "Format Data Drive (ZFS Storage)",
    "instructions": """
# Data Drive Format Instructions
This command prepares a data drive for backup storage using **ZFS** on a **GPT** partition table.

### ⚠️ DANGER
*   The selected drive will be **erased and formatted in its entirety**.
*   Ensure there is no important information on the drive before proceeding.
*   This script MUST be run on the device in question.
""",
    "commands": [
        "sudo sgdisk --zap-all <DATA_DRIVE>",
        "sudo partprobe <DATA_DRIVE>",
        "sudo sgdisk --new=1:0:0 --typecode=1:BF00 <DATA_DRIVE>",
        "sudo partprobe <DATA_DRIVE>",
        "sudo zpool destroy data-pool || true",
        "sudo zpool create -f -d -m none -o feature@zstd_compress=enabled -o ashift=12 -o autotrim=on data-pool <DATA_DRIVE>1",
        "echo '<PASSPHRASE>' | sudo zfs create -o encryption=on -o keyformat=passphrase -o keylocation=prompt -o xattr=sa -o acltype=posix -o relatime=on -o com.sun:auto-snapshot=true -o mountpoint=/Storage data-pool/storage"
    ],
    "menu_variables": {
        "DATA_DRIVE": {"title": "Data Drive to Format (e.g. /dev/sdb)", "type": "text"},
        "PASSPHRASE": {"title": "ZFS Pool Passphrase", "type": "password"}
    },
    "run_on_remote": True
}

all_commands = {
    "title": "Select a command",
    "commands": [
        run_all,
        nix_flake_update,
        export_dconf,
        nix_rebuild,
        nix_preview_generations,
        nix_purge_generations,
        nix_purge_generations_gc,
        nixos_install,
        format_data_drive,
    ]
}

HOST_TITLE = "Select Hosts"
