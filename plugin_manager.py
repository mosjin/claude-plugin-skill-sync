"""Claude plugin manager — list, update, and snapshot-sync plugins/skills.

Usage:
    python plugin_manager.py list
    python plugin_manager.py update caveman
    python plugin_manager.py update caveman ecc eduforge
    python plugin_manager.py update --all
    python plugin_manager.py update --all --parallel
    python plugin_manager.py save --identity mosjin --machine work-laptop
    python plugin_manager.py merge
    python plugin_manager.py upload snapshots/<file>.json
    python plugin_manager.py gist-list
    python plugin_manager.py fetch --gist-id <id>
    python plugin_manager.py apply merged.json --all -y --scope user
"""

import argparse
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


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


def run_gh(args: list) -> tuple:
    """Thin subprocess seam for the GitHub CLI — mirrors run_claude() so
    `upload`/`fetch` are mockable the same way the rest of the file is.
    """
    exe = shutil.which("gh")
    if not exe:
        sys.exit("Error: 'gh' not found in PATH. Is GitHub CLI installed?")
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


# The only scope values `claude plugin update/uninstall -s` accepts (per
# `--help`). Single source of truth — both call sites below check against
# this instead of each hardcoding the set.
CLI_MANAGEABLE_SCOPES = {"user", "project", "local", "managed"}

# One-line reason surfaced for any scope outside CLI_MANAGEABLE_SCOPES
# (currently just "synced"), phrased after the CLI's own explanation so it
# doesn't just repeat a bare "Invalid scope" error.
UNMANAGEABLE_SCOPE_MESSAGE = (
    "synced from your claude.ai account with no marketplace backing — "
    "manage it on claude.ai, or run `claude plugin disable` on this machine"
)


def is_cli_manageable(plugin: dict) -> bool:
    """Whether `claude plugin update/uninstall -s <scope>` accepts this
    plugin's scope at all.

    A plugin can carry a `scope` the CLI itself never accepts as an `-s`
    value — e.g. `"synced"` for plugins pulled from a claude.ai account
    with no local marketplace backing. Passing that straight to `-s`
    doesn't fail with the CLI's own explanation; it fails with a generic
    "Invalid scope" error. Callers check this first and skip instead.
    """
    return plugin.get("scope") in CLI_MANAGEABLE_SCOPES


def update_one(plugin: dict) -> dict:
    """Run `claude plugin update <id>`. Returns the raw outcome only —
    status (updated/current/failed) is resolved by the caller from a
    before/after version diff, not by guessing at stdout wording.
    """
    pid = plugin["id"]
    if not is_cli_manageable(plugin):
        return {"id": pid, "code": None, "message": UNMANAGEABLE_SCOPE_MESSAGE, "unmanageable": True}
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
            print("skipped" if r.get("unmanageable") else ("done" if r["code"] == 0 else "error"))

    # Re-list once, after every update has run, to get real post-update
    # versions — cheaper than one `claude plugin list` per plugin and gives
    # every status the same consistent snapshot to compare against.
    after_versions = {p["id"]: p.get("version") for p in list_plugins()}

    icons = {"updated": "✔", "current": "─", "skipped": "•", "failed": "✗"}
    results = []
    for p in targets:
        pid = p["id"]
        r = raw_by_id[pid]
        if r.get("unmanageable"):
            # Never sent to the CLI at all — nothing to diff versions on.
            results.append({
                "id": pid,
                "status": "skipped",
                "message": r["message"],
                "before_version": before_versions[pid],
                "after_version": before_versions[pid],
            })
            continue
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
    skipped = [r for r in results if r["status"] == "skipped"]
    failed = [r for r in results if r["status"] == "failed"]

    print(f"\nUpdated: {updated}  Already current: {current}  Skipped: {len(skipped)}  Failed: {len(failed)}")

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
    if not is_cli_manageable(plugin):
        return {"id": pid, "status": "skipped", "message": UNMANAGEABLE_SCOPE_MESSAGE}
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
    icons = {"uninstalled": "✔", "skipped": "•", "failed": "✗"}
    for i, p in enumerate(targets, 1):
        print(f"[{i}/{total}] {p['id']}...", end=" ", flush=True)
        r = uninstall_one(p, keep_data=args.keep_data, prune=args.prune)
        print(icons[r["status"]])
        results.append(r)

    skipped = [r for r in results if r["status"] == "skipped"]
    failed = [r for r in results if r["status"] == "failed"]
    succeeded = total - len(failed) - len(skipped)

    print(f"\n{'─' * 40}")
    print(f"Uninstalled: {succeeded}  Skipped: {len(skipped)}  Failed: {len(failed)}")

    if skipped:
        print("\nSkipped (not manageable by this tool):")
        for r in skipped:
            print(f"  • {r['id']}: {r['message']}")

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
ENV_GIST_ID = "PLUGIN_MANAGER_GIST_ID"

