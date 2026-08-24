{
    description = "NixTool - CLI tool for managing flake based NixOS installations";

    inputs = {
        nixpkgs.url = "github:nixos/nixpkgs/nixos-unstable";
        flake-utils.url = "github:numtide/flake-utils";
    };

    outputs =
        {
            self,
            nixpkgs,
            flake-utils,
        }:
        flake-utils.lib.eachDefaultSystem (
            system:
            let
                pkgs = nixpkgs.legacyPackages.${system};
                python = pkgs.python3;

                # Binaries the commands shell out to, on PATH even for a package installed without the module.
                # nix-inspect lists no x86_64-darwin, where naming it fails evaluation rather than the command.
                runtimeTools =
                    [
                        pkgs.curl
                        pkgs.jq
                        pkgs.nh
                        pkgs.sshpass
                    ]
                    ++ pkgs.lib.optional pkgs.stdenv.hostPlatform.isLinux pkgs.nix-inspect;
            in
            {
                packages.default = python.pkgs.buildPythonApplication {
                    pname = "nixtool";
                    version = "0.1.0";
                    src = ./.;
                    format = "pyproject";

                    nativeBuildInputs = [ python.pkgs.setuptools ];
                    propagatedBuildInputs = [
                        python.pkgs.textual
                    ]
                    ++ runtimeTools;
                };

                apps.default = {
                    type = "app";
                    program = "${self.packages.${system}.default}/bin/nixtool";
                };

                devShells.default = pkgs.mkShell {
                    buildInputs = [
                        (python.withPackages (ps: [
                            ps.textual
                            ps.pytest
                            ps.pytest-asyncio
                        ]))
                    ]
                    ++ runtimeTools;

                    # To stderr: `nix develop --command nixtool list --json | jq`
                    # has to see the command's output on stdout and nothing else.
                    shellHook = ''
                        {
                            echo
                            echo "NixTool dev shell — python ${python.version}, textual, pytest. Nothing installed."
                            echo "  python -m nixtool           launch the interactive interface"
                            echo "  python -m nixtool --help    command line usage"
                            echo "  python -m nixtool list      list every runnable command"
                            echo "  pytest tests/ -q            run the test suite"
                            echo
                        } >&2
                    '';
                };
            }
        )
        // {
            # System-independent, so it lives outside eachDefaultSystem. The module
            # takes `self` so its default package tracks this flake's own build.
            nixosModules = rec {
                nixtool = import ./module.nix self;
                default = nixtool;
            };
        };
}
