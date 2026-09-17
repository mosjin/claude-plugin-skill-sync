# Verified Facts

### On this Windows machine, a directory that is Claude Code's own session CWD cannot be renamed from within that session
- **Fact**: renaming `D:\works\claude_plugin_updater` failed identically from bash (`mv`) and a separate PowerShell process (`Rename-Item`) — both report the directory is in use.
- **Verified on**: 2026-09-17
- **Evidence**: both commands' error output in this session (`Device or resource busy`; `Cannot rename the item ... because it is in use`).
- **Verified by**: me, running both directly.
- **Invalidated by**: closing the session first, or running the rename from an unrelated terminal — either should work since the lock is the active session's own CWD handle, not a filesystem property.

### `claude plugin update`/`uninstall` support `-s/--scope <user|project|local|managed>`, default `user`
- **Fact**: without `-s`, both commands fail on any plugin not installed at `user` scope.
- **Verified on**: 2026-09-16
- **Evidence**: `claude plugin update --help` / `claude plugin uninstall --help` output; reproduced the failure live against `save-project-memory@save-project-memory` (scope `local`), fixed in commit `0f48d0f`.
- **Verified by**: me, reading CLI help + live repro in this session.
- **Invalidated by**: a future `claude` CLI release changing the flag name or default scope.

### No official Claude Code feature saves/syncs installed plugin+skill state across machines
- **Fact**: `claude plugin`/`claude config` have no export/sync/backup subcommand; the closest GitHub request (anthropics/claude-code#66303) is marked "not planned".
- **Verified on**: 2026-09-16
- **Evidence**: `claude plugin --help` subcommand list (init/install/uninstall/prune/update/enable/disable/list/validate/eval + marketplace); official docs at code.claude.com checked via a research subagent.
- **Verified by**: research subagent (WebFetch/WebSearch against official docs) + my own `claude plugin --help` check.
- **Invalidated by**: Anthropic shipping this feature in a future release — re-check `claude plugin --help` before building on this assumption again.

### `gh gist create` defaults to secret (unlisted, not access-controlled); `gh gist clone <id> <dir>` pulls every file in one call
- **Fact**: no `--public` flag needed for private-by-default; `gist clone` is a full `git clone` of the gist, avoiding a per-file `gist view --filename --raw` loop.
- **Verified on**: 2026-09-16
- **Evidence**: `gh gist create --help` / `gh gist clone --help` output (gh 2.89.0) on this machine.
- **Verified by**: me, reading CLI help directly.
- **Invalidated by**: a `gh` CLI update changing gist defaults or the clone command's shape.

### plugin_manager.py test suite is green at 187 tests as of the gist-list / auto-discovery feature
- **Fact**: `python -m pytest tests/ -q` → 187 passed, 0 failed (170 at commit `4e3b19e`, +7 from the synced-scope skip fix, +10 from `gist-list`/`resolve_gist_id` auto-discovery).
- **Verified on**: 2026-09-17
- **Evidence**: local pytest run output in this session.
- **Verified by**: me, running the suite directly.
- **Invalidated by**: any further commit to `plugin_manager.py`/`tests/test_plugin_manager.py` — re-run before trusting this count again.

### `gh gist list` has no `--json` flag — output is fixed-column TSV, one gist per line
- **Fact**: columns are `id\tdescription\tfile-count\tvisibility\tupdated-timestamp` (e.g. `1cd6200ecc8a99f2cf03d59954bda507\tclaude-plugin-skill-sync snapshots\t1 file\tsecret\t2026-09-17T00:34:47Z`). `--filter <regex>` matches against description/filenames (and content with `--include-content`), so tagging every gist this tool creates with a fixed description (`upload`'s `-d` flag) makes `--filter <that description>` a reliable way to find only this tool's gists.
- **Verified on**: 2026-09-17
- **Evidence**: `gh gist list --help` (no `--json` in FLAGS); live `gh gist list --filter claude-plugin-skill-sync -L 30` output on this machine, gh 2.89.0.
- **Verified by**: me, running both directly.
- **Invalidated by**: a future `gh` release adding `--json` support or changing the TSV column order — re-check `gh gist list --help` before trusting `parse_gist_list`'s column indices again.

### `claude plugin update/uninstall -s` rejects `scope: "synced"` outright, with a generic "Invalid scope" error instead of its own explanation
- **Fact**: plugins with `scope: "synced"` (pulled from a claude.ai account, no local marketplace backing) exist in `claude plugin list --json` output but are not among the `-s` flag's accepted values (`user`, `project`, `local`, `managed`). Passing `-s synced` fails with `Invalid scope "synced"...`; the CLI's real, more useful message — obtained by omitting `-s` (which defaults to `user`) or passing any accepted value — is: `This plugin is synced from your claude.ai account with no marketplace backing — it cannot be updated here. Manage it on claude.ai, or \`claude plugin disable\` to turn it off on this machine.`
- **Verified on**: 2026-09-17
- **Evidence**: live repro against `engineering@synced` and `ecc@synced` on this machine; `claude plugin update --help` confirms the accepted `-s` values; fixed in `plugin_manager.py` via `is_cli_manageable()` — these plugins are now skipped (status `skipped`) before any CLI call, in both `update_one` and `uninstall_one`.
- **Verified by**: me, running `claude plugin update <id> -s synced` and `claude plugin update <id>` (no `-s`) directly.
- **Invalidated by**: a future `claude` CLI release adding `synced` (or another new scope) to the `-s` flag's accepted values, or changing the wording of its own error message.