# Tag `upload` stamps on any gist it creates, and the only thing `gist-list`/
# auto-discovery filter on. Single definition — both call sites below (the
# `-d` at create time, the `--filter` at list time) reference this instead
# of each hardcoding the string.
SNAPSHOT_GIST_DESCRIPTION = "claude-plugin-skill-sync snapshots"


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


def whitelist_marketplace(m: dict) -> Optional[dict]:
    """Build the portable, desensitized marketplace record.

    Same field-by-field allowlist discipline as whitelist_plugin:
    `installLocation` (a local filesystem path, may embed a username) is
    never captured. Only the two remote source shapes `claude plugin
    marketplace add` accepts are portable — `source: "github"` carries
    `repo` ("owner/repo"), `source: "git"` carries a full `url`. Anything
    else (e.g. a marketplace added from a local directory) has nothing
    portable to record; still keep name+source so `apply` on another
    machine can at least explain why it can't auto-add this one, instead
    of silently pretending the marketplace never existed.
    """
    name = m.get("name")
    source = m.get("source")
    if not name or not source:
        return None
    record = {"name": name, "source": source}
    if source == "github" and m.get("repo"):
        record["repo"] = m["repo"]
    elif source == "git" and m.get("url"):
        record["url"] = m["url"]
    return record


def list_marketplaces() -> list:
    """Every marketplace configured on this machine, as `claude plugin
    marketplace list --json` reports it (name, source, repo/url,
    installLocation) — the single call site both
    marketplace_install_locations() and capture_snapshot() build on.
    """
    code, out, err = run_claude(["plugin", "marketplace", "list", "--json"])
    if code != 0:
        sys.exit(f"Error listing marketplaces: {err.strip()}")
    marketplaces = json.loads(out)
    if not isinstance(marketplaces, list):
        sys.exit(f"Error: unexpected response from claude (expected list, got {type(marketplaces).__name__})")
    return marketplaces


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

    `marketplaces` is additive (not in _REQUIRED_SNAPSHOT_FIELDS) — a
    snapshot saved before this field existed must still load and merge.
    """
    skills = []
    for root, owner in discover_skill_roots(all_plugins):
        skills.extend(scan_skills(root, owner))
    marketplaces = [m for m in (whitelist_marketplace(m) for m in list_marketplaces()) if m]
    return {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "kind": "snapshot",
        "identity": identity,
        "machine": machine,
        "platform": sys.platform,
        "captured_at": datetime.now(timezone.utc).date().isoformat(),
        "plugins": [whitelist_plugin(p) for p in all_plugins],
        "skills": skills,
        "marketplaces": marketplaces,
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
    marketplaces = {}
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
        # Absent on snapshots saved before this field existed — .get(), not
        # a required field, so those old snapshots still merge cleanly.
        for market in snap.get("marketplaces", []):
            entry = marketplaces.setdefault(market["name"], {"present_on": {}})
            entry["present_on"][machine_key] = {k: v for k, v in market.items() if k != "name"}

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

    for name, entry in marketplaces.items():
        infos = list(entry["present_on"].values())
        # Every machine's recording of this marketplace's source should
        # agree — if they don't, `apply` needs one to trust; last one
        # written wins (dict iteration over latest_by_machine), same
        # tie-break philosophy as the per-machine snapshot pick above.
        sources = {json.dumps(i, sort_keys=True) for i in infos}
        entry["drift"] = ["source"] if len(sources) > 1 else []
        entry["source_record"] = infos[-1]

    return {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "kind": "merged",
        "identity": identities.pop(),
        "machines": machines,
        "plugins": plugins,
        "skills": skills,
        "marketplaces": marketplaces,
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


_GIST_URL_RE = re.compile(r"https://gist\.github\.com/[^/\s]+/([0-9a-fA-F]+)")


def _gist_id_marker_path(snapshot_dir: Path) -> Path:
    """A gist id is paired with the snapshot dir it syncs, not with any
    single command invocation — caching it here (next to the data it
    identifies) means a second `upload` in the same dir doesn't need
    --gist-id/the env var repeated, and can't silently spray snapshots
    across a fresh gist every time the flag is forgotten."""
    return snapshot_dir / ".gist_id"


def parse_gist_list(output: str) -> list:
    """Parse `gh gist list` TSV output into records.

    Line shape is "<id>\\t<description>\\t<file count>\\t<visibility>\\t<updated>".
    No `--json` support on this gh subcommand (verified against `gh gist list
    --help`), so this is a plain split, not a json.loads.
    """
    records = []
    for line in output.splitlines():
        parts = line.split("\t")
        if len(parts) != 5:
            continue
        gist_id, description, files, visibility, updated = parts
        records.append({
            "id": gist_id.strip(),
            "description": description.strip(),
            "files": files.strip(),
            "visibility": visibility.strip(),
            "updated_at": updated.strip(),
        })
    return records


def list_snapshot_gists() -> list:
    """This tool's own gists — anything tagged SNAPSHOT_GIST_DESCRIPTION on
    the authenticated GitHub account. Backs both `gist-list` and
    resolve_gist_id's auto-discovery.
    """
    code, out, err = run_gh(["gist", "list", "--filter", SNAPSHOT_GIST_DESCRIPTION, "-L", "100"])
    if code != 0:
        sys.exit(f"Error listing gists: {_clean_gh_error(out, err, code)}")
    return parse_gist_list(out)


def cmd_gist_list(_args) -> None:
    gists = list_snapshot_gists()
    if not gists:
        print(f"No gists tagged '{SNAPSHOT_GIST_DESCRIPTION}' found on this GitHub account.")
        print("Run `upload` on a machine that has a snapshot to create one.")
        return

    id_w = max(len(g["id"]) for g in gists) + 2
    vis_w = max(len(g["visibility"]) for g in gists) + 2
    header = f"{'Gist ID':<{id_w}} {'Visibility':<{vis_w}} {'Files':<10} Updated"
    print(header)
    print("─" * len(header))
    for g in gists:
        print(f"{g['id']:<{id_w}} {g['visibility']:<{vis_w}} {g['files']:<10} {g['updated_at']}")
    print(f"\n{len(gists)} gist{'s' if len(gists) != 1 else ''} found.")
    print("Use one with: fetch --gist-id <id>  (or upload --gist-id <id>)")


def resolve_gist_id(args, snapshot_dir: Optional[Path] = None) -> Optional[str]:
    """No sys.exit here for the zero-match case — unlike identity/machine, a
    missing gist id is valid for `upload` (it means "create a new one").
    `fetch` requires one and checks for it itself.

    Precedence: --gist-id flag, then the env var, then this snapshot dir's
    own cached marker file, then auto-discovery — exactly one gist tagged
    SNAPSHOT_GIST_DESCRIPTION on this account means there's no ambiguity to
    ask the user about, so use it and cache it (this is what lets a second
    machine `fetch`/`upload` without ever typing an id by hand). Two or
    more candidates IS ambiguous — sys.exit here rather than let `upload`
    silently create a third, disconnected gist.
    """
    if args.gist_id:
        return args.gist_id
    env_id = os.environ.get(ENV_GIST_ID)
    if env_id:
        return env_id
    if snapshot_dir is not None:
        marker = _gist_id_marker_path(snapshot_dir)
        if marker.is_file():
            cached = marker.read_text(encoding="utf-8").strip()
            if cached:
                return cached

    matches = list_snapshot_gists()
    if len(matches) > 1:
        ids = ", ".join(m["id"] for m in matches)
        sys.exit(
            f"Error: {len(matches)} gists tagged '{SNAPSHOT_GIST_DESCRIPTION}' "
            f"found ({ids}) — ambiguous. Run `gist-list`, then pass --gist-id "
            f"<id> explicitly."
        )
    if len(matches) == 1:
        gist_id = matches[0]["id"]
        print(f"Auto-detected gist {gist_id} (only one tagged '{SNAPSHOT_GIST_DESCRIPTION}')")
        if snapshot_dir is not None:
            snapshot_dir.mkdir(parents=True, exist_ok=True)
            _gist_id_marker_path(snapshot_dir).write_text(gist_id, encoding="utf-8")
        return gist_id
    return None


def _extract_gist_id(create_output: str) -> str:
    """`gh gist create` prints the new gist's URL somewhere in its output —
    search rather than assume it's the entire (or even the first) line, and
    fail loudly instead of returning a hard-to-notice empty string."""
    match = _GIST_URL_RE.search(create_output)
    if not match:
        sys.exit(
            f"Error: could not find a gist URL in `gh gist create` output: "
            f"{create_output.strip()!r}"
        )
    return match.group(1)


def _clean_gh_error(out: str, err: str, code: int) -> str:
    """Prefer whichever stream actually has content — `err` being
    whitespace-only must not swallow a real message that landed in `out`."""
    message = err.strip() or out.strip() or "no output"
    return f"{message} (exit {code})"


def _is_valid_snapshot_file(path: Path) -> bool:
    """Refuse anything that isn't a `save`-produced snapshot — guards both
    `upload` (don't push `merged.json` or an unrelated file under a gist
    filename that silently replaces a real snapshot) and `fetch` (don't
    import a file that would make every later `merge` in this dir explode,
    permanently, since 'already exists locally' is the only skip check).
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False
    return (
        isinstance(data, dict)
        and data.get("kind") == "snapshot"
        and data.get("schema_version") == SNAPSHOT_SCHEMA_VERSION
    )


