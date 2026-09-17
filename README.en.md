<div align="center">

# claude-plugin-skill-sync

**Sync your Claude Code plugins and skills across machines — one snapshot, same setup everywhere**

[![Python](https://img.shields.io/badge/python-3.8%2B-3776AB?logo=python&logoColor=white)](#requirements)
[![Dependencies](https://img.shields.io/badge/dependencies-zero-success)](#requirements)
[![Tests](https://img.shields.io/badge/tests-243%20passing-brightgreen)](#tests)
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
desensitize, diff across machines, and apply to
auto-install what's missing — no cloud account required, just local
JSON + an optional GitHub Gist transport. Plugins only — it never touches
anything outside `~/.claude`; MCP servers outside the plugin system are
surfaced (not managed) via [`doctor`](#commands).

## Highlights

| Feature | Description |
|---|---|
| **Zero dependencies** | Stdlib only, nothing to install |
| **Cross-machine sync** | `save` → `diff` → `apply` installs whatever's missing on a machine |
| **Desensitized snapshots** | `identity`/`machine` are required labels, **never a raw email or hostname** |
| **`apply` defaults to dry-run** | Preview only unless `-y`; `-y` also requires `--scope` |
| **Works on a fresh machine** | Snapshots record each plugin's marketplace source (GitHub repo / git url) too — `apply -y` runs `marketplace add` for you |
| **Never guesses installs** | Plugins with no source on record (e.g. a marketplace added from a local path) are listed and skipped, **not force-installed** |
| **243 tests** | All mocked — real plugins are never touched during testing |
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

> **The command names borrow git's vocabulary, but the semantics aren't
> identical**: `fetch` really does behave like `git fetch` — it pulls
> remote data down. But `diff` (its real name; `merge` is kept only as an
> alias) is **not** `git merge` — it only compares, never touches a file.
> Read-only, full stop. The step that actually "applies the diff" is
> `apply` — not `diff`, and not `update` either (`update` only upgrades
> plugins already installed on this machine to their own latest version;
> it has nothing to do with this sync system).

### Quick Reference

Don't want the long version — just want to know what to type? This is the
whole thing, per machine:

| Machine | Commands |
|---|---|
| **Source** (already has the plugins, wants another machine to match) | `save` → `upload` |
| **Destination** (wants to sync in) | `fetch` → `diff` (optional — just a look at what differs) → `apply` |

Keep reading for the full commands plus real output from each step.

### Full Steps (real captured output)

Below, the two machines are called **src** (already has the plugins) and
**dest** (wants them). Every code block right after a command is this
tool's actual output (not hand-written) — your own plugin names/counts
will differ, but the shape will match.

```bash
# On src: save a snapshot of this machine
python plugin_manager.py save --identity mosjin --machine src
```
```
Saved snapshot: snapshots/mosjin__src__linux__2026-09-17__demo0001.json
  30 plugin(s), 549 skill(s)
```

```bash
# On src: upload it to a GitHub Gist (first upload auto-creates a secret
# gist and caches its id in snapshots/.gist_id; uploading again from this
# machine later replaces its previous snapshot — pass --keep-history to
# keep every upload instead)
python plugin_manager.py upload snapshots/mosjin__src__linux__2026-09-17__demo0001.json
```
```
Created new gist (https://gist.github.com/<your-account>/<new-gist-id>)
  secret = unlisted, not private — anyone with the link can read it.

Cached in snapshots/.gist_id — future upload/fetch in this dir reuse it automatically.
```

```bash
# On dest: fetch what src uploaded — auto-discovered when this account
# has exactly one such gist, no id typing needed
python plugin_manager.py fetch --dir snapshots
```
```
Fetched gist <gist_id>: 1 snapshot(s) found, 1 new one(s) copied into snapshots
```

```bash
# On dest: compare both snapshots (read-only — installs nothing)
python plugin_manager.py diff --dir snapshots --out merged.json
```
```
Identity: mosjin
Machines: dest@linux, src@linux

Plugins:
  agentmemory@agentmemory: src@linux=1.0.0  [missing on: dest@linux]
  (1 more identical across all machines — pass --full to show)

Skills:
  (1 more identical across all machines — pass --full to show)

Merged view written to: merged.json
```
`[missing on: dest@linux]` is "src has it, dest doesn't" — that's what
`apply` actually installs. The 1 collapsed entry is identical on both
sides; `--full` shows everything.

```bash
# On dest: preview first — nothing installs without -y
python plugin_manager.py apply merged.json --all --scope user
```
```
Installable (marketplace already available here):
  agentmemory@agentmemory  (on: src@linux)
    Persistent memory for AI coding agents -- captures tool usage, compresses via LLM, injects context into future sessions

Dry run — pass -y to actually install. Nothing was installed.
```

```bash
# Looks right — install for real
python plugin_manager.py apply merged.json --all -y --scope user
```

Even if dest is a completely fresh install with zero marketplaces
configured, this still works: the snapshot recorded each plugin's
marketplace source (a GitHub repo or git url) too, so `apply -y` runs
`marketplace add` for you before installing. Only a marketplace whose
recorded source is a local path (not portable across machines) gets
skipped with a note to add it by hand; a plugin only ever seen on a
different platform (e.g. a Windows-only tool) is skipped too, by default —
pass `--include-other-platforms` to force it anyway.

> **Standalone skills aren't `apply`'s job**: a skill bundled inside a
> plugin installs automatically along with that plugin. A "standalone"
> skill — living directly under `~/.claude/skills/` or a project's
> `.claude/skills/`, not part of any plugin — is only recorded by name in
> a snapshot (for the `diff` view) and is **never** installed or
> transferred by `apply`. Sync those by hand for now.

Want to sync the other direction later (push what dest has back to src)?
`save` + `upload` on dest — `.gist_id` is already cached there, so it adds
the new snapshot to the same gist instead of creating a second one — then
`fetch` on src.

## Commands

| Command | Purpose |
|---|---|
| [`list`](#list) | List all installed plugins |
| [`update`](#update) | Update one/multiple/all plugins |
| [`doctor`](#doctor) | List standalone MCP servers this tool can't touch |
| [`uninstall` / `remove`](#uninstall--remove) | Remove plugins |
| [`save`](#save--diff--upload--fetch--apply) | Save a desensitized snapshot of this machine |
| [`diff` (alias `merge`)](#save--diff--upload--fetch--apply) | Compare snapshots from multiple machines into a drift view (read-only) |
| [`upload` / `fetch`](#save--diff--upload--fetch--apply) | Sync snapshots via GitHub Gist |
| [`gist-list`](#gist-list) | List gists this tool created — find an id without opening a browser |
| [`apply`](#save--diff--upload--fetch--apply) | Install whatever's missing on this machine per a snapshot |

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
Updating 2 plugins...

[1/2] ecc@synced... skipped
[2/2] leansvg@leansvg... done

────────────────────────────────────────
Results (version before → after):
  • ecc@synced  synced from your claude.ai account with no marketplace backing — manage it on claude.ai, or run `claude plugin disable` on this machine
  ─ leansvg@leansvg  0.1.14 (unchanged)

Updated: 0  Already current: 1  Skipped: 1  Failed: 0
```

Icons: `✔` updated · `─` already current · `•` skipped (e.g. a plugin
synced from your claude.ai account with no marketplace backing this
machine can manage) · `✗` failed

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

### save / diff / upload / fetch / apply

Save a desensitized snapshot of installed plugins and skills, compare
snapshots from multiple machines into one read-only drift view, and
(optionally) install whatever's missing on a given machine. For the full
walkthrough with real captured output, see
[Full Cross-Machine Sync Walkthrough](#full-cross-machine-sync-walkthrough) —
this section is just a flag reference.

```bash
# Save a snapshot of this machine (identity/machine are required labels,
# never a raw email or hostname — see --help for why)
python plugin_manager.py save --identity mosjin --machine work-laptop

# Default dir is ./snapshots (gitignored, local to this machine only).
# Point --dir at a private repo/cloud-sync folder you control to sync it
# across machines yourself, or use upload/fetch below instead.
python plugin_manager.py save --identity mosjin --machine work-laptop --dir /path/to/synced/dir

# Compare every snapshot in a directory into one per-machine drift view
# (read-only — installs nothing). `merge` is kept as an alias of the same
# command for anyone used to the old name.
python plugin_manager.py diff --dir /path/to/synced/dir

# By default only drifted/missing entries print — identical ones collapse
# into a one-line count. On a real machine with hundreds of skills this
# turns hundreds of lines into a handful. Pass --full to see everything.
python plugin_manager.py diff --dir /path/to/synced/dir --full

# Also write the diff view to a file (for `apply` later)
python plugin_manager.py diff --dir /path/to/synced/dir --out merged.json

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

# Install on this machine whatever the diff view shows is missing here.
# Dry-run by default; -y (plus --scope) actually installs. Four cases:
# 1) marketplace already here -> installs directly.
# 2) not here, but the snapshot recorded a GitHub repo / git url for it
#    -> -y runs `marketplace add` first, then installs.
# 3) neither (e.g. its only recorded source is a local path) -> listed
#    and skipped, never guessed at.
# 4) only ever seen on a different platform (e.g. Windows-only) -> skipped
#    by default; pass --include-other-platforms to force it anyway.
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

243 tests, all subprocess calls mocked — no real plugins are modified during testing.

## Cross-platform

Works on Windows, Linux, macOS. Uses `shutil.which` to locate `claude`/`claude.cmd`/`claude.exe`.

---

<div align="center">

[中文 README](README.md)

</div>
