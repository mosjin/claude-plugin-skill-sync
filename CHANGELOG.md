# Changelog

## [unreleased] 2026-09-16

### 🚀 Feature
- `save` / `merge`: capture a desensitized snapshot of installed plugins + skills, merge snapshots from multiple machines into a per-machine drift view (version/scope/enabled differences, what's missing where). (#5)
- `upload` / `fetch`: sync snapshots across machines via a GitHub Gist, reusing existing `gh` auth. (#7)
- `apply`: install onto this machine whatever a merged snapshot shows is missing here — dry-run by default, `-y --scope <user|project|local>` to execute, `--lang en|zh` for the tool's own prompts. (#8)

### 🐛 Fix
- `update` / `uninstall`: pass the plugin's own recorded scope (`-s user|project|local`) instead of relying on the CLI's `user` default — previously any local/project-scope plugin failed both commands. (#6)

### Tests
170 tests (up from 36), all subprocess calls mocked.