def cmd_upload(args) -> None:
    file_path = Path(args.file)
    if not file_path.is_file():
        sys.exit(f"Error: snapshot file not found: {file_path}")
    if not _is_valid_snapshot_file(file_path):
        sys.exit(
            f"Error: {file_path} doesn't look like a `save`-produced "
            f"snapshot (expected kind='snapshot', schema_version="
            f"{SNAPSHOT_SCHEMA_VERSION})."
        )

    snapshot_dir = resolve_snapshot_dir(args)
    gist_id = resolve_gist_id(args, snapshot_dir)
    if gist_id:
        code, out, err = run_gh(["gist", "edit", gist_id, "--add", str(file_path)])
        if code != 0:
            sys.exit(f"Error uploading to gist {gist_id}: {_clean_gh_error(out, err, code)}")
        print(f"Uploaded {file_path.name} to gist {gist_id}")
    else:
        code, out, err = run_gh(["gist", "create", str(file_path), "-d", SNAPSHOT_GIST_DESCRIPTION])
        if code != 0:
            sys.exit(f"Error creating gist: {_clean_gh_error(out, err, code)}")
        new_id = _extract_gist_id(out + err)
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        _gist_id_marker_path(snapshot_dir).write_text(new_id, encoding="utf-8")
        print(f"Created new gist ({(out + err).strip()})")
        print("  secret = unlisted, not private — anyone with the link can read it.")
        print(
            f"\nCached in {_gist_id_marker_path(snapshot_dir)} — future "
            f"upload/fetch in this dir reuse it automatically. To reuse it "
            f"from a different --dir, set {ENV_GIST_ID}={new_id}."
        )


