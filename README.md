# claude-plugin-skill-sync

Cross-platform Python CLI to manage Claude Code plugins and sync
installed plugins/skills across machines.

## Requirements

- Python 3.8+
- `claude` CLI in PATH (Claude Code)

No external dependencies — stdlib only.

## bootstrap_tools.py

Separate script, separate concern: installs the standalone CLI binaries
(`rtk`, `gh-asset`, plus the `node`/`uv` runtimes several hooks and MCP
servers need) that `~/.claude`'s hooks and CLAUDE.md rules assume are on
PATH. These are not Claude plugins — `plugin_manager.py` above only ever
talks to `claude plugin ...`. No sudo required; everything installs under
`~/.local`.

```bash
python bootstrap_tools.py          # install what's missing
python bootstrap_tools.py --check  # report status only, install nothing
```

Written after migrating dev from Windows to Ubuntu (2026-09-03) surfaced
that none of these were present on a fresh machine. `sqz` is a known gap —
see the script's `KNOWN_UNAVAILABLE` note for why.

## Commands

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

Status is resolved by comparing the plugin's version string before and
after (via a second `claude plugin list --json` once all updates finish),
not by guessing at the CLI's stdout wording — a `0` exit code with an
unchanged version string now correctly reads as "current" instead of
"updated". This detects a changed version string, not arbitrary content
changes — a marketplace reinstalling identical content under the same
version still reads as "current". If anything did update, a note reminds
you to restart Claude Code: an updated plugin's bundled MCP servers/skills
keep running the old code in the current session until then.

### doctor

Some MCP servers aren't bundled inside any plugin — they were registered
directly with `claude mcp add`. `claude mcp` has no `update` subcommand, so
`plugin_manager.py update` cannot touch them; they must be refreshed through
their own package manager (npm/uv/pip). `doctor` lists exactly those, so
they don't go silently unmanaged.

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
python plugin_manager.py upload snapshots/<file>.json
python plugin_manager.py fetch --gist-id <id>

# Install on this machine whatever the merged view shows is missing here.
# Dry-run by default; -y (plus --scope) actually installs. Only plugins
# whose marketplace is already added on this machine are installable —
# others are listed and skipped, never guessed at.
python plugin_manager.py apply merged.json --all -y --scope user
python plugin_manager.py apply merged.json --lang zh   # 中文提示
```

## Tests

```bash
python -m pytest tests/ -v
```

170 tests, all subprocess calls mocked — no real plugins are modified during testing.

## Cross-platform

Works on Windows, Linux, macOS. Uses `shutil.which` to locate `claude`/`claude.cmd`/`claude.exe`.
