"""Standalone Windows downloader for the Desktop Goose component of EveTech."""

import argparse
import sqlite3

from evetech.goose_install import GOOSE_PAGE, GooseInstallError, install_goose
from evetech.storage import Store, data_directory


def main():
    parser = argparse.ArgumentParser(description="Set up the official Desktop Goose v0.31 for EveTech.")
    parser.add_argument("archive", nargs="?", help="Import an official Windows ZIP instead of downloading it.")
    parser.add_argument("--yes", action="store_true", help="Install without interactive prompts.")
    args = parser.parse_args()

    print("EveTech Desktop Goose Setup")
    print("Desktop Goose by Samperson:", GOOSE_PAGE)
    if not args.yes:
        try:
            action = "Import the official ZIP" if args.archive else "Download the official Windows ZIP from itch.io"
            if input(action + " and enable Desktop Goose in EveTech? [y/N] ").strip().lower() not in ("y", "yes"):
                return 0
        except (EOFError, KeyboardInterrupt):
            return 1

    result = 0
    try:
        print("Checking the official archive and setting up Desktop Goose…", flush=True)
        folder = install_goose(args.archive)
        Store(data_directory()).update_settings({"desktop_goose_folder": str(folder), "desktop_goose_enabled": "1"})
        print("Ready. Open or restart EveTech, then go to Settings > Desktop Pet > Preview Goose.")
    except (GooseInstallError, OSError, sqlite3.Error) as error:
        message = str(error) if isinstance(error, GooseInstallError) else "Could not write EveTech settings."
        print(message)
        print("To import the ZIP in EveTech, open Settings > Desktop Pet > Import Goose ZIP.")
        result = 1
    if not args.yes:
        try:
            input("Press Enter to close.")
        except (EOFError, KeyboardInterrupt):
            pass
    return result


if __name__ == "__main__":
    raise SystemExit(main())
