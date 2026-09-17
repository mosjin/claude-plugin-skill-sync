"""GitHub Gist sync: upload/fetch/gist-list and the shared gist-id resolution."""
import os
import re
import shutil
import subprocess
import sys
import json
import tempfile
from pathlib import Path
from typing import Optional

from . import snapshots


def run_gh(args: list) -> tuple:
    """Thin subprocess seam for the GitHub CLI — mirrors run_claude() so
    `upload`/`fetch` are mockable the same way the rest of the file is.
    """
    exe = shutil.which("gh")
    if not exe:
        sys.exit("Error: 'gh' not found in PATH. Is GitHub CLI installed?")
    result = subprocess.run(
        [exe] + args,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.returncode, result.stdout, result.stderr


ENV_GIST_ID = "PLUGIN_MANAGER_GIST_ID"


SNAPSHOT_GIST_DESCRIPTION = "claude-plugin-skill-sync snapshots"


_GIST_URL_RE = re.compile(r"https://gist\.github\.com/[^/\s]+/([0-9a-fA-F]+)")


def _gist_id_marker_path(snapshot_dir: Path) -> Path:
    """A gist id is paired with the snapshot dir it syncs, not with any
    single command invocation — caching it here (next to the data it
    identifies) means a second `upload` in the same dir doesn't need
    --gist-id/the env var repeated, and can't silently spray snapshots
    across a fresh gist every time the flag is forgotten."""
    return snapshot_dir / ".gist_id"


def parse_gist_list(output: str) -> list:
    """Parse `gh gist list` TSV output into records.

    Line shape is "<id>\\t<description>\\t<file count>\\t<visibility>\\t<updated>".
    No `--json` support on this gh subcommand (verified against `gh gist list
    --help`), so this is a plain split, not a json.loads.
    """
    records = []
    for line in output.splitlines():
        parts = line.split("\t")
        if len(parts) != 5:
            continue
        gist_id, description, files, visibility, updated = parts
        records.append({
            "id": gist_id.strip(),
            "description": description.strip(),
            "files": files.strip(),
            "visibility": visibility.strip(),
            "updated_at": updated.strip(),
        })
    return records


def list_snapshot_gists() -> list:
    """This tool's own gists — anything tagged SNAPSHOT_GIST_DESCRIPTION on
    the authenticated GitHub account. Backs both `gist-list` and
    resolve_gist_id's auto-discovery.
    """
    code, out, err = run_gh(["gist", "list", "--filter", SNAPSHOT_GIST_DESCRIPTION, "-L", "100"])
    if code != 0:
        sys.exit(f"Error listing gists: {_clean_gh_error(out, err, code)}")
    return parse_gist_list(out)


def cmd_gist_list(_args) -> None:
    gists = list_snapshot_gists()
    if not gists:
        print(f"No gists tagged '{SNAPSHOT_GIST_DESCRIPTION}' found on this GitHub account.")
        print("Run `upload` on a machine that has a snapshot to create one.")
        return

    id_w = max(len(g["id"]) for g in gists) + 2
    vis_w = max(len(g["visibility"]) for g in gists) + 2
    header = f"{'Gist ID':<{id_w}} {'Visibility':<{vis_w}} {'Files':<10} Updated"
    print(header)
    print("─" * len(header))
    for g in gists:
        print(f"{g['id']:<{id_w}} {g['visibility']:<{vis_w}} {g['files']:<10} {g['updated_at']}")
    print(f"\n{len(gists)} gist{'s' if len(gists) != 1 else ''} found.")
    print("Use one with: fetch --gist-id <id>  (or upload --gist-id <id>)")


def resolve_gist_id(args, snapshot_dir: Optional[Path] = None) -> Optional[str]:
    """No sys.exit here for the zero-match case — unlike identity/machine, a
    missing gist id is valid for `upload` (it means "create a new one").
    `fetch` requires one and checks for it itself.

    Precedence: --gist-id flag, then the env var, then this snapshot dir's
    own cached marker file, then auto-discovery — exactly one gist tagged
    SNAPSHOT_GIST_DESCRIPTION on this account means there's no ambiguity to
    ask the user about, so use it and cache it (this is what lets a second
    machine `fetch`/`upload` without ever typing an id by hand). Two or
    more candidates IS ambiguous — sys.exit here rather than let `upload`
    silently create a third, disconnected gist.
    """
    if args.gist_id:
        return args.gist_id
    env_id = os.environ.get(ENV_GIST_ID)
    if env_id:
        return env_id
    if snapshot_dir is not None:
        marker = _gist_id_marker_path(snapshot_dir)
        if marker.is_file():
            cached = marker.read_text(encoding="utf-8").strip()
            if cached:
                return cached

    matches = list_snapshot_gists()
    if len(matches) > 1:
        ids = ", ".join(m["id"] for m in matches)
        sys.exit(
            f"Error: {len(matches)} gists tagged '{SNAPSHOT_GIST_DESCRIPTION}' "
            f"found ({ids}) — ambiguous. Run `gist-list`, then pass --gist-id "
            f"<id> explicitly."
        )
    if len(matches) == 1:
        gist_id = matches[0]["id"]
        print(f"Auto-detected gist {gist_id} (only one tagged '{SNAPSHOT_GIST_DESCRIPTION}')")
        if snapshot_dir is not None:
            snapshot_dir.mkdir(parents=True, exist_ok=True)
            _gist_id_marker_path(snapshot_dir).write_text(gist_id, encoding="utf-8")
        return gist_id
    return None


def _extract_gist_id(create_output: str) -> str:
    """`gh gist create` prints the new gist's URL somewhere in its output —
    search rather than assume it's the entire (or even the first) line, and
    fail loudly instead of returning a hard-to-notice empty string."""
    match = _GIST_URL_RE.search(create_output)
    if not match:
        sys.exit(
            f"Error: could not find a gist URL in `gh gist create` output: "
            f"{create_output.strip()!r}"
        )
    return match.group(1)


def _clean_gh_error(out: str, err: str, code: int) -> str:
    """Prefer whichever stream actually has content — `err` being
    whitespace-only must not swallow a real message that landed in `out`."""
    message = err.strip() or out.strip() or "no output"
    return f"{message} (exit {code})"


def _is_valid_snapshot_file(path: Path) -> bool:
    """Refuse anything that isn't a `save`-produced snapshot — guards both
    `upload` (don't push `merged.json` or an unrelated file under a gist
    filename that silently replaces a real snapshot) and `fetch` (don't
    import a file that would make every later `merge` in this dir explode,
    permanently, since 'already exists locally' is the only skip check).
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False
    return (
        isinstance(data, dict)
        and data.get("kind") == "snapshot"
        and data.get("schema_version") == snapshots.SNAPSHOT_SCHEMA_VERSION
    )


def list_gist_files(gist_id: str) -> list:
    """Filenames currently in a gist. Backs `upload`'s stale-snapshot
    cleanup below — needs to know what's already there before deciding
    what (if anything) to remove.
    """
    code, out, err = run_gh(["gist", "view", gist_id, "--files"])
    if code != 0:
        sys.exit(f"Error listing files in gist {gist_id}: {_clean_gh_error(out, err, code)}")
    return [line.strip() for line in out.splitlines() if line.strip()]


def find_stale_gist_snapshots(gist_files: list, snapshot: dict, new_filename: str) -> list:
    """Which of a gist's current files are THIS machine's own older
    snapshots — same identity/machine/platform prefix as the one about to
    be uploaded, excluding the new file itself. `upload` removes these
    before adding the new one so a shared gist ends up with one snapshot
    per machine instead of accumulating every save forever.
    """
    prefix = snapshots._snapshot_machine_prefix(snapshot)
    return [name for name in gist_files if name.startswith(prefix) and name != new_filename]


def cmd_upload(args) -> None:
    file_path = Path(args.file)
    if not file_path.is_file():
        sys.exit(f"Error: snapshot file not found: {file_path}")
    if not _is_valid_snapshot_file(file_path):
        sys.exit(
            f"Error: {file_path} doesn't look like a `save`-produced "
            f"snapshot (expected kind='snapshot', schema_version="
            f"{snapshots.SNAPSHOT_SCHEMA_VERSION})."
        )

    snapshot_dir = snapshots.resolve_snapshot_dir(args)
    gist_id = resolve_gist_id(args, snapshot_dir)
    if gist_id:
        stale = []
        if not args.keep_history:
            snapshot = json.loads(file_path.read_text(encoding="utf-8"))
            stale = find_stale_gist_snapshots(list_gist_files(gist_id), snapshot, file_path.name)

        # Add before remove, never the reverse: a gist can't have zero
        # files, so removing this machine's last remaining snapshot before
        # the new one is added fails GitHub's own validation
        # ("Gist.files is missing", verified live — see VERIFIED_FACTS.md).
        # `gh gist edit` also refuses --add and --remove in the same
        # invocation, so this has to be two calls, in this order.
        code, out, err = run_gh(["gist", "edit", gist_id, "--add", str(file_path)])
        if code != 0:
            sys.exit(f"Error uploading to gist {gist_id}: {_clean_gh_error(out, err, code)}")
        print(f"Uploaded {file_path.name} to gist {gist_id}")

        for name in stale:
            code, out, err = run_gh(["gist", "edit", gist_id, "--remove", name])
            if code != 0:
                sys.exit(f"Error removing superseded snapshot {name} from gist {gist_id}: {_clean_gh_error(out, err, code)}")
            print(f"Removed superseded snapshot {name} from gist {gist_id}")
    else:
        code, out, err = run_gh(["gist", "create", str(file_path), "-d", SNAPSHOT_GIST_DESCRIPTION])
        if code != 0:
            sys.exit(f"Error creating gist: {_clean_gh_error(out, err, code)}")
        new_id = _extract_gist_id(out + err)
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        _gist_id_marker_path(snapshot_dir).write_text(new_id, encoding="utf-8")
        print(f"Created new gist ({(out + err).strip()})")
        print("  secret = unlisted, not private — anyone with the link can read it.")
        print(
            f"\nCached in {_gist_id_marker_path(snapshot_dir)} — future "
            f"upload/fetch in this dir reuse it automatically. To reuse it "
            f"from a different --dir, set {ENV_GIST_ID}={new_id}."
        )


def cmd_fetch(args) -> None:
    out_dir = snapshots.resolve_snapshot_dir(args)
    gist_id = resolve_gist_id(args, out_dir)
    if not gist_id:
        sys.exit(f"Error: gist id required. Pass --gist-id <id> or set {ENV_GIST_ID}.")

    out_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        clone_dir = Path(tmp) / "gist"
        code, out, err = run_gh(["gist", "clone", gist_id, str(clone_dir)])
        if code != 0:
            sys.exit(f"Error cloning gist {gist_id}: {_clean_gh_error(out, err, code)}")

        found = copied = invalid = 0
        for src in sorted(clone_dir.glob("*.json")):
            found += 1
            dest = out_dir / src.name
            if dest.exists():
                continue
            if not _is_valid_snapshot_file(src):
                invalid += 1
                continue
            shutil.copyfile(src, dest)
            copied += 1

        print(f"Fetched gist {gist_id}: {found} snapshot(s) found, {copied} new one(s) copied into {out_dir}")
        if invalid:
            print(f"  skipped {invalid} file(s) that aren't valid `save` snapshots")


