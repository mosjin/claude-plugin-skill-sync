"""Merge a directory of per-machine snapshots into one drift view."""
import sys
import json
from pathlib import Path

from . import snapshots as snapshots_mod


def load_snapshots(dir_path: Path) -> list:
    """Read every `save`-produced snapshot in `dir_path`. A `merge --out`
    result living in the same directory is recognized by its `kind` marker
    and skipped rather than fed back in as an input.
    """
    if not dir_path.is_dir():
        sys.exit(f"Error: snapshot dir not found: {dir_path}")
    snapshots = []
    for path in sorted(dir_path.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            sys.exit(f"Error reading snapshot {path}: {exc}")
        if not isinstance(data, dict):
            sys.exit(f"Error: {path} is not a JSON object (got {type(data).__name__})")
        if data.get("kind") == "merged":
            continue
        if data.get("schema_version") != snapshots_mod.SNAPSHOT_SCHEMA_VERSION:
            sys.exit(
                f"Error: {path} has schema_version {data.get('schema_version')!r}, "
                f"expected {snapshots_mod.SNAPSHOT_SCHEMA_VERSION}"
            )
        snapshots.append(data)
    if not snapshots:
        sys.exit(f"Error: no snapshot files found in {dir_path}")
    return snapshots


_REQUIRED_SNAPSHOT_FIELDS = {"identity", "machine", "platform", "captured_at", "plugins", "skills"}


def merge_snapshots(snapshots: list) -> dict:
    """Pure function: list of snapshot dicts in, one drift-view dict out.
    Raises ValueError on bad input — no process-level side effects, so
    callers (CLI or test) decide how to react.

    Merge is always a read-time projection over independent, append-only
    per-machine snapshots — nothing here mutates a shared file. For each
    machine, exactly ONE snapshot (the most recently captured) supplies
    that machine's plugins/skills wholesale — a snapshot is a point-in-time
    whole, never merged field-by-field with another snapshot of the same
    machine, so an uninstalled plugin or a deleted skill from an older save
    cannot linger forever. `captured_at` is date-only (see `capture_snapshot`),
    so same-day snapshots for one machine tie; ties are broken by input
    order (`load_snapshots` feeds files in filename-sorted order) and
    surfaced in the returned `notes` list rather than resolved silently.
    Calling this twice with the same input list yields the same output.
    """
    if not snapshots:
        raise ValueError("no snapshots to merge")

    for snap in snapshots:
        if not isinstance(snap, dict):
            raise ValueError(f"snapshot is not a JSON object: {snap!r}")
        missing = _REQUIRED_SNAPSHOT_FIELDS - snap.keys()
        if missing:
            raise ValueError(f"snapshot missing required field(s): {sorted(missing)}")

    identities = {s["identity"] for s in snapshots}
    if len(identities) > 1:
        raise ValueError(f"snapshots belong to different identities: {sorted(identities)}")

    latest_by_machine = {}
    notes = []
    for snap in snapshots:
        machine_key = f"{snap['machine']}@{snap['platform']}"
        existing = latest_by_machine.get(machine_key)
        if existing is None or snap["captured_at"] > existing["captured_at"]:
            latest_by_machine[machine_key] = snap
        elif snap["captured_at"] == existing["captured_at"]:
            notes.append(
                f"{machine_key}: multiple snapshots dated {snap['captured_at']} — "
                f"using the last one given to merge; delete stale saves for "
                f"this machine to avoid relying on tie-break order"
            )
            latest_by_machine[machine_key] = snap

    machines = {
        key: {"machine": snap["machine"], "platform": snap["platform"], "captured_at": snap["captured_at"]}
        for key, snap in latest_by_machine.items()
    }

    plugins = {}
    skills = {}
    marketplaces = {}
    for machine_key, snap in latest_by_machine.items():
        for plugin in snap["plugins"]:
            entry = plugins.setdefault(plugin["id"], {"present_on": {}})
            entry["present_on"][machine_key] = {
                "version": plugin["version"],
                "scope": plugin["scope"],
                "enabled": plugin["enabled"],
            }
        for skill in snap["skills"]:
            skill_key = f"{skill['owner']}/{skill['name']}"
            entry = skills.setdefault(skill_key, {"present_on": {}})
            entry["present_on"][machine_key] = True
        # Absent on snapshots saved before this field existed — .get(), not
        # a required field, so those old snapshots still merge cleanly.
        for market in snap.get("marketplaces", []):
            entry = marketplaces.setdefault(market["name"], {"present_on": {}})
            entry["present_on"][machine_key] = {k: v for k, v in market.items() if k != "name"}

    for entry in plugins.values():
        infos = entry["present_on"].values()
        drift = []
        if len({i["version"] for i in infos}) > 1:
            drift.append("version")
        if len({i["scope"] for i in infos}) > 1:
            drift.append("scope")
        if len({i["enabled"] for i in infos}) > 1:
            drift.append("enabled")
        entry["drift"] = drift

    for name, entry in marketplaces.items():
        infos = list(entry["present_on"].values())
        # Every machine's recording of this marketplace's source should
        # agree — if they don't, `apply` needs one to trust; last one
        # written wins (dict iteration over latest_by_machine), same
        # tie-break philosophy as the per-machine snapshot pick above.
        sources = {json.dumps(i, sort_keys=True) for i in infos}
        entry["drift"] = ["source"] if len(sources) > 1 else []
        entry["source_record"] = infos[-1]

    return {
        "schema_version": snapshots_mod.SNAPSHOT_SCHEMA_VERSION,
        "kind": "merged",
        "identity": identities.pop(),
        "machines": machines,
        "plugins": plugins,
        "skills": skills,
        "marketplaces": marketplaces,
        "notes": notes,
    }


def _print_merge_table(merged: dict) -> None:
    machine_keys = sorted(merged["machines"])
    print(f"Identity: {merged['identity']}")
    print(f"Machines: {', '.join(machine_keys)}\n")

    print("Plugins:")
    for pid in sorted(merged["plugins"]):
        entry = merged["plugins"][pid]
        marker = f" ({'/'.join(entry['drift'])} drift)" if entry["drift"] else ""
        where = ", ".join(
            f"{m}={info['version']}" for m, info in sorted(entry["present_on"].items())
        )
        missing = [m for m in machine_keys if m not in entry["present_on"]]
        missing_note = f"  [missing on: {', '.join(missing)}]" if missing else ""
        print(f"  {pid}{marker}: {where}{missing_note}")

    print("\nSkills:")
    for key in sorted(merged["skills"]):
        entry = merged["skills"][key]
        where = ", ".join(sorted(entry["present_on"]))
        missing = [m for m in machine_keys if m not in entry["present_on"]]
        missing_note = f"  [missing on: {', '.join(missing)}]" if missing else ""
        print(f"  {key}: {where}{missing_note}")

    if merged["notes"]:
        print("\nNotes:")
        for note in merged["notes"]:
            print(f"  - {note}")


def cmd_merge(args) -> None:
    dir_path = snapshots_mod.resolve_snapshot_dir(args)
    snapshots = load_snapshots(dir_path)
    try:
        merged = merge_snapshots(snapshots)
    except ValueError as exc:
        sys.exit(f"Error: {exc}")
    _print_merge_table(merged)

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(merged, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\nMerged view written to: {out_path}")


