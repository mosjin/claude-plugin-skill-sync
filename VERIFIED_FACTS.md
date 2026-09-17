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

### plugin_manager.py test suite is green at 177 tests as of the synced-scope fix
- **Fact**: `python -m pytest tests/ -q` → 177 passed, 0 failed (170 at commit `4e3b19e`, +7 from the synced-scope skip fix below).
- **Verified on**: 2026-09-17
- **Evidence**: local pytest run output in this session.
- **Verified by**: me, running the suite directly.
- **Invalidated by**: any further commit to `plugin_manager.py`/`tests/test_plugin_manager.py` — re-run before trusting this count again.

### `claude plugin update/uninstall -s` rejects `scope: "synced"` outright, with a generic "Invalid scope" error instead of its own explanation
- **Fact**: plugins with `scope: "synced"` (pulled from a claude.ai account, no local marketplace backing) exist in `claude plugin list --json` output but are not among the `-s` flag's accepted values (`user`, `project`, `local`, `managed`). Passing `-s synced` fails with `Invalid scope "synced"...`; the CLI's real, more useful message — obtained by omitting `-s` (which defaults to `user`) or passing any accepted value — is: `This plugin is synced from your claude.ai account with no marketplace backing — it cannot be updated here. Manage it on claude.ai, or \`claude plugin disable\` to turn it off on this machine.`
- **Verified on**: 2026-09-17
- **Evidence**: live repro against `engineering@synced` and `ecc@synced` on this machine; `claude plugin update --help` confirms the accepted `-s` values; fixed in `plugin_manager.py` via `is_cli_manageable()` — these plugins are now skipped (status `skipped`) before any CLI call, in both `update_one` and `uninstall_one`.
- **Verified by**: me, running `claude plugin update <id> -s synced` and `claude plugin update <id>` (no `-s`) directly.
- **Invalidated by**: a future `claude` CLI release adding `synced` (or another new scope) to the `-s` flag's accepted values, or changing the wording of its own error message.
