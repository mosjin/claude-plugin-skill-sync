"""Marketplace source capture and the installable/addable/skipped split `apply` uses."""
import json
import sys
from pathlib import Path
from typing import Optional

from . import claude_cli


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
    code, out, err = claude_cli.run_claude(["plugin", "marketplace", "list", "--json"])
    if code != 0:
        sys.exit(f"Error listing marketplaces: {err.strip()}")
    marketplaces = json.loads(out)
    if not isinstance(marketplaces, list):
        sys.exit(f"Error: unexpected response from claude (expected list, got {type(marketplaces).__name__})")
    return marketplaces


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


