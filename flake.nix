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
                        pkgs.nh
                    ];
                };

                apps.default = {
                    type = "app";
                    program = "${self.packages.${system}.default}/bin/nixtool";
                };

                devShells.default = pkgs.mkShell {
                    buildInputs = [
                        (python.withPackages (ps: [ ps.textual ]))
                        pkgs.nh
                    ];
                };
            }
        );
}
