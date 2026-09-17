<div align="center">

# claude-plugin-skill-sync

**Sync your Claude Code plugins and skills across machines — one snapshot, same setup everywhere**

[![Python](https://img.shields.io/badge/python-3.8%2B-3776AB?logo=python&logoColor=white)](#requirements)
[![Dependencies](https://img.shields.io/badge/dependencies-zero-success)](#requirements)
[![Tests](https://img.shields.io/badge/tests-222%20passing-brightgreen)](#tests)
[![Platform](https://img.shields.io/badge/platform-windows%20%7C%20linux%20%7C%20macos-lightgrey)](#cross-platform)

[中文](README.md)

</div>

---

## Table of Contents

- [About](#about)
- [Highlights](#highlights)
- [Quick Start](#quick-start-beginner-friendly)
- [Full Cross-Machine Sync Walkthrough](#full-cross-machine-sync-walkthrough)
- [Commands](#commands)
- [Requirements](#requirements)
- [bootstrap_tools.py](#bootstrap_toolspy)
- [Tests](#tests)
- [Cross-platform](#cross-platform)

---

## About

A cross-platform Python CLI for managing Claude Code plugins and syncing
installed plugins/skills across machines.

> Anyone running Claude Code on more than one machine ends up manually
> tracking which plugins are installed and whether versions line up.

This tool turns "what's installed" into a snapshot you can save,
desensitize, merge for a drift view across machines, and apply to
auto-install what's missing — no cloud account required, just local
JSON + an optional GitHub Gist transport. Plugins only — it never touches
anything outside `~/.claude`; MCP servers outside the plugin system are
surfaced (not managed) via [`doctor`](#commands).

## Highlights

| Feature | Description |
|---|---|
| **Zero dependencies** | Stdlib only, nothing to install |
| **Cross-machine sync** | `save` → `merge` → `apply` installs whatever's missing on a machine |
| **Desensitized snapshots** | `identity`/`machine` are required labels, **never a raw email or hostname** |
| **`apply` defaults to dry-run** | Preview only unless `-y`; `-y` also requires `--scope` |
| **Works on a fresh machine** | Snapshots record each plugin's marketplace source (GitHub repo / git url) too — `apply -y` runs `marketplace add` for you |
| **Never guesses installs** | Plugins with no source on record (e.g. a marketplace added from a local path) are listed and skipped, **not force-installed** |
| **222 tests** | All mocked — real plugins are never touched during testing |
| **Modular internals** | Implementation lives in the `plugin_sync/` package, split by domain (claude_cli/snapshots/marketplaces/merge/gist/apply) — `plugin_manager.py` is just the entry-point shim |

## Quick Start (beginner-friendly)

Nothing to install — clone and run:

```bash
git clone <this-repo-url>
cd claude-plugin-skill-sync

# Step 1: see what's installed on this machine
python plugin_manager.py list

# Step 2: update everything in one go
python plugin_manager.py update --all
```

Only using one machine? That's it. Want to sync plugins across machines? See the full walkthrough below.

## Full Cross-Machine Sync Walkthrough

Typical scenario: machine A has a pile of plugins installed, and you want
machine B to end up with the same set. Only the first `fetch` might need an
extra id typed in — every other step is copy-paste.

```bash
# ① Machine A — save a snapshot of this machine
python plugin_manager.py save --identity mosjin --machine work-laptop

# ② Machine A — upload it to a GitHub Gist
#    First upload auto-creates a secret gist, caches its id in snapshots/.gist_id.
#    Uploading again from this machine later replaces its previous snapshot
#    in the gist by default (no unbounded pile-up); pass --keep-history to
#    keep every upload instead.
python plugin_manager.py upload snapshots/<file>.json
```

```bash
# ③ Machine B — save its own snapshot too (optional, so the drift view
#    below also shows what machine B already has)
python plugin_manager.py save --identity mosjin --machine home-pc

# ④ Machine B — fetch machine A's snapshot
#    This machine has never fetched/uploaded before, so there's no cached
#    id — but as long as this GitHub account has exactly one gist this
#    tool created, fetch finds and caches it automatically. No id typing:
python plugin_manager.py fetch

# More than one such gist on this account (synced more than one set before)?
# Check first, then be explicit:
python plugin_manager.py gist-list
python plugin_manager.py fetch --gist-id <id>

# ⑤ Machine B — merge both snapshots into a drift view
python plugin_manager.py merge --dir snapshots --out merged.json

# ⑥ Machine B — preview what would install, then actually install with -y
python plugin_manager.py apply merged.json --all --scope user
python plugin_manager.py apply merged.json --all -y --scope user

# ⑦ Machine B — bring everything (including what was just installed) up to date
python plugin_manager.py update --all
```

Even if machine B is a completely fresh install with zero marketplaces
configured, this still works: the snapshot recorded each plugin's
marketplace source (a GitHub repo or git url) too, so `apply -y` runs
`marketplace add` for you before installing. Only a marketplace whose
recorded source is a local path (not portable across machines) gets
skipped with a note to add it by hand.

> **Standalone skills aren't `apply`'s job**: a skill bundled inside a
> plugin installs automatically along with that plugin. A "standalone"
> skill — living directly under `~/.claude/skills/` or a project's
> `.claude/skills/`, not part of any plugin — is only recorded by name in
> a snapshot (for the `merge` drift view) and is **never** installed or
> transferred by `apply`. Sync those by hand for now.

Want to sync the other direction later (push what machine B has back to
machine A)? `save` + `upload` on machine B — `.gist_id` is already cached
there, so it adds the new snapshot to the same gist instead of creating a
second one — then `fetch` on machine A.

## Commands

| Command | Purpose |
|---|---|
| [`list`](#list) | List all installed plugins |
| [`update`](#update) | Update one/multiple/all plugins |
| [`doctor`](#doctor) | List standalone MCP servers this tool can't touch |
| [`uninstall` / `remove`](#uninstall--remove) | Remove plugins |
| [`save`](#save--merge--upload--fetch--apply) | Save a desensitized snapshot of this machine |
| [`merge`](#save--merge--upload--fetch--apply) | Merge snapshots from multiple machines into a drift view |
| [`upload` / `fetch`](#save--merge--upload--fetch--apply) | Sync snapshots via GitHub Gist |
| [`gist-list`](#gist-list) | List gists this tool created — find an id without opening a browser |
| [`apply`](#save--merge--upload--fetch--apply) | Install whatever's missing on this machine per a snapshot |

### list

Show all installed plugins in a compact table.

```bash
python plugin_manager.py list
```

```
Plugin                     Source                    Version        Scope    Status
───────────────────────────────────────────────────────────────────────────────────
caveman                    caveman                   655b7d9c5431   user     ✔
ecc                        ecc                       2.0.0-rc.1     user     ✔
context7                   claude-plugins-official   cda114029ef8   user     ✗
...

27 plugins installed
```

### update

Update one, multiple, or all plugins. Partial plugin names are accepted (no need to type `caveman@caveman`).

```bash
# Single plugin
python plugin_manager.py update caveman

# Multiple plugins
python plugin_manager.py update caveman ecc eduforge

# All plugins (sequential)
python plugin_manager.py update --all

# All plugins (parallel — faster for many plugins)
python plugin_manager.py update --all --parallel
```

```
Updating 3 plugins...

[1/3] caveman@caveman... ✔
[2/3] ecc@ecc... ─
[3/3] eduforge@eduforge... ✔

────────────────────────────────────────
Updated: 2  Already current: 1  Failed: 0
```

Icons: `✔` updated · `─` already current · `✗` failed

Status is resolved by comparing version strings before and after, not by
guessing at CLI output text — it detects a version change, not arbitrary
content change.

> **Note**: If anything updated, restart Claude Code — the old MCP
> servers/skills keep running in the current session until then.

### doctor

Some MCP servers are registered directly with `claude mcp add` — not
bundled in any plugin, so `update` can't touch them. `doctor` lists them
so they don't go silently unmanaged.

```bash
python plugin_manager.py doctor
```

```
Standalone MCP servers (not bundled in any plugin):

  firecrawl: npx -y firecrawl-mcp  [✔ Connected]

1 standalone server found.
`claude mcp` has no update subcommand — refresh these via their own
package manager (npm/uv/pip), not this tool.
```

### uninstall / remove

Remove one or more plugins. Prompts for confirmation unless `-y` is passed.

```bash
# Single plugin (with confirmation prompt)
python plugin_manager.py uninstall caveman

# Skip prompt
python plugin_manager.py uninstall caveman -y

# Multiple plugins
python plugin_manager.py uninstall caveman ecc -y

# Preserve plugin data directory
python plugin_manager.py uninstall caveman -y --keep-data

# Remove unused auto-installed dependencies
python plugin_manager.py uninstall caveman -y --prune

# 'remove' is an alias
python plugin_manager.py remove caveman -y
```

### save / merge / upload / fetch / apply

Save a desensitized snapshot of installed plugins and skills, merge
snapshots from multiple machines into one drift view, and (optionally)
install whatever's missing on a given machine.

```bash
# Save a snapshot of this machine (identity/machine are required labels,
# never a raw email or hostname — see --help for why)
python plugin_manager.py save --identity mosjin --machine work-laptop

# Default dir is ./snapshots (gitignored, local to this machine only).
# Point --dir at a private repo/cloud-sync folder you control to sync it
# across machines yourself, or use upload/fetch below instead.
python plugin_manager.py save --identity mosjin --machine work-laptop --dir /path/to/synced/dir

# Merge every snapshot in a directory into one per-machine drift view
python plugin_manager.py merge --dir /path/to/synced/dir

# Also write the merged view to a file (for `apply` later)
python plugin_manager.py merge --dir /path/to/synced/dir --out merged.json

# Sync via a GitHub Gist instead of your own transport (reuses `gh` auth).
# First upload creates a secret gist and caches its id next to the
# snapshot dir; later uploads/fetches in that dir reuse it automatically.
# No cache, no --gist-id: exactly one gist this tool created on the account
# is used automatically; more than one is an error pointing at gist-list.
# Uploading again from the same machine replaces its previous snapshot in
# the gist by default — no unbounded pile-up. Keep every upload instead:
python plugin_manager.py upload snapshots/<file>.json
python plugin_manager.py upload snapshots/<file>.json --keep-history
python plugin_manager.py fetch --gist-id <id>

# Install on this machine whatever the merged view shows is missing here.
# Dry-run by default; -y (plus --scope) actually installs. Three cases:
# 1) marketplace already here -> installs directly.
# 2) not here, but the snapshot recorded a GitHub repo / git url for it
#    -> -y runs `marketplace add` first, then installs.
# 3) neither (e.g. its only recorded source is a local path) -> listed
#    and skipped, never guessed at.
python plugin_manager.py apply merged.json --all -y --scope user
python plugin_manager.py apply merged.json --lang zh   # Chinese prompts
```

### gist-list

List the gists this tool created (filtered by the tag `upload` stamps on
them) — no need to open a browser to find an id.

```bash
python plugin_manager.py gist-list
```

```
Gist ID                           Visibility  Files      Updated
──────────────────────────────────────────────────────────────────
1cd6200ecc8a99f2cf03d59954bda507  secret      1 file     2026-09-17T00:34:47Z

1 gist found.
Use one with: fetch --gist-id <id>  (or upload --gist-id <id>)
```

## Requirements

- Python 3.8+
- `claude` CLI in PATH (Claude Code)

No external dependencies — stdlib only.

## bootstrap_tools.py

Separate script: installs the standalone CLI binaries (`rtk`, `gh-asset`,
`node`/`uv`) that `~/.claude` hooks/rules assume are on PATH — not Claude
plugins. No sudo required; everything installs under `~/.local`.

```bash
python bootstrap_tools.py          # install what's missing
python bootstrap_tools.py --check  # report status only, install nothing
```

`sqz` is a known gap — see the script's `KNOWN_UNAVAILABLE` note for why.

## Tests

```bash
python -m pytest tests/ -v
```

222 tests, all subprocess calls mocked — no real plugins are modified during testing.

## Cross-platform

Works on Windows, Linux, macOS. Uses `shutil.which` to locate `claude`/`claude.cmd`/`claude.exe`.

---

<div align="center">

[中文 README](README.md)

</div>
