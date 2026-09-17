"""Desensitized snapshot capture: identity/machine labels, plugin/skill/marketplace whitelisting, `save`."""
import os
import re
import secrets
import sys
import json
from datetime import datetime, timezone
from pathlib import Path

from . import claude_cli
from . import marketplaces as marketplaces_mod


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

    `marketplaces` is additive (not in _REQUIRED_SNAPSHOT_FIELDS) — a
    snapshot saved before this field existed must still load and merge.
    """
    skills = []
    for root, owner in discover_skill_roots(all_plugins):
        skills.extend(scan_skills(root, owner))
    marketplaces = [m for m in (marketplaces_mod.whitelist_marketplace(m) for m in marketplaces_mod.list_marketplaces()) if m]
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


def _snapshot_machine_prefix(snapshot: dict) -> str:
    """The filename prefix every save for one machine shares — reused by
    snapshot_filename() below and by `upload` to find that machine's own
    older snapshot files sitting in a shared gist (see
    find_stale_gist_snapshots), so uploads don't accumulate one file
    forever per save.
    """
    return f"{snapshot['identity']}__{snapshot['machine']}__{snapshot['platform']}__"


def snapshot_filename(snapshot: dict) -> str:
    """identity/machine are already sanitized by resolve_identity/
    resolve_machine — reused as-is so the filename and the JSON body never
    disagree. A random token (not a precise timestamp) makes same-day
    filenames from the same machine unique without fingerprinting the
    time of day `captured_at` was deliberately coarsened to hide.
    """
    token = secrets.token_hex(4)
    return f"{_snapshot_machine_prefix(snapshot)}{snapshot['captured_at']}__{token}.json"


def cmd_save(args) -> None:
    identity = resolve_identity(args)
    machine = resolve_machine(args)
    all_plugins = claude_cli.list_plugins()
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


