# Changelog

## [unreleased] 2026-09-17

### 📐 Refactor
- Repo renamed `claude_plugin_updater` → `claude-plugin-skill-sync` to reflect actual scope (plugin management + cross-machine plugin/skill sync, not just "updating"). Old GitHub URL still redirects. Local project folder intentionally left unrenamed — see LESSONS.md.
- README split into `README.md` (Chinese, default) and `README.en.md` (English), cross-linked — no more single file mixing both languages.
- `plugin_manager.py` (1300+ lines, one file) split into a `plugin_sync/` package by domain (`claude_cli`, `snapshots`, `marketplaces`, `merge`, `gist`, `apply`, `cli`) — zero behavior change, `python plugin_manager.py <command>` unchanged (now a 20-line shim). Every cross-module call goes through a qualified module reference, never a bare `from .x import y`, so `unittest.mock.patch(...)` keeps intercepting by the function's real owning module. (#12)

### 🚀 Feature
- `gist-list`: list gists this tool created (tagged `claude-plugin-skill-sync snapshots`) without opening a browser. `resolve_gist_id` now auto-discovers the shared gist when there's no `--gist-id`, no env var, and no cached marker — exactly one match is used and cached automatically, two or more is an error pointing at `gist-list`. Removes the manual "copy the id from machine A, paste on machine B" step for the common single-shared-gist case. (#9)
- README: full step-by-step two-machine save → upload → fetch → merge → apply → update walkthrough. (#9)
- `save` now also captures each configured marketplace's portable source (`repo` for a GitHub-sourced marketplace, `url` for a git one — never the local `installLocation`), and `merge` carries it into the merged view. `apply -y` uses it to run `claude plugin marketplace add` automatically for a plugin whose marketplace isn't on this machine yet, before installing — so a snapshot can actually bootstrap a brand-new machine with zero marketplaces configured, not just one that already has every source added. A marketplace whose only recorded source is a local path (not portable) still gets skipped with an explanation. (#10)
- `upload` now overwrites this machine's previous snapshot in the shared gist by default (adds the new file, then removes any older file matching this machine's identity/machine/platform prefix) instead of accumulating one file per save forever. `--keep-history` opts back into the old append-only behavior. Add-before-remove is required, not a style choice — GitHub rejects editing a gist down to zero files, verified live. (#11)
- `doctor`: lists MCP servers registered standalone via `claude mcp add` (not bundled in any plugin, so `update` structurally can't touch them — `claude mcp` itself has no `update` subcommand).
- `diff` (renamed from `merge`, which is now gone entirely — see Refactor below) defaults to compact output: plugins/skills identical across every machine collapse into a one-line count instead of printing every entry. `--full` restores the old always-show-everything behavior. Only kicks in with 2+ machines to compare. Real two-machine save went from 585 printed lines to 8 when the machines were near-identical. (#13)
- `apply` now skips (by default) a missing plugin that has only ever been seen installed on a platform other than this machine's (e.g. a Windows-only tool, when running on Linux) — derived from each plugin's `platforms` field in the diff view, itself derived from every merged snapshot's `@platform`-suffixed machine key. `--include-other-platforms` overrides per run. Heuristic, not a manifest fact: neither `claude plugin list --json` nor any plugin's own manifest declares an OS restriction (checked live against every installed plugin.json/SKILL.md — no such field exists anywhere), and the signal is recomputed fresh from current data every run, so it self-corrects the moment any machine on that platform actually installs the plugin and saves a snapshot.

### 🐛 Fix
- `update` / `uninstall`: plugins with `scope: "synced"` (pulled from a claude.ai account, no local marketplace backing) are now skipped with a clear message instead of failing with a generic "Invalid scope" error — see VERIFIED_FACTS.md.
- `update`: status (updated/current/failed) is now resolved from a real before/after version-string diff, not a regex guess against the CLI's stdout wording — a `0` exit code with unchanged version now correctly reads as "current" instead of "updated". Results now print each plugin's real version transition (`before → after`, or `(unchanged)`), not just an icon.
- `gist-list --public`/`--secret` crashed with "unrecognized arguments" (never wired into argparse), and the bare command crashed with `unknown flag: --filter` — `gh gist list` has no server-side description filter, only `-L`/`--public`/`--secret`; a prior `--filter <description>` flag was never real and had never been exercised against the real CLI. Filtering by this tool's own snapshot-gist tag now happens client-side on the parsed output, which also fixes a second bug the dead flag was masking: with no client-side filter, an account with unrelated gists would have had them misidentified as this tool's own.

### 📐 Refactor (continued)
- `merge` command deleted entirely (was briefly kept as a `diff` alias after the rename below, then removed on request) — `plugin_manager.py merge` now errors with `invalid choice`. Renamed cascaded through the implementation, not just the CLI: `plugin_sync/merge.py` → `plugin_sync/diff.py`, `cmd_merge`/`merge_snapshots`/`_print_merge_table` → their `diff`-prefixed names, every test reference updated to match. `"kind": "merged"` and similarly-named JSON/data fields were left alone — they describe the snapshot schema, not the command, and changing them would break old `--out` files.
- README rewritten again after live usage feedback: command examples and their real output now share one fenced code block (`$`-prefixed command line, output below) instead of two adjacent blocks that read as two separate commands; a "Quick Reference" table (functionally-minimal step sequence per machine role) now precedes the detailed walkthrough; Requirements/Cross-platform moved to the very top, ahead of the project description; duplicate bottom-of-file language-switch links removed (the top one is enough); corrected a claim that `diff` "can be skipped" — `apply`'s first argument is a `diff --out` file, so the command itself is required, only reading its printed table is optional.

### Tests
243 tests (up from 170), all subprocess calls mocked.

## [unreleased] 2026-09-16

### 🚀 Feature
- `save` / `merge`: capture a desensitized snapshot of installed plugins + skills, merge snapshots from multiple machines into a per-machine drift view (version/scope/enabled differences, what's missing where). (#5)
- `upload` / `fetch`: sync snapshots across machines via a GitHub Gist, reusing existing `gh` auth. (#7)
- `apply`: install onto this machine whatever a merged snapshot shows is missing here — dry-run by default, `-y --scope <user|project|local>` to execute, `--lang en|zh` for the tool's own prompts. (#8)

### 🐛 Fix
- `update` / `uninstall`: pass the plugin's own recorded scope (`-s user|project|local`) instead of relying on the CLI's `user` default — previously any local/project-scope plugin failed both commands. (#6)

### Tests
170 tests (up from 36), all subprocess calls mocked.