def cmd_fetch(args) -> None:
    out_dir = resolve_snapshot_dir(args)
    gist_id = resolve_gist_id(args, out_dir)
    if not gist_id:
        sys.exit(f"Error: gist id required. Pass --gist-id <id> or set {ENV_GIST_ID}.")

    out_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        clone_dir = Path(tmp) / "gist"
        code, out, err = run_gh(["gist", "clone", gist_id, str(clone_dir)])
        if code != 0:
            sys.exit(f"Error cloning gist {gist_id}: {_clean_gh_error(out, err, code)}")

        found = copied = invalid = 0
        for src in sorted(clone_dir.glob("*.json")):
            found += 1
            dest = out_dir / src.name
            if dest.exists():
                continue
            if not _is_valid_snapshot_file(src):
                invalid += 1
                continue
            shutil.copyfile(src, dest)
            copied += 1

        print(f"Fetched gist {gist_id}: {found} snapshot(s) found, {copied} new one(s) copied into {out_dir}")
        if invalid:
            print(f"  skipped {invalid} file(s) that aren't valid `save` snapshots")


LANG_MESSAGES = {
    "en": {
        "nothing_missing": "Nothing missing — every plugin in the merged snapshot is already installed here.",
        "installable_header": "Installable (marketplace already available here):",
        "addable_header": "Needs a marketplace add first (will be added automatically with -y):",
        "skip_header": "Skipped (no marketplace source on record — add it manually first):",
        "dry_run_note": "Dry run — pass -y to actually install. Nothing was installed.",
        "scope_required": "Error: --scope is required to actually install (user/project/local).",
        "select_prompt": "Install which? [a]ll / [n]one / comma-separated numbers: ",
        "adding_marketplace": "Adding marketplace '{name}' from {source}...",
        "installing": "Installing {id} (scope={scope})...",
        "summary": "Installed: {ok}  Failed: {fail}",
    },
    "zh": {
        "nothing_missing": "没有缺的插件 — 合并快照里的插件这台机器都已经装了。",
        "installable_header": "可装（这台机器已有对应 marketplace）：",
        "addable_header": "需要先加 marketplace 源（加 -y 会自动加）：",
        "skip_header": "跳过（没记录到 marketplace 源，先手动加）：",
        "dry_run_note": "预览模式，未实际安装。加 -y 才会真正安装。",
        "scope_required": "错误：真正安装需要 --scope（user/project/local）。",
        "select_prompt": "装哪些？[a]全部 / [n]不装 / 逗号分隔序号：",
        "adding_marketplace": "正在加 marketplace '{name}'（源：{source}）...",
        "installing": "正在装 {id}（scope={scope}）...",
        "summary": "已装：{ok}  失败：{fail}",
    },
}
DEFAULT_LANG = "en"


