import argparse
import pathlib
from . import run

def main():
    parser = argparse.ArgumentParser(description="NixTool - CLI tool for managing NixOS")
    parser.add_argument("-c", "--config", type=pathlib.Path, help="Path to the nixtool-config.json file")
    args = parser.parse_args()
    # This allows the package to be run via 'python -m nixtool' or the 'nixtool' command
    run(config_path=args.config)

if __name__ == "__main__":
    main()