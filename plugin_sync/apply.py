"""`apply`: install what's missing on this machine from a merged snapshot."""
import sys
import json
from pathlib import Path

from . import claude_cli, marketplaces


LANG_MESSAGES = {
    "en": {
        "nothing_missing": "Nothing missing — every plugin in the merged snapshot is already installed here.",
        "installable_header": "Installable (marketplace already available here):",
        "addable_header": "Needs a marketplace add first (will be added automatically with -y):",
        "skip_header": "Skipped (no marketplace source on record — add it manually first):",
        "other_platform_header": "Skipped — only ever seen on a different platform than this machine (pass --include-other-platforms to consider anyway):",
        "other_platform_included_header": "Included despite only ever being seen on a different platform (--include-other-platforms):",
        "all_missing_were_other_platform": "Nothing else missing — every other-platform plugin above was skipped. Pass --include-other-platforms to consider them.",
        "dry_run_note": "Dry run — pass -y to actually install. Nothing was installed.",
        "scope_required": "Error: --scope is required to actually install (user/project/local).",
        "select_prompt": "Install which? [a]ll / [n]one / comma-separated numbers: ",
        "adding_marketplace": "Adding marketplace '{name}' from {source}...",
        "installing": "Installing {id} (scope={scope})...",
        "summary": "Installed: {ok}  Failed: {fail}",
    },
    "zh": {
        "nothing_missing": "没有缺的插件 — 合并快照里的插件这台机器都已经装了。",
        "installable_header": "可装（这台机器已有对应 marketplace）：",
        "addable_header": "需要先加 marketplace 源（加 -y 会自动加）：",
        "skip_header": "跳过（没记录到 marketplace 源，先手动加）：",
        "other_platform_header": "跳过（只在别的平台见过，跟本机不符；加 --include-other-platforms 强制考虑）：",
        "other_platform_included_header": "已纳入（虽然只在别的平台见过，因为加了 --include-other-platforms）：",
        "all_missing_were_other_platform": "没别的缺项了 —— 上面列的都是平台不符被跳过的。加 --include-other-platforms 才会考虑它们。",
        "dry_run_note": "预览模式，未实际安装。加 -y 才会真正安装。",
        "scope_required": "错误：真正安装需要 --scope（user/project/local）。",
        "select_prompt": "装哪些？[a]全部 / [n]不装 / 逗号分隔序号：",
        "adding_marketplace": "正在加 marketplace '{name}'（源：{source}）...",
        "installing": "正在装 {id}（scope={scope}）...",
        "summary": "已装：{ok}  失败：{fail}",
    },
}


DEFAULT_LANG = "en"


def _msg(lang: str, key: str, **kwargs) -> str:
    table = LANG_MESSAGES.get(lang, LANG_MESSAGES[DEFAULT_LANG])
    return table[key].format(**kwargs)