def _msg(lang: str, key: str, **kwargs) -> str:
    table = LANG_MESSAGES.get(lang, LANG_MESSAGES[DEFAULT_LANG])
    return table[key].format(**kwargs)


def marketplace_install_locations() -> dict:
    """name -> Path(installLocation) for every marketplace already
    configured on this machine. `apply` uses this for the fast path (the
    marketplace is already here); when it's missing here, `apply` falls
    back to the merged snapshot's own recorded source (see
    whitelist_marketplace) before giving up.
    """
    locations = {}
    for m in list_marketplaces():
        loc = m.get("installLocation")
        if m.get("name") and loc:
            locations[m["name"]] = Path(loc)
    return locations


def plugin_description(plugin_id: str, marketplaces: dict) -> Optional[str]:
    """Read the plugin's own author-written description from its
    marketplace's local manifest — never translated or rewritten here.
    Returns None if the marketplace or the description isn't available.
    """
    name, _, marketplace = plugin_id.partition("@")
    loc = marketplaces.get(marketplace)
    if not loc:
        return None
    manifest_path = loc / ".claude-plugin" / "marketplace.json"
    if not manifest_path.is_file():
        return None
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(manifest, dict):
        return None
    for entry in manifest.get("plugins", []):
        if isinstance(entry, dict) and entry.get("name") == name:
            return entry.get("description")
    return None


