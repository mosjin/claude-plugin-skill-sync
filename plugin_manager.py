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
import os
import re
import secrets
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path


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


SNAPSHOT_SCHEMA_VERSION = 1
DEFAULT_SNAPSHOT_DIR = Path("snapshots")
ENV_IDENTITY = "PLUGIN_MANAGER_IDENTITY"
ENV_MACHINE = "PLUGIN_MANAGER_MACHINE"
ENV_SNAPSHOT_DIR = "PLUGIN_MANAGER_SNAPSHOT_DIR"


def _sanitize_label(label: str) -> str:
    """Filesystem-safe slug: lowercase, non-alnum runs collapsed to '-'."""
    slug = re.sub(r"[^a-z0-9]+", "-", label.strip().lower()).strip("-")
    if not slug:
        sys.exit(f"Error: '{label}' has no usable characters for a filename")
    return slug


def resolve_identity(args) -> str:
    """Account label for a snapshot — required, never defaulted from a raw
    email. Keep it short (e.g. email local-part): it becomes part of a
    filename and of any future upload payload.
    """
    identity = args.identity or os.environ.get(ENV_IDENTITY)
    if not identity:
        sys.exit(
            f"Error: identity required. Pass --identity <label> or set "
            f"{ENV_IDENTITY} (e.g. your email's local-part, not the full "
            f"address)."
        )
    if "@" in identity:
        sys.exit(
            f"Error: --identity/{ENV_IDENTITY} looks like a full email "
            f"address ('{identity}'). Use a short label instead (e.g. the "
            f"part before '@') — the full address would be written into "
            f"the snapshot verbatim."
        )
    return _sanitize_label(identity)


def resolve_machine(args) -> str:
    """Machine label for a snapshot — required, never defaulted from the
    real hostname, so nothing machine-identifying leaks without the user
    choosing to put it there.
    """
    machine = args.machine or os.environ.get(ENV_MACHINE)
    if not machine:
        sys.exit(
            f"Error: machine label required. Pass --machine <label> or set "
            f"{ENV_MACHINE} (e.g. 'work-laptop')."
        )
    return _sanitize_label(machine)


def resolve_snapshot_dir(args) -> Path:
    if args.dir:
        return Path(args.dir)
    env_dir = os.environ.get(ENV_SNAPSHOT_DIR)
    if env_dir:
        return Path(env_dir)
    return DEFAULT_SNAPSHOT_DIR


def whitelist_plugin(plugin: dict) -> dict:
    """Build the portable, desensitized plugin record.

    Field-by-field allowlist, not collect-then-strip: a field Anthropic
    adds to `claude plugin list --json` tomorrow (e.g. an mcpServers arg
    holding an absolute local path) cannot leak into a snapshot until
    someone adds it here deliberately. `mcpServers` shape is defensive
    (not just a missing-key guard) — a future CLI version could change it
    from an object to something else without warning.
    """
    mcp_servers = plugin.get("mcpServers")
    if not isinstance(mcp_servers, dict):
        mcp_servers = {}
    return {
        "id": plugin["id"],
        "version": plugin.get("version"),
        "scope": plugin.get("scope"),
        "enabled": plugin.get("enabled", True),
        "mcp_server_names": sorted(mcp_servers.keys()),
    }


def scan_skills(root, owner: str) -> list:
    """Find `<root>/skills/*/SKILL.md` and return portable skill records.

    `owner` says where a skill comes from ("user", "project", or
    "plugin:<id>") — the skill's filesystem path is never recorded.
    """
    skills_dir = Path(root) / "skills"
    if not skills_dir.is_dir():
        return []
    records = []
    for entry in sorted(skills_dir.iterdir()):
        if (entry / "SKILL.md").is_file():
            records.append({"name": entry.name, "owner": owner})
    return records


def discover_skill_roots(all_plugins: list) -> list:
    """(root_path, owner) pairs to scan for skills: the user-level config
    dir, the current project, and every installed plugin's own tree.

    `CLAUDE_CONFIG_DIR` — when set — replaces `~/.claude` as Claude Code's
    own config root rather than adding to it, so this mirrors that instead
    of scanning both (which would report skills Claude Code never loads).
    """
    roots = []
    seen = set()

    def add(path, owner):
        path = Path(path)
        # normcase + normpath so the same directory typed with different
        # case or separators (common on Windows) dedupes correctly.
        key = (os.path.normcase(os.path.normpath(str(path))), owner)
        if key not in seen:
            seen.add(key)
            roots.append((path, owner))

    config_dir = os.environ.get("CLAUDE_CONFIG_DIR")
    if config_dir:
        add(config_dir, "user")
    else:
        add(Path.home() / ".claude", "user")
    add(Path.cwd() / ".claude", "project")
    for plugin in all_plugins:
        install_path = plugin.get("installPath")
        if install_path:
            add(install_path, f"plugin:{plugin['id']}")
    return roots


