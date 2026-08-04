# NixOS module for nixtool.
#
# Renders nixtool-config.json into /etc/nixtool, which is the last entry in
# nixtool's own search path, so a plain `nixtool` finds it with no --config.
#
# Credentials are named by *path*, never by value. That keeps this module
# independent of how those paths come to exist: sops-nix, agenix, a systemd
# credential or a file placed by hand all work, and none of them becomes a
# dependency of nixtool itself.

self:
{
    config,
    lib,
    pkgs,
    ...
}:
let
    cfg = config.programs.nixtool;

    variableFiles = lib.types.attrsOf lib.types.str;

    configFile =
        { }
        // lib.optionalAttrs (cfg.flakePath != null) { flake_path = cfg.flakePath; }
        // lib.optionalAttrs (cfg.user != null) { user = cfg.user; }
        // lib.optionalAttrs (cfg.hosts != { }) { hosts = cfg.hosts; }
        // lib.optionalAttrs (cfg.values != { }) { values = cfg.values; }
        // lib.optionalAttrs (cfg.valueFiles != { }) { value_files = cfg.valueFiles; }
        // lib.optionalAttrs (cfg.hostValues != { }) { host_values = cfg.hostValues; }
        // lib.optionalAttrs (cfg.hostValueFiles != { }) { host_value_files = cfg.hostValueFiles; };
in
{
    options.programs.nixtool = {
        enable = lib.mkEnableOption "nixtool, and a system-wide nixtool-config.json";

        package = lib.mkOption {
            type = lib.types.package;
            default = self.packages.${pkgs.stdenv.hostPlatform.system}.default;
            defaultText = lib.literalExpression "nixtool.packages.\${system}.default";
            description = "The nixtool package to install.";
        };

        flakePath = lib.mkOption {
            type = lib.types.nullOr lib.types.str;
            default = null;
            description = "Path to the flake nixtool operates on.";
        };

        user = lib.mkOption {
            type = lib.types.nullOr lib.types.str;
            default = null;
            description = "User nixtool connects to hosts as.";
        };

        hosts = lib.mkOption {
            type = lib.types.attrsOf lib.types.str;
            default = { };
            example = lib.literalExpression ''{ alpha = "alpha.example.com"; }'';
            description = "Hostname to SSH address mapping.";
        };

        values = lib.mkOption {
            type = variableFiles;
            default = { };
            description = ''
                Non-secret variables given literally, applied to every host.

                nixtool refuses a secret supplied this way, since this value is
                written to the world-readable config file in the Nix store.
                Anything sensitive belongs in {option}`valueFiles`.
            '';
        };

        valueFiles = lib.mkOption {
            type = variableFiles;
            default = { };
            example = lib.literalExpression ''{ SSH_PASSWORD = "/run/secrets/ssh_password"; }'';
            description = ''
                Variable name to the path of a file holding its value, applied to
                every host. Only the path is written to the config; nixtool reads
                the file at run time, so the credential never enters the Nix store.
            '';
        };

        hostValues = lib.mkOption {
            type = lib.types.attrsOf variableFiles;
            default = { };
            example = lib.literalExpression ''{ alpha = { SSH_TARGET = "root@10.0.0.1"; }; }'';
            description = "Per-host {option}`values`, overriding the shared ones.";
        };

        hostValueFiles = lib.mkOption {
            type = lib.types.attrsOf variableFiles;
            default = { };
            example = lib.literalExpression ''
                { alpha = { ENCRYPTION_KEY = "/run/secrets/alpha_encryption_key"; }; }
            '';
            description = "Per-host {option}`valueFiles`, overriding the shared ones.";
        };
    };

    config = lib.mkIf cfg.enable {
        assertions = [
            {
                assertion = lib.all (host: cfg.hosts ? ${host}) (
                    lib.attrNames cfg.hostValues ++ lib.attrNames cfg.hostValueFiles
                );
                message =
                    "programs.nixtool: hostValues/hostValueFiles name hosts that are not in "
                    + "programs.nixtool.hosts, so they would never be used. Known hosts: "
                    + (lib.concatStringsSep ", " (lib.attrNames cfg.hosts));
            }
        ];

        # nixtool's commands are shell strings, so their dependencies are not
        # visible to Nix and cannot be closure-tracked -- they simply have to be
        # on PATH. Installing them alongside the tool is what makes a host that
        # imports this module able to run every command in the tree, rather than
        # discovering a gap the first time it formats something.
        #
        # sgdisk and partprobe were exactly that gap: install-local,
        # format-data-drive, flash-towboot and format-sd-data all call them, and
        # a host with neither failed at the first step with
        # `sudo: sgdisk: command not found`.
        #
        # The rest are listed because they are used, not because they were
        # missing -- most arrive with any NixOS system, but naming them here
        # means a minimal host does not have to work out why a command failed
        # halfway through.
        environment.systemPackages = [
            cfg.package
            pkgs.gptfdisk
            pkgs.parted
            pkgs.util-linux
            pkgs.wget
            pkgs.gnutar
            pkgs.xz
        ];
        environment.etc."nixtool/nixtool-config.json".text = builtins.toJSON configFile;
    };
}