def load_merged(path: Path) -> dict:
    if not path.is_file():
        sys.exit(f"Error: merged snapshot file not found: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        sys.exit(f"Error reading {path}: {exc}")
    if not isinstance(data, dict) or data.get("kind") != "merged":
        sys.exit(f"Error: {path} is not a `merge --out` file (expected kind='merged')")
    return data


def missing_plugin_ids(merged: dict, installed_ids: set) -> list:
    return sorted(pid for pid in merged["plugins"] if pid not in installed_ids)


def addable_marketplace_source(record: dict) -> Optional[str]:
    """The string to hand `claude plugin marketplace add` for a merged
    view's recorded marketplace source record — the same two portable
    shapes whitelist_marketplace keeps. None means there's nothing
    portable on record (old-schema merged file, or the source machine
    added it from a local path) — `apply` must leave that plugin skipped.
    """
    if record.get("source") == "github" and record.get("repo"):
        return record["repo"]
    if record.get("source") == "git" and record.get("url"):
        return record["url"]
    return None


def classify_missing_plugins(missing: list, local_marketplaces: dict, merged_marketplaces: dict) -> tuple:
    """Three-way split, not two: a plugin whose marketplace isn't on this
    machine yet is only truly stuck (`skipped`) if the merged snapshot also
    has no portable source recorded for it. Otherwise it's `addable` —
    `apply -y` can add that marketplace itself first. Returns
    (installable, addable, addable_sources) where addable_sources maps
    marketplace name -> the source string to pass to `marketplace add`.
    """
    installable, addable, skipped = [], [], []
    addable_sources = {}
    for pid in missing:
        _, _, marketplace = pid.partition("@")
        if marketplace in local_marketplaces:
            installable.append(pid)
            continue
        record = merged_marketplaces.get(marketplace, {}).get("source_record", {})
        source = addable_marketplace_source(record)
        if source:
            addable.append(pid)
            addable_sources[marketplace] = source
        else:
            skipped.append(pid)
    return installable, addable, skipped, addable_sources


def cmd_apply(args) -> None:
    lang = args.lang or DEFAULT_LANG
    merged = load_merged(Path(args.merged_file))
    installed_ids = {p["id"] for p in list_plugins()}
    missing = missing_plugin_ids(merged, installed_ids)

    if not missing:
        print(_msg(lang, "nothing_missing"))
        return

    local_marketplaces = marketplace_install_locations()
    merged_marketplaces = merged.get("marketplaces", {})
    installable, addable, skipped, addable_sources = classify_missing_plugins(
        missing, local_marketplaces, merged_marketplaces
    )

    if installable:
        print(_msg(lang, "installable_header"))
        for pid in installable:
            where = ", ".join(sorted(merged["plugins"][pid]["present_on"]))
            print(f"  {pid}  (on: {where})")
            desc = plugin_description(pid, local_marketplaces)
            if desc:
                print(f"    {desc}")
    if addable:
        print(_msg(lang, "addable_header"))
        for pid in addable:
            _, _, marketplace = pid.partition("@")
            print(f"  {pid}  (adds marketplace '{marketplace}' from {addable_sources[marketplace]})")
    if skipped:
        print(_msg(lang, "skip_header"))
        for pid in skipped:
            print(f"  {pid}")

    selectable = installable + addable
    if not selectable:
        return

    if args.all:
        targets = selectable
    elif args.plugins:
        targets = [p for p in args.plugins if p in selectable]
        unknown = [p for p in args.plugins if p not in selectable]
        if unknown:
            sys.exit(f"Error: not in the installable list: {unknown}")
    else:
        print()
        answer = input(_msg(lang, "select_prompt")).strip().lower()
        if answer in ("a", "all"):
            targets = selectable
        elif answer in ("", "n", "none"):
            targets = []
        else:
            try:
                indices = [int(x.strip()) for x in answer.split(",") if x.strip()]
                targets = [selectable[i - 1] for i in indices]
            except (ValueError, IndexError):
                sys.exit("Error: could not parse selection")

    if not targets:
        return

    if not args.yes:
        print()
        print(_msg(lang, "dry_run_note"))
        return

    if not args.scope:
        sys.exit(_msg(lang, "scope_required"))

    # Cache each marketplace's add outcome (None = succeeded) — dedup so
    # two plugins from the same new marketplace only trigger one `add`.
    add_results = {}
    ok = fail = 0
    for pid in targets:
        _, _, marketplace = pid.partition("@")
        if marketplace in addable_sources:
            if marketplace not in add_results:
                source = addable_sources[marketplace]
                print(_msg(lang, "adding_marketplace", name=marketplace, source=source))
                code, out, err = run_claude(["plugin", "marketplace", "add", source, "--scope", args.scope])
                add_results[marketplace] = None if code == 0 else (out + err).strip()
            add_error = add_results[marketplace]
            if add_error is not None:
                fail += 1
                print(f"  {pid}: skipped — marketplace add failed: {add_error}")
                continue

        print(_msg(lang, "installing", id=pid, scope=args.scope))
        code, out, err = run_claude(["plugin", "install", pid, "-s", args.scope, "-y"])
        if code == 0:
            ok += 1
        else:
            fail += 1
            print(f"  {(out + err).strip()} (exit {code})")
    print(_msg(lang, "summary", ok=ok, fail=fail))
    if fail:
        sys.exit(1)


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

    upload = sub.add_parser("upload", help="Upload a snapshot file to a GitHub Gist")
    upload.add_argument("file", help="Path to the snapshot JSON file to upload (printed by `save`)")
    upload.add_argument(
        "--gist-id",
        help=f"Existing gist id to add to (default: ${ENV_GIST_ID} or the target dir's cached id; creates a new secret gist if none found)",
    )
    upload.add_argument(
        "--dir",
        help=f"Snapshot dir this gist is paired with, for caching the id (default: ${ENV_SNAPSHOT_DIR} or ./{DEFAULT_SNAPSHOT_DIR})",
    )

    fetch = sub.add_parser("fetch", help="Fetch snapshots from a GitHub Gist into the local snapshot dir")
    fetch.add_argument("--gist-id", help=f"Gist id to fetch from (default: ${ENV_GIST_ID})")
    fetch.add_argument(
        "--dir",
        help=f"Local snapshot dir to copy into (default: ${ENV_SNAPSHOT_DIR} or ./{DEFAULT_SNAPSHOT_DIR})",
    )

    sub.add_parser(
        "gist-list",
        help=f"List this tool's own gists (tagged '{SNAPSHOT_GIST_DESCRIPTION}') — find a --gist-id without leaving the terminal",
    )

    apply_ = sub.add_parser("apply", help="Install plugins missing on this machine from a merged snapshot")
    apply_.add_argument("merged_file", help="Path to a `merge --out` JSON file")
    apply_.add_argument("plugins", nargs="*", metavar="plugin", help="Specific plugin id(s) to install")
    apply_.add_argument("--all", action="store_true", help="Install everything missing")
    apply_.add_argument("-y", "--yes", action="store_true", help="Actually install (default is dry-run preview)")
    apply_.add_argument("--scope", choices=["user", "project", "local"], help="Install scope (required with -y)")
    apply_.add_argument("--lang", choices=["en", "zh"], help="Language for this tool's own prompts (default: en)")

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
    elif args.command == "upload":
        cmd_upload(args)
    elif args.command == "fetch":
        cmd_fetch(args)
    elif args.command == "gist-list":
        cmd_gist_list(args)
    elif args.command == "apply":
        cmd_apply(args)


if __name__ == "__main__":
    main()
