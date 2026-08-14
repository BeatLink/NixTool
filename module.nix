# NixOS module for nixtool.
#
# Renders nixtool-config.json into /etc/nixtool, which is the last entry in
# nixtool's own search path, so a plain `nixtool` finds it with no --config.
#
# Credentials are named by *path*, never by value, so agenix, systemd
# credentials or a file placed by hand all work. `sops` is a convenience on top
# of that, resolving secret names into paths; using it requires sops-nix.

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

  sopsPaths = lib.mapAttrs (_: secret: config.sops.secrets.${secret}.path);

  sopsValueFiles = lib.optionalAttrs cfg.sops.enable (sopsPaths cfg.sops.valueSecrets);
  sopsHostValueFiles = lib.optionalAttrs cfg.sops.enable (
    lib.mapAttrs (_: sopsPaths) cfg.sops.hostValueSecrets
  );

  valueFiles = sopsValueFiles // cfg.valueFiles;

  hostValueFiles = lib.zipAttrsWith (_: lib.foldl' (a: b: a // b) { }) [
    sopsHostValueFiles
    cfg.hostValueFiles
  ];

  configFile =
    { }
    // lib.optionalAttrs (cfg.flakePath != null) { flake_path = cfg.flakePath; }
    // lib.optionalAttrs (cfg.user != null) { user = cfg.user; }
    // lib.optionalAttrs (cfg.hosts != { }) { hosts = cfg.hosts; }
    // lib.optionalAttrs (cfg.values != { }) { values = cfg.values; }
    // lib.optionalAttrs (valueFiles != { }) { value_files = valueFiles; }
    // lib.optionalAttrs (cfg.hostValues != { }) { host_values = cfg.hostValues; }
    // lib.optionalAttrs (hostValueFiles != { }) { host_value_files = hostValueFiles; };
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

    sops = {
      enable = lib.mkEnableOption "sourcing credential paths from sops-nix secrets";

      sopsFile = lib.mkOption {
        type = lib.types.nullOr lib.types.path;
        default = null;
        description = "sops file holding the secrets named below. Null uses sops-nix's default.";
      };

      owner = lib.mkOption {
        type = lib.types.nullOr lib.types.str;
        default = null;
        # nixos-anywhere runs unprivileged, so a root-owned secret is unreadable to it
        description = "Owner of the decrypted credential files.";
      };

      valueSecrets = lib.mkOption {
        type = variableFiles;
        default = { };
        example = lib.literalExpression ''{ SSH_PASSWORD = "ssh_password"; }'';
        description = ''
          Variable name to the *name* of the sops secret holding its value,
          applied to every host. Merged into {option}`valueFiles`, which
          takes precedence on a conflict.
        '';
      };

      hostValueSecrets = lib.mkOption {
        type = lib.types.attrsOf variableFiles;
        default = { };
        example = lib.literalExpression ''{ alpha = { ENCRYPTION_KEY = "alpha_encryption_key"; }; }'';
        description = ''
          Per-host {option}`sops.valueSecrets`. Merged into
          {option}`hostValueFiles`, which takes precedence on a conflict.
        '';
      };
    };
  };

  config = lib.mkMerge [
    (lib.mkIf cfg.enable {
      assertions = [
        {
          assertion = lib.all (host: cfg.hosts ? ${host}) (
            lib.attrNames cfg.hostValues ++ lib.attrNames hostValueFiles
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
        pkgs.nix-inspect
      ];
      environment.etc."nixtool/nixtool-config.json".text = builtins.toJSON configFile;
    })

    # Defining sops.secrets at all requires sops-nix imported, even when disabled
    (lib.mkIf (cfg.enable && cfg.sops.enable) {
      sops.secrets =
        lib.genAttrs
          (lib.unique (
            lib.attrValues cfg.sops.valueSecrets
            ++ lib.concatMap lib.attrValues (lib.attrValues cfg.sops.hostValueSecrets)
          ))
          (
            _:
            {
              mode = "0400";
            }
            // lib.optionalAttrs (cfg.sops.sopsFile != null) { sopsFile = cfg.sops.sopsFile; }
            // lib.optionalAttrs (cfg.sops.owner != null) { owner = cfg.sops.owner; }
          );
    })
  ];
}
