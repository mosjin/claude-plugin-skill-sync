# Tech Log

## 2026-09-17: synced-scope skip, gist auto-discovery, marketplace-source capture, upload overwrite (Issues #9, #10, #11)

### Problem
Four separate gaps surfaced by actually running the tool and by a user walking through the real two-machine workflow: (1) `update`/`uninstall` crashed on `scope: "synced"` plugins with a useless generic error; (2) a second machine had no way to find the shared gist id without opening github.com by hand; (3) `apply` on a genuinely fresh machine (zero marketplaces configured) could install nothing, because a snapshot never recorded where a marketplace's plugins actually come from; (4) `upload` accumulated one file per save forever in the shared gist, with no supported cleanup.

### Root Cause
1. `claude plugin list --json` can report `scope: "synced"` for a plugin pulled from a claude.ai account with no local marketplace backing — not one of the `-s` flag's accepted values (`user`/`project`/`local`/`managed`). `update_one`/`uninstall_one` passed it straight through.
2. `resolve_gist_id()` only checked `--gist-id`, the env var, and a per-dir cache — never the account's own gists.
3. `whitelist_plugin()`/`capture_snapshot()` never recorded a marketplace's `repo`/`url`, only the plugin's `id` (whose `@marketplace` segment is a local name, not a source).
4. `snapshot_filename()` deliberately randomizes (so same-day *local* saves don't collide), and `upload` always called `gh gist edit --add`, which only adds — the two behaviors combined meant every upload from one machine landed as a new, never-cleaned-up gist file.

### Fix
- `is_cli_manageable()` + a new `skipped` status: `update`/`uninstall` recognize any scope outside `{user, project, local, managed}` up front and skip the CLI call entirely, surfacing the CLI's own (better) explanation instead of a generic "Invalid scope" error.
- `list_snapshot_gists()` (filtered by a fixed `SNAPSHOT_GIST_DESCRIPTION` tag) backs both a new `gist-list` command and `resolve_gist_id`'s auto-discovery: exactly one match is used and cached automatically; two or more is an error pointing at `gist-list` instead of `upload` silently forking a second gist.
- `whitelist_marketplace()` captures each marketplace's portable source (`repo` for `source: "github"`, `url` for `source: "git"` — never the local `installLocation`); `merge` carries it into the merged view; `apply -y` now does a three-way split (installable / addable / skipped) and runs `claude plugin marketplace add` itself for the `addable` case before installing.
- `find_stale_gist_snapshots()` matches a gist's existing files against the uploading machine's own identity/machine/platform prefix; `upload` now adds the new file **then** removes the stale match(es) — never the reverse, since a live repro showed GitHub rejects editing a gist down to zero files, and `gh gist edit` itself refuses `--add`+`--remove` in one call. `--keep-history` opts back into pure accumulation.

### Lessons
- **Rule**: when a CLI's enum-like field (`scope`, here) has a value your code doesn't recognize, don't force it through the same code path as the known values and let the CLI's own validation produce a confusing error — check membership first and handle the unknown case explicitly, with the CLI's actual explanation surfaced (obtained by testing the omitted/default case live, not guessed).
- **Why**: `claude plugin update -s synced` and `claude plugin update` (no `-s`, defaults to `user`) produce completely different error text for the exact same plugin — only live testing both surfaced the CLI's real, more useful message instead of the generic one this tool was echoing.
- **Rule**: before assuming two GitHub CLI mutations can be combined or reordered freely, test the actual sequence against a real gist — API-level invariants (a gist can never have zero files) and CLI-level flag restrictions (`--add`/`--remove` mutually exclusive) aren't discoverable from `--help` text alone.
- **Why**: the remove-then-add order (the "obvious" one) failed twice in two different ways in this session — first with a generic-looking "Invalid scope" style HTTP 422, second with a CLI argument-parsing error — before add-then-remove was confirmed live to work.

## 2026-09-16: Snapshot save/merge/upload/apply pipeline (Issues #5, #6, #7, #8)

### Problem
No way to inventory installed Claude Code plugins/skills, compare across machines, or reinstall a missing set. `update`/`uninstall` also silently defaulted to `-s user`, failing on any local/project-scope plugin.

### Root Cause
`plugin_manager.py` only ever wrapped live `claude plugin list/update/uninstall` — nothing persisted state to disk. Separately, `update_one`/`uninstall_one` never read the plugin's own recorded `scope` field, so `claude plugin update/uninstall` fell back to its own `-s user` default and rejected `local`/`project`-scope plugins outright.

### Fix
- `save`: whitelist-built (not collect-then-strip) desensitized snapshot — plugin id/version/scope/enabled/mcp-server-names, skill name/owner. Identity/machine are required labels, sanitized, full-email rejected outright.
- `merge`: reads a directory of snapshots, picks exactly one (most recent) snapshot per machine wholesale — never unions two of the same machine's snapshots field-by-field, so an uninstalled plugin/deleted skill from a stale save can't linger forever.
- `upload`/`fetch`: sync via a GitHub Gist (`run_gh()` mirrors the existing `run_claude()` subprocess seam); gist id cached in the snapshot dir's `.gist_id` marker so it's not re-typed every call.
- `apply`: installs what's missing on this machine from a merged snapshot; dry-run by default, `--scope` required to actually install (never copied from the source machine — project scope is directory-bound).
- `update_one`/`uninstall_one`: now pass `-s <plugin's own scope>` instead of relying on the CLI's `user` default.

### Lessons
- **Rule**: build a desensitization schema as an explicit field allowlist constructed from scratch, never "collect the raw dict then delete sensitive keys" — the latter lets any newly-added upstream field (e.g. `mcpServers[].args` holding an absolute path) leak silently.
- **Why**: a two-round independent code review (opus) caught exactly this class of bug plus several more (identity/machine stored raw despite being "sanitized" in the filename only, `merge`'s "most recent wins" rule applied to only one of three data structures, `gh gist create`'s URL parsed with zero validation). All were real, all were fixed, all now have regression tests that assert the actual leaked *value*, not just an absent key name.
