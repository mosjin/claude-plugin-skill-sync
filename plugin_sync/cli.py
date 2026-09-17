"""Argparse wiring and command dispatch — the single entry point `plugin_manager.py` calls."""
import argparse

from . import claude_cli, snapshots, gist
from . import merge as merge_mod
from . import apply as apply_mod


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="plugin_manager",
        description="Claude plugin manager — list, update, uninstall, and snapshot-sync plugins/skills",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="List installed plugins")

    up = sub.add_parser("update", help="Update plugin(s)")
    up.add_argument("plugins", nargs="*", metavar="plugin", help="Plugin name(s) to update")
    up.add_argument("--all", action="store_true", help="Update all installed plugins")
    up.add_argument("--parallel", action="store_true", help="Run updates concurrently (useful with --all)")

    un = sub.add_parser("uninstall", aliases=["remove"], help="Uninstall plugin(s)")
    un.add_argument("plugins", nargs="*", metavar="plugin", help="Plugin name(s) to uninstall")
    un.add_argument("-y", "--yes", action="store_true", help="Skip confirmation prompt")
    un.add_argument("--keep-data", action="store_true", help="Preserve plugin data directory")
    un.add_argument("--prune", action="store_true", help="Remove unused auto-installed dependencies")

    sub.add_parser(
        "doctor",
        help="List standalone MCP servers this tool cannot update (not bundled in any plugin)",
    )

    save = sub.add_parser("save", help="Save a desensitized snapshot of installed plugins/skills")
    save.add_argument("--identity", help=f"Account label (default: ${snapshots.ENV_IDENTITY})")
    save.add_argument("--machine", help=f"Machine label (default: ${snapshots.ENV_MACHINE})")
    save.add_argument(
        "--dir",
        help=f"Snapshot output dir (default: ${snapshots.ENV_SNAPSHOT_DIR} or ./{snapshots.DEFAULT_SNAPSHOT_DIR}, local-only)",
    )

    merge = sub.add_parser("merge", help="Merge snapshots from a directory into one drift view")
    merge.add_argument(
        "--dir",
        help=f"Snapshot input dir (default: ${snapshots.ENV_SNAPSHOT_DIR} or ./{snapshots.DEFAULT_SNAPSHOT_DIR})",
    )
    merge.add_argument("--out", help="Optional path to also write the merged view as JSON")

    upload = sub.add_parser("upload", help="Upload a snapshot file to a GitHub Gist")
    upload.add_argument("file", help="Path to the snapshot JSON file to upload (printed by `save`)")
    upload.add_argument(
        "--gist-id",
        help=f"Existing gist id to add to (default: ${gist.ENV_GIST_ID} or the target dir's cached id; creates a new secret gist if none found)",
    )
    upload.add_argument(
        "--dir",
        help=f"Snapshot dir this gist is paired with, for caching the id (default: ${snapshots.ENV_SNAPSHOT_DIR} or ./{snapshots.DEFAULT_SNAPSHOT_DIR})",
    )
    upload.add_argument(
        "--keep-history",
        action="store_true",
        help="Don't remove this machine's older snapshots from the gist — keep every upload instead of overwriting",
    )

    fetch = sub.add_parser("fetch", help="Fetch snapshots from a GitHub Gist into the local snapshot dir")
    fetch.add_argument("--gist-id", help=f"Gist id to fetch from (default: ${gist.ENV_GIST_ID})")
    fetch.add_argument(
        "--dir",
        help=f"Local snapshot dir to copy into (default: ${snapshots.ENV_SNAPSHOT_DIR} or ./{snapshots.DEFAULT_SNAPSHOT_DIR})",
    )

    gist_list = sub.add_parser(
        "gist-list",
        help=f"List this tool's own gists (tagged '{gist.SNAPSHOT_GIST_DESCRIPTION}') — find a --gist-id without leaving the terminal",
    )
    gist_visibility = gist_list.add_mutually_exclusive_group()
    gist_visibility.add_argument("--public", action="store_true", help="Only show public gists")
    gist_visibility.add_argument("--secret", action="store_true", help="Only show secret gists")

    apply_ = sub.add_parser("apply", help="Install plugins missing on this machine from a merged snapshot")
    apply_.add_argument("merged_file", help="Path to a `merge --out` JSON file")
    apply_.add_argument("plugins", nargs="*", metavar="plugin", help="Specific plugin id(s) to install")
    apply_.add_argument("--all", action="store_true", help="Install everything missing")
    apply_.add_argument("-y", "--yes", action="store_true", help="Actually install (default is dry-run preview)")
    apply_.add_argument("--scope", choices=["user", "project", "local"], help="Install scope (required with -y)")
    apply_.add_argument("--lang", choices=["en", "zh"], help="Language for this tool's own prompts (default: en)")

    args = parser.parse_args()
    if args.command == "list":
        claude_cli.cmd_list(args)
    elif args.command == "update":
        claude_cli.cmd_update(args)
    elif args.command in ("uninstall", "remove"):
        claude_cli.cmd_uninstall(args)
    elif args.command == "doctor":
        claude_cli.cmd_doctor(args)
    elif args.command == "save":
        snapshots.cmd_save(args)
    elif args.command == "merge":
        merge_mod.cmd_merge(args)
    elif args.command == "upload":
        gist.cmd_upload(args)
    elif args.command == "fetch":
        gist.cmd_fetch(args)
    elif args.command == "gist-list":
        gist.cmd_gist_list(args)
    elif args.command == "apply":
        apply_mod.cmd_apply(args)


