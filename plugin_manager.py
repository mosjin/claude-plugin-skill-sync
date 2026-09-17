"""Claude plugin manager — list, update, and snapshot-sync plugins/skills.

Usage:
    python plugin_manager.py list
    python plugin_manager.py update caveman
    python plugin_manager.py update caveman ecc eduforge
    python plugin_manager.py update --all
    python plugin_manager.py update --all --parallel
    python plugin_manager.py save --identity mosjin --machine work-laptop
    python plugin_manager.py merge
    python plugin_manager.py upload snapshots/<file>.json
    python plugin_manager.py gist-list
    python plugin_manager.py fetch --gist-id <id>
    python plugin_manager.py apply merged.json --all -y --scope user

Implementation lives in plugin_sync/ (claude_cli, snapshots, marketplaces,
merge, gist, apply, cli) — this file is a thin entry point so `python
plugin_manager.py <command>` keeps working exactly as documented above and
in README.md/README.en.md.
"""
from plugin_sync.cli import main

if __name__ == "__main__":
    main()