def load_merged(path: Path) -> dict:
    if not path.is_file():
        sys.exit(f"Error: merged snapshot file not found: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        sys.exit(f"Error reading {path}: {exc}")
    if not isinstance(data, dict) or data.get("kind") != "merged":
        sys.exit(f"Error: {path} is not a `diff --out` file (expected kind='merged')")
    return data


def missing_plugin_ids(merged: dict, installed_ids: set) -> list:
    return sorted(pid for pid in merged["plugins"] if pid not in installed_ids)


def other_platform_only_ids(merged: dict, plugin_ids: list, current_platform: str) -> list:
    """Which of `plugin_ids` have only ever been seen (across every merged
    machine) on a platform other than `current_platform`.

    Heuristic, not a manifest fact — see the comment on merge_snapshots'
    `platforms` field for why no authoritative source exists to query
    instead. A plugin with an empty/missing `platforms` list (older
    `merge --out` files predating this field) is never flagged: no signal
    beats a wrong one.
    """
    result = []
    for pid in plugin_ids:
        platforms = merged["plugins"][pid].get("platforms", [])
        if platforms and current_platform not in platforms:
            result.append(pid)
    return result


def cmd_apply(args) -> None:
    lang = args.lang or DEFAULT_LANG
    merged = load_merged(Path(args.merged_file))
    installed_ids = {p["id"] for p in claude_cli.list_plugins()}
    missing = missing_plugin_ids(merged, installed_ids)

    if not missing:
        print(_msg(lang, "nothing_missing"))
        return

    include_other_platforms = getattr(args, "include_other_platforms", False)
    other_platform = other_platform_only_ids(merged, missing, sys.platform)
    if other_platform:
        header = "other_platform_included_header" if include_other_platforms else "other_platform_header"
        print(_msg(lang, header))
        for pid in other_platform:
            seen = ", ".join(merged["plugins"][pid]["platforms"])
            print(f"  {pid}  (seen only on: {seen}; this machine is {sys.platform})")
        if not include_other_platforms:
            missing = [pid for pid in missing if pid not in other_platform]
            if not missing:
                print(_msg(lang, "all_missing_were_other_platform"))
                return

    local_marketplaces = marketplaces.marketplace_install_locations()
    merged_marketplaces = merged.get("marketplaces", {})
    installable, addable, skipped, addable_sources = marketplaces.classify_missing_plugins(
        missing, local_marketplaces, merged_marketplaces
    )

    if installable:
        print(_msg(lang, "installable_header"))
        for pid in installable:
            where = ", ".join(sorted(merged["plugins"][pid]["present_on"]))
            print(f"  {pid}  (on: {where})")
            desc = marketplaces.plugin_description(pid, local_marketplaces)
            if desc:
                print(f"    {desc}")
    if addable:
        print(_msg(lang, "addable_header"))
        for pid in addable:
            _, _, marketplace = pid.partition("@")
            print(f"  {pid}  (adds marketplace '{marketplace}' from {addable_sources[marketplace]})")
    if skipped:
        print(_msg(lang, "skip_header"))
        for pid in skipped:
            print(f"  {pid}")

    selectable = installable + addable
    if not selectable:
        return

    if args.all:
        targets = selectable
    elif args.plugins:
        targets = [p for p in args.plugins if p in selectable]
        unknown = [p for p in args.plugins if p not in selectable]
        if unknown:
            sys.exit(f"Error: not in the installable list: {unknown}")
    else:
        print()
        answer = input(_msg(lang, "select_prompt")).strip().lower()
        if answer in ("a", "all"):
            targets = selectable
        elif answer in ("", "n", "none"):
            targets = []
        else:
            try:
                indices = [int(x.strip()) for x in answer.split(",") if x.strip()]
                targets = [selectable[i - 1] for i in indices]
            except (ValueError, IndexError):
                sys.exit("Error: could not parse selection")

    if not targets:
        return

    if not args.yes:
        print()
        print(_msg(lang, "dry_run_note"))
        return

    if not args.scope:
        sys.exit(_msg(lang, "scope_required"))

    # Cache each marketplace's add outcome (None = succeeded) — dedup so
    # two plugins from the same new marketplace only trigger one `add`.
    add_results = {}
    ok = fail = 0
    for pid in targets:
        _, _, marketplace = pid.partition("@")
        if marketplace in addable_sources:
            if marketplace not in add_results:
                source = addable_sources[marketplace]
                print(_msg(lang, "adding_marketplace", name=marketplace, source=source))
                code, out, err = claude_cli.run_claude(["plugin", "marketplace", "add", source, "--scope", args.scope])
                add_results[marketplace] = None if code == 0 else (out + err).strip()
            add_error = add_results[marketplace]
            if add_error is not None:
                fail += 1
                print(f"  {pid}: skipped — marketplace add failed: {add_error}")
                continue

        print(_msg(lang, "installing", id=pid, scope=args.scope))
        code, out, err = claude_cli.run_claude(["plugin", "install", pid, "-s", args.scope, "-y"])
        if code == 0:
            ok += 1
        else:
            fail += 1
            print(f"  {(out + err).strip()} (exit {code})")
    print(_msg(lang, "summary", ok=ok, fail=fail))
    if fail:
        sys.exit(1)