def capture_snapshot(identity: str, machine: str, all_plugins: list) -> dict:
    """Build the full desensitized snapshot — the one schema `save`,
    `merge`, and (later) `upload`/`apply` all share.
    """
    skills = []
    for root, owner in discover_skill_roots(all_plugins):
        skills.extend(scan_skills(root, owner))
    return {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "kind": "snapshot",
        "identity": identity,
        "machine": machine,
        "platform": sys.platform,
        "captured_at": datetime.now(timezone.utc).date().isoformat(),
        "plugins": [whitelist_plugin(p) for p in all_plugins],
        "skills": skills,
    }


def snapshot_filename(snapshot: dict) -> str:
    """identity/machine are already sanitized by resolve_identity/
    resolve_machine — reused as-is so the filename and the JSON body never
    disagree. A random token (not a precise timestamp) makes same-day
    filenames from the same machine unique without fingerprinting the
    time of day `captured_at` was deliberately coarsened to hide.
    """
    token = secrets.token_hex(4)
    return (
        f"{snapshot['identity']}__{snapshot['machine']}__"
        f"{snapshot['platform']}__{snapshot['captured_at']}__{token}.json"
    )


def cmd_save(args) -> None:
    identity = resolve_identity(args)
    machine = resolve_machine(args)
    all_plugins = list_plugins()
    snapshot = capture_snapshot(identity, machine, all_plugins)

    out_dir = resolve_snapshot_dir(args)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / snapshot_filename(snapshot)
    out_path.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"Saved snapshot: {out_path}")
    print(f"  {len(snapshot['plugins'])} plugin(s), {len(snapshot['skills'])} skill(s)")
    print(
        f"  project-scope skills were scanned from {Path.cwd()} — run `save` "
        f"from the same project dir on every machine for project skills to "
        f"compare meaningfully"
    )
    if out_dir == DEFAULT_SNAPSHOT_DIR:
        print(
            f"\nWarning: '{DEFAULT_SNAPSHOT_DIR}/' is local to this machine "
            f"only — it is gitignored and nothing syncs it elsewhere. To "
            f"merge across machines, point every machine's --dir (or "
            f"{ENV_SNAPSHOT_DIR}) at a location you sync yourself (private "
            f"repo, cloud-sync folder, etc)."
        )


