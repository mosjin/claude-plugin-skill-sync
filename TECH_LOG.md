# Tech Log

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
