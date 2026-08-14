{
    pkgs ? import <nixpkgs> { },
}:

pkgs.mkShell {
    name = "technet-installer-venv";

    buildInputs = with pkgs; [
        # System dependencies required for formatting and installation
        gptfdisk
        zfs
        parted
        util-linux
        wget
        sshpass
        nix-inspect

        # Python environment and venv automation
        python3
        python3Packages.textual
        python3Packages.pytest
        python3Packages.pytest-asyncio
        python3Packages.venvShellHook
    ];

    # Path to the virtual environment directory
    venvDir = "./.venv";

    # Command to run after the venv is created and activated. The banner goes to
    # stderr so `nix-shell --run "nixtool list --json" | jq` stays parseable.
    postShellHook = ''
        pip install -e .
        {
            echo
            echo "NixTool venv ready at $venvDir — nixtool installed in editable mode."
            echo "  nixtool                     launch the interactive interface"
            echo "  nixtool --help              command line usage"
            echo "  nixtool list                list every runnable command"
            echo "  pytest tests/ -q            run the test suite"
            echo
        } >&2
    '';
}
