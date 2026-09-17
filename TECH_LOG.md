# Tech Log

## 2026-09-17: README rewrite exposed a self-updating plugin, a dead flag, and repeated wrong claims about what's optional

### Problem
A user support conversation about `plugin_manager.py`'s scope ("can it also cover skills/MCP?") led into fixing `update`'s status detection, then a real `gist-list --public`/`--secret` crash (`unknown flag: --filter`), then a full README rewrite — during which three separate wrong claims were made and had to be corrected live by the user, each traceable to a specific reasoning shortcut.

### Root Cause
1. `update_one` classified success/failure by regexing `claude plugin update`'s stdout text (`"already"`/`"up to date"`) instead of comparing the plugin's version string before and after — a `0` exit code with unchanged wording could misreport as "updated".
2. `list_snapshot_gists()` called `gh gist list --filter <description>` — `--filter` was never a real flag on that subcommand (verified live against `gh gist list --help`: only `-L`/`--public`/`--secret` exist). The mocked test asserted the same wrong flag the implementation sent, so it "passed" without ever exercising the real CLI — nothing caught this until a user ran it for real.
3. Writing the cross-machine walkthrough, "`diff` can be skipped" was asserted twice in slightly different forms — first as "this step is optional", then (after a partial correction) left uncorrected in a second location (the Quick Reference table) while the walkthrough prose was fixed. Both times, the actual constraint (`apply`'s first CLI argument is a `diff --out` file — the command cannot be skipped, only *reading its printed output* can be) was sitting right there in the example's own `--out merged.json` flag, unnoticed until the user pointed at it directly in a screenshot.

### Fix
- `update_one` now returns raw `{id, code, message}`; `resolve_update_status(before, after, code)` is a pure function comparing real version strings, tested directly with before/after fixtures instead of stdout-wording fixtures.
- `list_snapshot_gists(visibility=None)` sends only real `gh gist list` flags (`-L`, plus `--public`/`--secret` when requested) and filters by `SNAPSHOT_GIST_DESCRIPTION` **client-side** on the parsed output — fixing a second, quieter bug the dead `--filter` was masking: with no client-side filter, an account with unrelated gists would have had them misidentified as this tool's own snapshots.
- Every "optional" claim in the README was traced to its actual CLI/argparse constraint before being asserted, and every place the same claim appears (Quick Reference table, walkthrough step heading, prose) was greped for and updated together — not just the first place noticed.

### Lessons
- **Rule**: a mocked test that asserts the implementation sent flag X is not evidence flag X is real — it only proves the test and the implementation agree with each other. Verify a wrapped CLI's actual flags against its own `--help` (or docs) independent of what the code currently sends, especially for any flag introduced without ever running the real subprocess.
- **Why**: this is exactly how `--filter` shipped and stayed broken through however many commits — nothing ever ran `gh gist list` for real until a live user did.
- **Rule**: before writing "X is optional" or "X can be skipped" in user-facing docs, find the specific code that would make it false if it weren't — a required CLI argument, a non-nullable field, a hard-coded check — and cite it. If no such thing is found, the claim is unverified, not optional.
- **Why**: "optional" is a strong claim about a hard constraint (does the program accept the input's absence), not a style preference — asserting it without checking is indistinguishable, to a reader, from asserting it after checking, until the reader hits the wall the writer didn't.
- **Rule**: correcting a wrong claim only where the user pointed means the same claim can still be wrong two paragraphs or one table away. Grep for every occurrence of the corrected concept in the document, not just the flagged instance.
- **Why**: happened twice in one session on the same underlying claim (`diff` optionality) — the second occurrence survived the first fix specifically because the fix was scoped to "the sentence the user quoted" instead of "every sentence making this claim".

## 2026-09-17: modularize plugin_manager.py into plugin_sync/ (PR #12)

### Problem
`plugin_manager.py` had grown past 1300 lines as a single flat file across four feature cycles in one day — user's standing design principles (CLAUDE.md rule 8: modularity, single-definition, high cohesion/low coupling, maintainability) called for splitting it before it grew further.

### Root Cause
Not a bug — a proactive refactor. The risk was entirely self-inflicted: 221 existing tests patch dozens of names by the string `"plugin_manager.<name>"`, and `unittest.mock.patch()` only intercepts a call site that looks up the patched name *on the module object at call time* — a `from .module import name` import binds a private copy into the importing module's namespace that `patch("plugin_sync.module.name")` cannot touch, silently producing tests that pass while mocking nothing (catastrophic here, since `run_claude`/`run_gh` mocks are the only thing stopping tests from hitting real plugins/gists).

### Fix
- Split into `plugin_sync/{claude_cli,snapshots,marketplaces,merge,gist,apply,cli}.py` by domain, using `ast` to extract each function/constant's exact source rather than retyping.
- Every cross-module call is qualified through an imported module object (`from . import claude_cli` then `claude_cli.run_claude(...)`), never a bare `from .x import y` — this is what keeps every existing `patch(...)` target valid after the move.
- `plugin_manager.py` reduced to a 20-line shim (`from plugin_sync.cli import main`) — `python plugin_manager.py <command>` is byte-for-byte unchanged, so zero README lines needed to change.
- Found and fixed two real bugs introduced *during* the move (not present before): `merge.py` and `snapshots.py` each had a local variable shadowing an imported module name (`snapshots`, `marketplaces`) — caught immediately by the test suite (`UnboundLocalError`), fixed by renaming the import aliases.
- Verified with more than a green test suite: deliberately broke `run_claude`/`run_gh` (`raise RuntimeError`) and confirmed every test that's supposed to mock them still passed, then reverted — proof the patch targets actually intercept, not just that nothing crashed. Also ran a live `--help` sweep across all 11 subcommands plus real `list`/`gist-list` calls, since 100%-mocked unit tests can't catch a broken argparse wire-up or missing import.
- Did not force a class onto the `cmd_*` handlers — the per-handler `lang`/`scope`/`snapshot_dir`/`gist_id` resolution duplication was only 1-2 lines each, not enough to justify a config-object abstraction over otherwise-independent pure functions (`merge_snapshots`, `whitelist_plugin`, `classify_missing_plugins`, etc. stayed plain functions).

### Lessons
- **Rule**: when moving code between modules in a codebase with heavy `unittest.mock.patch("module.name")` usage, always import the *module* and call through it (`module.name(...)`), never `from module import name` — the latter breaks every patch target for that name silently (tests keep passing, they just stop testing anything).
- **Why**: this is exactly the Style-A-vs-Style-B distinction that determined whether this refactor was a clean mechanical move or a landmine of silently-neutered tests — decided *before* moving a single function, per a second-opinion review, rather than discovered after the fact.
- **Rule**: "pytest is green" is necessary but not sufficient evidence a mock-heavy refactor is safe — deliberately break the thing the mocks are supposed to intercept and confirm the tests still pass (proving they mock it), then revert.
- **Why**: a broken patch target produces the exact same "all green" output as a correct one; only forcing the real code path to fail (and confirming tests don't fail with it) distinguishes the two.

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
