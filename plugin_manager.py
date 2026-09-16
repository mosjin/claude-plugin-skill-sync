"""Claude plugin manager — list and batch-update plugins.

Usage:
    python plugin_manager.py list
    python plugin_manager.py update caveman
    python plugin_manager.py update caveman ecc eduforge
    python plugin_manager.py update --all
    python plugin_manager.py update --all --parallel
"""

import argparse
import json
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed


def run_claude(args: list) -> tuple:
    """Thin subprocess seam — mockable in tests.

    Returns (returncode, stdout, stderr).
    Cross-platform: shutil.which locates claude/claude.cmd/claude.exe.
    """
    exe = shutil.which("claude")
    if not exe:
        sys.exit("Error: 'claude' not found in PATH. Is Claude Code installed?")
    result = subprocess.run(
        [exe] + args,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.returncode, result.stdout, result.stderr


def list_plugins() -> list:
    """Return parsed plugin list from `claude plugin list --json`."""
    code, out, err = run_claude(["plugin", "list", "--json"])
    if code != 0:
        sys.exit(f"Error listing plugins: {err.strip()}")
    plugins = json.loads(out)
    if not isinstance(plugins, list):
        sys.exit(f"Error: unexpected response from claude (expected list, got {type(plugins).__name__})")
    return plugins


def resolve_plugins(names: list, all_plugins: list) -> list:
    """Match plugin names (partial or full id) to plugin records.

    Accepts both full id ("caveman@caveman") and short name ("caveman").
    """
    if not names:
        return []
    index = {p["id"]: p for p in all_plugins}
    resolved = []
    for name in names:
        if name in index:
            resolved.append(index[name])
            continue
        matches = [p for p in all_plugins if p["id"].split("@")[0] == name]
        if len(matches) == 1:
            resolved.append(matches[0])
        elif len(matches) > 1:
            options = ", ".join(p["id"] for p in matches)
            sys.exit(f"Ambiguous plugin '{name}'. Options: {options}")
        else:
            sys.exit(f"Plugin '{name}' not found. Run 'list' to see installed plugins.")
    return resolved


def update_one(plugin: dict) -> dict:
    """Run `claude plugin update <id>`. Returns the raw outcome only —
    status (updated/current/failed) is resolved by the caller from a
    before/after version diff, not by guessing at stdout wording.
    """
    pid = plugin["id"]
    scope = plugin.get("scope", "user")
    code, out, err = run_claude(["plugin", "update", pid, "-s", scope])
    return {"id": pid, "code": code, "message": (out + err).strip()}


def resolve_update_status(before_version, after_version, code: int) -> str:
    """Classify one update's outcome from real evidence: exit code plus
    whether the plugin's version string actually changed. A zero exit code
    with unchanged stdout wording used to be misread as success by regex —
    this compares state instead of parsing prose.
    """
    if code != 0:
        return "failed"
    if after_version is None:
        return "failed"
    return "updated" if after_version != before_version else "current"


def _split_id(plugin_id: str) -> tuple:
    parts = plugin_id.split("@", 1)
    if len(parts) != 2:
        sys.exit(f"Error: malformed plugin id '{plugin_id}' (expected 'name@source')")
    return parts[0], parts[1]


def _print_table(plugins: list) -> None:
    name_w = max(len(_split_id(p["id"])[0]) for p in plugins) + 2
    src_w = max(len(_split_id(p["id"])[1]) for p in plugins) + 2
    ver_w = max(len(p.get("version", "")) for p in plugins) + 2

    header = f"{'Plugin':<{name_w}} {'Source':<{src_w}} {'Version':<{ver_w}} {'Scope':<8} Status"
    print(header)
    print("─" * len(header))
    for p in plugins:
        name, source = _split_id(p["id"])
        status = "✔" if p.get("enabled", True) else "✗"
        print(f"{name:<{name_w}} {source:<{src_w}} {p.get('version', '?'):<{ver_w}} {p.get('scope', '?'):<8} {status}")
    print(f"\n{len(plugins)} plugin{'s' if len(plugins) != 1 else ''} installed")


def cmd_list(_args) -> None:
    _print_table(list_plugins())


def cmd_update(args) -> None:
    all_plugins = list_plugins()

    if args.all:
        targets = all_plugins
    elif args.plugins:
        targets = resolve_plugins(args.plugins, all_plugins)
    else:
        sys.exit("Error: specify plugin name(s) or --all")

    total = len(targets)
    print(f"Updating {total} plugin{'s' if total != 1 else ''}...\n")

    before_versions = {p["id"]: p.get("version") for p in targets}
    raw_by_id = {}

    if args.parallel and total > 1:
        with ThreadPoolExecutor(max_workers=min(8, total)) as ex:
            futures = {ex.submit(update_one, p): p["id"] for p in targets}
            for fut in as_completed(futures):
                pid = futures[fut]
                try:
                    r = fut.result()
                except Exception as exc:
                    r = {"id": pid, "code": 1, "message": str(exc)}
                raw_by_id[pid] = r
                print(f"  ran {pid}")
    else:
        for i, p in enumerate(targets, 1):
            print(f"[{i}/{total}] {p['id']}...", end=" ", flush=True)
            r = update_one(p)
            raw_by_id[p["id"]] = r
            print("done" if r["code"] == 0 else "error")

    # Re-list once, after every update has run, to get real post-update
    # versions — cheaper than one `claude plugin list` per plugin and gives
    # every status the same consistent snapshot to compare against.
    after_versions = {p["id"]: p.get("version") for p in list_plugins()}

    icons = {"updated": "✔", "current": "─", "failed": "✗"}
    results = []
    for p in targets:
        pid = p["id"]
        r = raw_by_id[pid]
        after_version = after_versions.get(pid)
        status = resolve_update_status(before_versions[pid], after_version, r["code"])
        # A zero exit code paired with "vanished from the list" would print
        # the update's own success text next to a ✗ — keep the failure
        # reason honest instead of echoing a message that contradicts it.
        message = r["message"] if not (r["code"] == 0 and after_version is None) else "plugin no longer listed after update"
        results.append({
            "id": pid,
            "status": status,
            "message": message,
            "before_version": before_versions[pid],
            "after_version": after_version,
        })

    print(f"\n{'─' * 40}")
    print("Results (version before → after):")
    for r in results:
        if r["status"] == "updated":
            detail = f"{r['before_version']} → {r['after_version']}"
        elif r["status"] == "current":
            detail = f"{r['after_version']} (unchanged)"
        else:
            detail = r["message"]
        print(f"  {icons[r['status']]} {r['id']}  {detail}")

    updated = sum(1 for r in results if r["status"] == "updated")
    current = sum(1 for r in results if r["status"] == "current")
    failed = [r for r in results if r["status"] == "failed"]

    print(f"\nUpdated: {updated}  Already current: {current}  Failed: {len(failed)}")

    if updated:
        print(
            "\nNote: restart Claude Code to load the updated plugin(s) — "
            "any MCP servers or skills they bundle keep running the old "
            "version in this session until then."
        )

    if failed:
        print("\nFailed plugins:")
        for r in failed:
            print(f"  ✗ {r['id']}: {r['message']}")
        sys.exit(1)


def uninstall_one(plugin: dict, keep_data: bool = False, prune: bool = False) -> dict:
    """Uninstall a single plugin. Returns result dict with status key."""
    pid = plugin["id"]
    scope = plugin.get("scope", "user")
    cli_args = ["plugin", "uninstall", pid, "-s", scope]
    if keep_data:
        cli_args.append("--keep-data")
    if prune:
        cli_args += ["--prune", "-y"]
    code, out, err = run_claude(cli_args)
    message = (out + err).strip()
    if code != 0:
        return {"id": pid, "status": "failed", "message": message}
    return {"id": pid, "status": "uninstalled", "message": message}


def cmd_uninstall(args) -> None:
    if not args.plugins:
        sys.exit("Error: specify plugin name(s) to uninstall")

    all_plugins = list_plugins()
    targets = resolve_plugins(args.plugins, all_plugins)
    total = len(targets)

    if not args.yes:
        names = ", ".join(p["id"] for p in targets)
        print(f"About to uninstall {total} plugin{'s' if total != 1 else ''}: {names}")
        answer = input("Proceed? [y/N] ").strip().lower()
        if answer != "y":
            print("Aborted.")
            return

    print(f"Uninstalling {total} plugin{'s' if total != 1 else ''}...\n")
    results = []
    for i, p in enumerate(targets, 1):
        print(f"[{i}/{total}] {p['id']}...", end=" ", flush=True)
        r = uninstall_one(p, keep_data=args.keep_data, prune=args.prune)
        print("✔" if r["status"] == "uninstalled" else "✗")
        results.append(r)

    failed = [r for r in results if r["status"] == "failed"]
    succeeded = total - len(failed)

    print(f"\n{'─' * 40}")
    print(f"Uninstalled: {succeeded}  Failed: {len(failed)}")

    if failed:
        print("\nFailed plugins:")
        for r in failed:
            print(f"  ✗ {r['id']}: {r['message']}")
        sys.exit(1)


def parse_mcp_list(output: str) -> list:
    """Parse `claude mcp list` text into records.

    Line shape is "<name>: <command> - <status>". Plugin-bundled server
    names look like "plugin:<plugin>:<server>" (colon-separated, no space),
    so splitting on the first ": " (colon + space) safely separates the
    name from the command even though the name itself contains colons.
    """
    records = []
    for line in output.splitlines():
        line = line.strip()
        if ": " not in line or " - " not in line:
            continue
        name, rest = line.split(": ", 1)
        command, _, status = rest.rpartition(" - ")
        records.append({"name": name.strip(), "command": command.strip(), "status": status.strip()})
    return records


def classify_mcp_server(name: str) -> str:
    """Which update path (if any) owns this MCP server.

    - "plugin": bundled inside a plugin — `update` above already covers it.
    - "host": a claude.ai-managed connector — Anthropic operates it, not us.
    - "standalone": registered directly via `claude mcp add` — nothing in
      the `claude` CLI updates these; the underlying package (npm/uv/pip)
      must be updated by hand.
    """
    if name.startswith("plugin:"):
        return "plugin"
    if name.startswith("claude.ai "):
        return "host"
    return "standalone"


def cmd_doctor(_args) -> None:
    """Surface the one class of MCP server this tool cannot update: those
    registered standalone via `claude mcp add` rather than bundled in a
    plugin. `claude mcp` has no update subcommand, so these can only be
    refreshed through their own package manager — this just makes them
    visible instead of silently unmanaged.
    """
    code, out, err = run_claude(["mcp", "list"])
    if code != 0:
        sys.exit(f"Error running 'claude mcp list': {err.strip()}")

    standalone = [r for r in parse_mcp_list(out) if classify_mcp_server(r["name"]) == "standalone"]

    print("Standalone MCP servers (not bundled in any plugin):\n")
    if not standalone:
        print("  none — every configured MCP server is either a claude.ai")
        print("  host connector or bundled inside a plugin (covered by `update`).")
        return

    for r in standalone:
        print(f"  {r['name']}: {r['command']}  [{r['status']}]")
    print(f"\n{len(standalone)} standalone server{'s' if len(standalone) != 1 else ''} found.")
    print("`claude mcp` has no update subcommand — refresh these via their own")
    print("package manager (npm/uv/pip), not this tool.")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="plugin_manager",
        description="Claude plugin manager — list, update, and uninstall plugins",
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

    args = parser.parse_args()
    if args.command == "list":
        cmd_list(args)
    elif args.command == "update":
        cmd_update(args)
    elif args.command in ("uninstall", "remove"):
        cmd_uninstall(args)
    elif args.command == "doctor":
        cmd_doctor(args)


if __name__ == "__main__":
    main()