def load_snapshots(dir_path: Path) -> list:
    """Read every `save`-produced snapshot in `dir_path`. A `merge --out`
    result living in the same directory is recognized by its `kind` marker
    and skipped rather than fed back in as an input.
    """
    if not dir_path.is_dir():
        sys.exit(f"Error: snapshot dir not found: {dir_path}")
    snapshots = []
    for path in sorted(dir_path.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            sys.exit(f"Error reading snapshot {path}: {exc}")
        if not isinstance(data, dict):
            sys.exit(f"Error: {path} is not a JSON object (got {type(data).__name__})")
        if data.get("kind") == "merged":
            continue
        if data.get("schema_version") != SNAPSHOT_SCHEMA_VERSION:
            sys.exit(
                f"Error: {path} has schema_version {data.get('schema_version')!r}, "
                f"expected {SNAPSHOT_SCHEMA_VERSION}"
            )
        snapshots.append(data)
    if not snapshots:
        sys.exit(f"Error: no snapshot files found in {dir_path}")
    return snapshots


_REQUIRED_SNAPSHOT_FIELDS = {"identity", "machine", "platform", "captured_at", "plugins", "skills"}


def merge_snapshots(snapshots: list) -> dict:
    """Pure function: list of snapshot dicts in, one drift-view dict out.
    Raises ValueError on bad input — no process-level side effects, so
    callers (CLI or test) decide how to react.

    Merge is always a read-time projection over independent, append-only
    per-machine snapshots — nothing here mutates a shared file. For each
    machine, exactly ONE snapshot (the most recently captured) supplies
    that machine's plugins/skills wholesale — a snapshot is a point-in-time
    whole, never merged field-by-field with another snapshot of the same
    machine, so an uninstalled plugin or a deleted skill from an older save
    cannot linger forever. `captured_at` is date-only (see `capture_snapshot`),
    so same-day snapshots for one machine tie; ties are broken by input
    order (`load_snapshots` feeds files in filename-sorted order) and
    surfaced in the returned `notes` list rather than resolved silently.
    Calling this twice with the same input list yields the same output.
    """
    if not snapshots:
        raise ValueError("no snapshots to merge")

    for snap in snapshots:
        if not isinstance(snap, dict):
            raise ValueError(f"snapshot is not a JSON object: {snap!r}")
        missing = _REQUIRED_SNAPSHOT_FIELDS - snap.keys()
        if missing:
            raise ValueError(f"snapshot missing required field(s): {sorted(missing)}")

    identities = {s["identity"] for s in snapshots}
    if len(identities) > 1:
        raise ValueError(f"snapshots belong to different identities: {sorted(identities)}")

    latest_by_machine = {}
    notes = []
    for snap in snapshots:
        machine_key = f"{snap['machine']}@{snap['platform']}"
        existing = latest_by_machine.get(machine_key)
        if existing is None or snap["captured_at"] > existing["captured_at"]:
            latest_by_machine[machine_key] = snap
        elif snap["captured_at"] == existing["captured_at"]:
            notes.append(
                f"{machine_key}: multiple snapshots dated {snap['captured_at']} — "
                f"using the last one given to merge; delete stale saves for "
                f"this machine to avoid relying on tie-break order"
            )
            latest_by_machine[machine_key] = snap

    machines = {
        key: {"machine": snap["machine"], "platform": snap["platform"], "captured_at": snap["captured_at"]}
        for key, snap in latest_by_machine.items()
    }

    plugins = {}
    skills = {}
    for machine_key, snap in latest_by_machine.items():
        for plugin in snap["plugins"]:
            entry = plugins.setdefault(plugin["id"], {"present_on": {}})
            entry["present_on"][machine_key] = {
                "version": plugin["version"],
                "scope": plugin["scope"],
                "enabled": plugin["enabled"],
            }
        for skill in snap["skills"]:
            skill_key = f"{skill['owner']}/{skill['name']}"
            entry = skills.setdefault(skill_key, {"present_on": {}})
            entry["present_on"][machine_key] = True

    for entry in plugins.values():
        infos = entry["present_on"].values()
        drift = []
        if len({i["version"] for i in infos}) > 1:
            drift.append("version")
        if len({i["scope"] for i in infos}) > 1:
            drift.append("scope")
        if len({i["enabled"] for i in infos}) > 1:
            drift.append("enabled")
        entry["drift"] = drift

    return {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "kind": "merged",
        "identity": identities.pop(),
        "machines": machines,
        "plugins": plugins,
        "skills": skills,
        "notes": notes,
    }


def _print_merge_table(merged: dict) -> None:
    machine_keys = sorted(merged["machines"])
    print(f"Identity: {merged['identity']}")
    print(f"Machines: {', '.join(machine_keys)}\n")

    print("Plugins:")
    for pid in sorted(merged["plugins"]):
        entry = merged["plugins"][pid]
        marker = f" ({'/'.join(entry['drift'])} drift)" if entry["drift"] else ""
        where = ", ".join(
            f"{m}={info['version']}" for m, info in sorted(entry["present_on"].items())
        )
        missing = [m for m in machine_keys if m not in entry["present_on"]]
        missing_note = f"  [missing on: {', '.join(missing)}]" if missing else ""
        print(f"  {pid}{marker}: {where}{missing_note}")

    print("\nSkills:")
    for key in sorted(merged["skills"]):
        entry = merged["skills"][key]
        where = ", ".join(sorted(entry["present_on"]))
        missing = [m for m in machine_keys if m not in entry["present_on"]]
        missing_note = f"  [missing on: {', '.join(missing)}]" if missing else ""
        print(f"  {key}: {where}{missing_note}")

    if merged["notes"]:
        print("\nNotes:")
        for note in merged["notes"]:
            print(f"  - {note}")


def cmd_merge(args) -> None:
    dir_path = resolve_snapshot_dir(args)
    snapshots = load_snapshots(dir_path)
    try:
        merged = merge_snapshots(snapshots)
    except ValueError as exc:
        sys.exit(f"Error: {exc}")
    _print_merge_table(merged)

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(merged, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\nMerged view written to: {out_path}")


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

    save = sub.add_parser("save", help="Save a desensitized snapshot of installed plugins/skills")
    save.add_argument("--identity", help=f"Account label (default: ${ENV_IDENTITY})")
    save.add_argument("--machine", help=f"Machine label (default: ${ENV_MACHINE})")
    save.add_argument(
        "--dir",
        help=f"Snapshot output dir (default: ${ENV_SNAPSHOT_DIR} or ./{DEFAULT_SNAPSHOT_DIR}, local-only)",
    )

    merge = sub.add_parser("merge", help="Merge snapshots from a directory into one drift view")
    merge.add_argument(
        "--dir",
        help=f"Snapshot input dir (default: ${ENV_SNAPSHOT_DIR} or ./{DEFAULT_SNAPSHOT_DIR})",
    )
    merge.add_argument("--out", help="Optional path to also write the merged view as JSON")

    args = parser.parse_args()
    if args.command == "list":
        cmd_list(args)
    elif args.command == "update":
        cmd_update(args)
    elif args.command in ("uninstall", "remove"):
        cmd_uninstall(args)
    elif args.command == "doctor":
        cmd_doctor(args)
    elif args.command == "save":
        cmd_save(args)
    elif args.command == "merge":
        cmd_merge(args)


if __name__ == "__main__":
    main()
