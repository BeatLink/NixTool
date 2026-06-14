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

nixos_install_test = {
    "name": "Test Installation (Isolated VM)",
    "instructions": """
# Installation VM Test
This will spin up a **QEMU Virtual Machine** and attempt to run the partitioning and installation logic using your flake configuration.

*   It does **not** touch your physical disks.
*   It requires `disko` to be defined in your NixOS configuration.
""",
    "commands": [
        "nix run github:nix-community/disko -- --mode disko --vm-test --flake <FLAKEPATH>#<HOSTNAME>"
    ],
    "run_on_remote": False
}

format_data_drive = {
    "name": "Format Data Drive (Isolated VM)",
    "instructions": """
# Format Data Drive (ZFS on GPT)

This command will format the specified drive to GPT and install a ZFS data pool for stateful data.

To protect your system, this command launches **NixOS VM** and passes through your physical disk. 
The formatting commands are then run inside the VM.

### ⚠️ WARNING
Ensure you specify the correct `DATA_DRIVE` path. Data on that disk will be permanently erased.
""",
    "commands": [
        "bash -c '(sleep 20; printf \"sgdisk --zap-all /dev/vda\\npartprobe /dev/vda\\nsgdisk --new=1:0:0 --typecode=1:BF00 /dev/vda\\npartprobe /dev/vda\\nzpool create -f -d -m none -o feature@zstd_compress=enabled -o ashift=12 -o autotrim=on data-pool /dev/vda1\\necho \\'<PASSPHRASE>\\' | zfs create -o encryption=on -o keyformat=passphrase -o keylocation=prompt -o xattr=sa -o acltype=posix -o relatime=on -o com.sun:auto-snapshot=true -o mountpoint=/Storage data-pool/storage\\npoweroff\\n\") | nix run nixpkgs#qemu_kvm -- -enable-kvm -m 2G -smp 2 -nographic -kernel $(nix-build <nixpkgs/nixos> -A config.system.build.kernel --no-out-link)/bzImage -initrd $(nix-build <nixpkgs/nixos> -A config.system.build.initialRamdisk --no-out-link)/initrd -append \"console=ttyS0 boot.shell_on_fail\" -drive file=<DATA_DRIVE>,format=raw,if=virtio'"
    ],
    "menu_variables": {
        "DATA_DRIVE": {"title": "Select Drive to Format", "type": "disk"},
        "PASSPHRASE": {"title": "ZFS Pool Passphrase", "type": "password"}
    },
    "run_on_remote": True
}

simulate_zfs_format = {
    "name": "Simulate ZFS Format (Sandbox)",
    "instructions": """
# ZFS Sandbox Simulation
This runs the formatting logic inside a **temporary 2GB file** located in `/tmp`.

*   **Safe**: It does not touch physical disks.
*   **Educational**: You can see how ZFS handles encryption and datasets.
""",
    "commands": [
        "truncate -s 2G /tmp/nix-sandbox.img",
        "sudo zpool create -f -m none sandbox-pool /tmp/nix-sandbox.img",
        "echo '<PASSPHRASE>' | sudo zfs create -o encryption=on -o keyformat=passphrase -o keylocation=prompt -o mountpoint=/tmp/sandbox-mnt sandbox-pool/storage",
        "zpool status sandbox-pool",
        "sudo zpool destroy sandbox-pool",
        "rm /tmp/nix-sandbox.img"
    ],
    "menu_variables": {
        "PASSPHRASE": {"title": "Test Passphrase", "type": "password"}
    },
    "run_on_remote": False
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
        nixos_install_test,
        format_data_drive,
        simulate_zfs_format,
    ]
}

HOST_TITLE = "Select Hosts"
