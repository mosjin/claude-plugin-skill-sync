"""Tests for plugin_manager.py — all subprocess calls are mocked."""

import json
import os
import sys
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch, call

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))
import plugin_manager
from plugin_sync import claude_cli, snapshots, marketplaces, gist
from plugin_sync import merge as merge_mod
from plugin_sync import apply as apply_mod


SAMPLE_PLUGINS = [
    {
        "id": "caveman@caveman",
        "version": "655b7d9c5431",
        "scope": "user",
        "enabled": True,
        "installPath": "/home/user/.claude/plugins/cache/caveman/caveman/655b7d9c5431",
        "installedAt": "2026-05-08T02:31:21.418Z",
        "lastUpdated": "2026-05-27T02:56:22.992Z",
    },
    {
        "id": "ecc@ecc",
        "version": "2.0.0-rc.1",
        "scope": "user",
        "enabled": True,
        "installPath": "/home/user/.claude/plugins/cache/ecc/ecc/2.0.0-rc.1",
        "installedAt": "2026-01-01T00:00:00.000Z",
        "lastUpdated": "2026-03-01T00:00:00.000Z",
    },
    {
        "id": "context7@claude-plugins-official",
        "version": "cda114029ef8",
        "scope": "user",
        "enabled": False,
        "installPath": "/home/user/.claude/plugins/cache/claude-plugins-official/context7/cda114029ef8",
        "installedAt": "2026-01-19T03:17:14.825Z",
        "lastUpdated": "2026-05-31T09:14:08.973Z",
    },
]


class TestListPlugins(unittest.TestCase):
    def test_returns_parsed_list(self):
        with patch("plugin_sync.claude_cli.run_claude", return_value=(0, json.dumps(SAMPLE_PLUGINS), "")) as mock:
            result = claude_cli.list_plugins()
        mock.assert_called_once_with(["plugin", "list", "--json"])
        self.assertEqual(len(result), 3)
        self.assertEqual(result[0]["id"], "caveman@caveman")

    def test_exits_on_cli_error(self):
        with patch("plugin_sync.claude_cli.run_claude", return_value=(1, "", "some error")):
            with self.assertRaises(SystemExit):
                claude_cli.list_plugins()


class TestResolvePlugins(unittest.TestCase):
    def test_exact_id_match(self):
        result = claude_cli.resolve_plugins(["caveman@caveman"], SAMPLE_PLUGINS)
        self.assertEqual(result[0]["id"], "caveman@caveman")

    def test_partial_name_match(self):
        result = claude_cli.resolve_plugins(["caveman"], SAMPLE_PLUGINS)
        self.assertEqual(result[0]["id"], "caveman@caveman")

    def test_multiple_names(self):
        result = claude_cli.resolve_plugins(["caveman", "ecc"], SAMPLE_PLUGINS)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["id"], "caveman@caveman")
        self.assertEqual(result[1]["id"], "ecc@ecc")

    def test_not_found_exits(self):
        with self.assertRaises(SystemExit):
            claude_cli.resolve_plugins(["nonexistent"], SAMPLE_PLUGINS)

    def test_empty_names_returns_empty(self):
        result = claude_cli.resolve_plugins([], SAMPLE_PLUGINS)
        self.assertEqual(result, [])


class TestUpdateOne(unittest.TestCase):
    """update_one is now a thin subprocess wrapper — it no longer guesses
    status from stdout wording. That's resolve_update_status's job, tested
    below against real before/after version state.
    """

    def test_returns_raw_code_and_message(self):
        with patch("plugin_sync.claude_cli.run_claude", return_value=(0, "Plugin updated successfully", "")):
            result = claude_cli.update_one(SAMPLE_PLUGINS[0])
        self.assertEqual(result["id"], "caveman@caveman")
        self.assertEqual(result["code"], 0)
        self.assertIn("updated successfully", result["message"])

    def test_nonzero_code_preserved(self):
        with patch("plugin_sync.claude_cli.run_claude", return_value=(1, "", "Failed to update: network error")):
            result = claude_cli.update_one(SAMPLE_PLUGINS[0])
        self.assertEqual(result["code"], 1)
        self.assertIn("network error", result["message"])

    def test_calls_with_full_id(self):
        with patch("plugin_sync.claude_cli.run_claude", return_value=(0, "updated", "")) as mock:
            claude_cli.update_one(SAMPLE_PLUGINS[0])
        mock.assert_called_once_with(["plugin", "update", "caveman@caveman", "-s", "user"])

    def test_passes_plugin_own_scope(self):
        """A local/project-scope plugin must not default to -s user — the
        CLI rejects update/uninstall at the wrong scope."""
        local_plugin = dict(SAMPLE_PLUGINS[0], scope="local")
        with patch("plugin_sync.claude_cli.run_claude", return_value=(0, "updated", "")) as mock:
            claude_cli.update_one(local_plugin)
        mock.assert_called_once_with(["plugin", "update", "caveman@caveman", "-s", "local"])

    def test_synced_scope_skipped_without_calling_cli(self):
        """`scope: "synced"` (claude.ai account plugin, no marketplace
        backing) isn't a valid `-s` value — the CLI rejects it outright
        with a generic "Invalid scope" error. update_one must recognize
        it and never call the CLI at all."""
        synced_plugin = dict(SAMPLE_PLUGINS[0], scope="synced")
        with patch("plugin_sync.claude_cli.run_claude") as mock:
            result = claude_cli.update_one(synced_plugin)
        mock.assert_not_called()
        self.assertTrue(result["unmanageable"])
        self.assertIsNone(result["code"])
        self.assertIn("claude.ai", result["message"])


class TestIsCliManageable(unittest.TestCase):
    def test_known_scopes_are_manageable(self):
        for scope in ("user", "project", "local", "managed"):
            with self.subTest(scope=scope):
                self.assertTrue(claude_cli.is_cli_manageable({"scope": scope}))

    def test_synced_scope_is_not_manageable(self):
        self.assertFalse(claude_cli.is_cli_manageable({"scope": "synced"}))

    def test_missing_scope_is_not_manageable(self):
        self.assertFalse(claude_cli.is_cli_manageable({}))


class TestResolveUpdateStatus(unittest.TestCase):
    """The version-diff replacement for the old regex-on-stdout guess."""

    def test_version_changed_is_updated(self):
        status = claude_cli.resolve_update_status("1.0.0", "1.1.0", code=0)
        self.assertEqual(status, "updated")

    def test_version_unchanged_is_current(self):
        status = claude_cli.resolve_update_status("1.0.0", "1.0.0", code=0)
        self.assertEqual(status, "current")

    def test_nonzero_exit_is_failed_even_if_version_changed(self):
        status = claude_cli.resolve_update_status("1.0.0", "1.1.0", code=1)
        self.assertEqual(status, "failed")

    def test_missing_after_version_is_failed(self):
        """Plugin vanished from the post-update list (e.g. it got removed) —
        cannot claim success without evidence the new version exists."""
        status = claude_cli.resolve_update_status("1.0.0", None, code=0)
        self.assertEqual(status, "failed")

    def test_success_message_but_unchanged_version_is_not_updated(self):
        """The bug this replaces: a zero exit code alone used to be read
        as success regardless of whether anything actually changed."""
        status = claude_cli.resolve_update_status("655b7d9c5431", "655b7d9c5431", code=0)
        self.assertEqual(status, "current")


class TestCmdList(unittest.TestCase):
    def test_output_contains_plugin_names(self):
        with patch("plugin_sync.claude_cli.list_plugins", return_value=SAMPLE_PLUGINS):
            with patch("sys.stdout", new_callable=StringIO) as mock_out:
                claude_cli.cmd_list(None)
                output = mock_out.getvalue()
        self.assertIn("caveman", output)
        self.assertIn("ecc", output)
        self.assertIn("context7", output)

    def test_output_shows_disabled_status(self):
        with patch("plugin_sync.claude_cli.list_plugins", return_value=SAMPLE_PLUGINS):
            with patch("sys.stdout", new_callable=StringIO) as mock_out:
                claude_cli.cmd_list(None)
                output = mock_out.getvalue()
        self.assertIn("✗", output)  # context7 is disabled

    def test_output_shows_plugin_count(self):
        with patch("plugin_sync.claude_cli.list_plugins", return_value=SAMPLE_PLUGINS):
            with patch("sys.stdout", new_callable=StringIO) as mock_out:
                claude_cli.cmd_list(None)
                output = mock_out.getvalue()
        self.assertIn("3 plugins", output)


class TestCmdUpdate(unittest.TestCase):
    """cmd_update now calls list_plugins twice: once to resolve targets
    (before-state), once after all updates run (after-state), and derives
    status from the diff via resolve_update_status. Mocks below supply both
    snapshots with side_effect=[before, after].
    """

    def _make_args(self, plugins=None, all_=False, parallel=False):
        class Args:
            pass
        a = Args()
        a.plugins = plugins or []
        a.all = all_
        a.parallel = parallel
        return a

    def test_update_single_by_partial_name(self):
        after = [dict(SAMPLE_PLUGINS[0], version="new-version"), SAMPLE_PLUGINS[1], SAMPLE_PLUGINS[2]]
        with patch("plugin_sync.claude_cli.list_plugins", side_effect=[SAMPLE_PLUGINS, after]):
            with patch("plugin_sync.claude_cli.update_one", return_value={"id": "caveman@caveman", "code": 0, "message": "ok"}) as mock_update:
                with patch("sys.stdout", new_callable=StringIO):
                    claude_cli.cmd_update(self._make_args(plugins=["caveman"]))
        mock_update.assert_called_once()
        self.assertEqual(mock_update.call_args[0][0]["id"], "caveman@caveman")

    def test_update_all(self):
        with patch("plugin_sync.claude_cli.list_plugins", side_effect=[SAMPLE_PLUGINS, SAMPLE_PLUGINS]):
            with patch("plugin_sync.claude_cli.update_one", return_value={"id": "x", "code": 0, "message": "ok"}) as mock_update:
                with patch("sys.stdout", new_callable=StringIO):
                    claude_cli.cmd_update(self._make_args(all_=True))
        self.assertEqual(mock_update.call_count, 3)

    def test_update_all_continues_on_failure(self):
        """One failure must not abort remaining updates."""
        def side_effect(plugin):
            if plugin["id"] == "ecc@ecc":
                return {"id": "ecc@ecc", "code": 1, "message": "network error"}
            return {"id": plugin["id"], "code": 0, "message": "ok"}

        after = [dict(SAMPLE_PLUGINS[0], version="new"), SAMPLE_PLUGINS[1], dict(SAMPLE_PLUGINS[2], version="new")]
        with patch("plugin_sync.claude_cli.list_plugins", side_effect=[SAMPLE_PLUGINS, after]):
            with patch("plugin_sync.claude_cli.update_one", side_effect=side_effect):
                with patch("sys.stdout", new_callable=StringIO):
                    with self.assertRaises(SystemExit) as ctx:
                        claude_cli.cmd_update(self._make_args(all_=True))
        self.assertEqual(ctx.exception.code, 1)

    def test_summary_shows_counts(self):
        """caveman updates (version changes), ecc is already current
        (version unchanged), context7 fails (nonzero exit)."""
        def side_effect(plugin):
            if plugin["id"] == "context7@claude-plugins-official":
                return {"id": plugin["id"], "code": 1, "message": "error"}
            return {"id": plugin["id"], "code": 0, "message": "ok"}

        after = [dict(SAMPLE_PLUGINS[0], version="new-version"), SAMPLE_PLUGINS[1], SAMPLE_PLUGINS[2]]
        with patch("plugin_sync.claude_cli.list_plugins", side_effect=[SAMPLE_PLUGINS, after]):
            with patch("plugin_sync.claude_cli.update_one", side_effect=side_effect):
                with patch("sys.stdout", new_callable=StringIO) as mock_out:
                    with self.assertRaises(SystemExit):
                        claude_cli.cmd_update(self._make_args(all_=True))
                    output = mock_out.getvalue()
        self.assertIn("Updated: 1", output)
        self.assertIn("Already current: 1", output)
        self.assertIn("Failed: 1", output)

    def test_updated_plugin_shows_restart_note(self):
        after = [dict(SAMPLE_PLUGINS[0], version="new-version"), SAMPLE_PLUGINS[1], SAMPLE_PLUGINS[2]]
        with patch("plugin_sync.claude_cli.list_plugins", side_effect=[SAMPLE_PLUGINS, after]):
            with patch("plugin_sync.claude_cli.update_one", return_value={"id": "caveman@caveman", "code": 0, "message": "ok"}):
                with patch("sys.stdout", new_callable=StringIO) as mock_out:
                    claude_cli.cmd_update(self._make_args(plugins=["caveman"]))
                    output = mock_out.getvalue()
        self.assertIn("restart Claude Code", output)

    def test_vanished_plugin_reports_honest_message_not_success_text(self):
        """code=0 but the plugin is absent from the post-update list must
        not print its own success message next to a ✗ — that reads as a
        contradiction."""
        after = [SAMPLE_PLUGINS[1], SAMPLE_PLUGINS[2]]  # caveman missing
        with patch("plugin_sync.claude_cli.list_plugins", side_effect=[SAMPLE_PLUGINS, after]):
            with patch("plugin_sync.claude_cli.update_one", return_value={"id": "caveman@caveman", "code": 0, "message": "Plugin updated successfully"}):
                with patch("sys.stdout", new_callable=StringIO) as mock_out:
                    with self.assertRaises(SystemExit):
                        claude_cli.cmd_update(self._make_args(plugins=["caveman"]))
                    output = mock_out.getvalue()
        self.assertNotIn("Plugin updated successfully", output)
        self.assertIn("no longer listed", output)

    def test_results_line_shows_before_after_version(self):
        """The complaint this fixes: per-item 'done' during the run says
        nothing about whether a version actually changed. The results
        block must spell out before → after so it's unambiguous."""
        after = [dict(SAMPLE_PLUGINS[0], version="new-version"), SAMPLE_PLUGINS[1], SAMPLE_PLUGINS[2]]
        with patch("plugin_sync.claude_cli.list_plugins", side_effect=[SAMPLE_PLUGINS, after]):
            with patch("plugin_sync.claude_cli.update_one", return_value={"id": "caveman@caveman", "code": 0, "message": "ok"}):
                with patch("sys.stdout", new_callable=StringIO) as mock_out:
                    claude_cli.cmd_update(self._make_args(plugins=["caveman"]))
                    output = mock_out.getvalue()
        self.assertIn("655b7d9c5431 → new-version", output)

    def test_results_line_shows_unchanged_version_when_current(self):
        with patch("plugin_sync.claude_cli.list_plugins", side_effect=[SAMPLE_PLUGINS, SAMPLE_PLUGINS]):
            with patch("plugin_sync.claude_cli.update_one", return_value={"id": "caveman@caveman", "code": 0, "message": "already current"}):
                with patch("sys.stdout", new_callable=StringIO) as mock_out:
                    claude_cli.cmd_update(self._make_args(plugins=["caveman"]))
                    output = mock_out.getvalue()
        self.assertIn("655b7d9c5431 (unchanged)", output)

    def test_no_restart_note_when_nothing_updated(self):
        with patch("plugin_sync.claude_cli.list_plugins", side_effect=[SAMPLE_PLUGINS, SAMPLE_PLUGINS]):
            with patch("plugin_sync.claude_cli.update_one", return_value={"id": "caveman@caveman", "code": 0, "message": "already current"}):
                with patch("sys.stdout", new_callable=StringIO) as mock_out:
                    claude_cli.cmd_update(self._make_args(plugins=["caveman"]))
                    output = mock_out.getvalue()
        self.assertNotIn("restart Claude Code", output)

    def test_no_plugins_and_no_all_exits(self):
        """Calling update with no args should error."""
        with patch("plugin_sync.claude_cli.list_plugins", return_value=SAMPLE_PLUGINS):
            with patch("sys.stdout", new_callable=StringIO):
                with self.assertRaises(SystemExit):
                    claude_cli.cmd_update(self._make_args(plugins=[], all_=False))

    def test_parallel_reports_updates_correctly(self):
        """--all --parallel is the README's documented fast path — must be
        covered directly, not just inferred from the sequential branch."""
        after = [dict(SAMPLE_PLUGINS[0], version="new"), dict(SAMPLE_PLUGINS[1], version="new"), SAMPLE_PLUGINS[2]]
        with patch("plugin_sync.claude_cli.list_plugins", side_effect=[SAMPLE_PLUGINS, after]):
            with patch("plugin_sync.claude_cli.update_one", return_value={"id": "placeholder", "code": 0, "message": "ok"}) as mock_update:
                def side_effect(plugin):
                    return {"id": plugin["id"], "code": 0, "message": "ok"}
                mock_update.side_effect = side_effect
                with patch("sys.stdout", new_callable=StringIO) as mock_out:
                    claude_cli.cmd_update(self._make_args(all_=True, parallel=True))
                    output = mock_out.getvalue()
        self.assertEqual(mock_update.call_count, 3)
        self.assertIn("Updated: 2", output)
        self.assertIn("Already current: 1", output)

    def test_parallel_exception_becomes_failed_row_not_crash(self):
        """A raised exception inside a worker thread must surface as a
        failed row in the summary, not an unhandled traceback."""
        def side_effect(plugin):
            if plugin["id"] == "ecc@ecc":
                raise RuntimeError("subprocess exploded")
            return {"id": plugin["id"], "code": 0, "message": "ok"}

        after = [dict(SAMPLE_PLUGINS[0], version="new"), SAMPLE_PLUGINS[1], dict(SAMPLE_PLUGINS[2], version="new")]
        with patch("plugin_sync.claude_cli.list_plugins", side_effect=[SAMPLE_PLUGINS, after]):
            with patch("plugin_sync.claude_cli.update_one", side_effect=side_effect):
                with patch("sys.stdout", new_callable=StringIO) as mock_out:
                    with self.assertRaises(SystemExit) as ctx:
                        claude_cli.cmd_update(self._make_args(all_=True, parallel=True))
                    output = mock_out.getvalue()
        self.assertEqual(ctx.exception.code, 1)
        self.assertIn("subprocess exploded", output)
        self.assertIn("✗ ecc@ecc", output)

    def test_synced_scope_plugin_reported_as_skipped_not_failed(self):
        """A `scope: "synced"` plugin must show up as skipped in the
        summary and must NOT trip the failed-exit-code path — it was never
        actually attempted, so it's not a failure."""
        synced = dict(SAMPLE_PLUGINS[0], id="engineering@synced", scope="synced")
        plugins = [synced, SAMPLE_PLUGINS[1]]

        def side_effect(plugin):
            if plugin["id"] == "engineering@synced":
                return {"id": "engineering@synced", "code": None, "message": claude_cli.UNMANAGEABLE_SCOPE_MESSAGE, "unmanageable": True}
            return {"id": plugin["id"], "code": 0, "message": "ok"}

        with patch("plugin_sync.claude_cli.list_plugins", side_effect=[plugins, plugins]):
            with patch("plugin_sync.claude_cli.update_one", side_effect=side_effect):
                with patch("sys.stdout", new_callable=StringIO) as mock_out:
                    claude_cli.cmd_update(self._make_args(all_=True))
                    output = mock_out.getvalue()
        self.assertIn("Skipped: 1", output)
        self.assertIn("• engineering@synced", output)
        self.assertIn("claude.ai", output)


class TestRunClaude(unittest.TestCase):
    def test_finds_claude_executable(self):
        with patch("plugin_sync.claude_cli.shutil.which", return_value="/usr/bin/claude"):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = type("R", (), {"returncode": 0, "stdout": "[]", "stderr": ""})()
                code, out, err = claude_cli.run_claude(["plugin", "list", "--json"])
        self.assertEqual(code, 0)
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        self.assertEqual(args[0], "/usr/bin/claude")
        self.assertIn("plugin", args)

    def test_exits_if_claude_not_in_path(self):
        with patch("plugin_sync.claude_cli.shutil.which", return_value=None):
            with self.assertRaises(SystemExit):
                claude_cli.run_claude(["plugin", "list", "--json"])


class TestUninstallOne(unittest.TestCase):
    def test_successful_uninstall(self):
        with patch("plugin_sync.claude_cli.run_claude", return_value=(0, "Plugin uninstalled successfully", "")):
            result = claude_cli.uninstall_one(SAMPLE_PLUGINS[0])
        self.assertEqual(result["status"], "uninstalled")
        self.assertEqual(result["id"], "caveman@caveman")

    def test_failed_uninstall(self):
        with patch("plugin_sync.claude_cli.run_claude", return_value=(1, "", "Plugin not found")):
            result = claude_cli.uninstall_one(SAMPLE_PLUGINS[0])
        self.assertEqual(result["status"], "failed")
        self.assertIn("not found", result["message"])

    def test_calls_with_full_id(self):
        with patch("plugin_sync.claude_cli.run_claude", return_value=(0, "ok", "")) as mock:
            claude_cli.uninstall_one(SAMPLE_PLUGINS[0])
        args = mock.call_args[0][0]
        self.assertEqual(args[:2], ["plugin", "uninstall"])
        self.assertIn("caveman@caveman", args)

    def test_synced_scope_skipped_without_calling_cli(self):
        synced_plugin = dict(SAMPLE_PLUGINS[0], scope="synced")
        with patch("plugin_sync.claude_cli.run_claude") as mock:
            result = claude_cli.uninstall_one(synced_plugin)
        mock.assert_not_called()
        self.assertEqual(result["status"], "skipped")
        self.assertIn("claude.ai", result["message"])

    def test_passes_plugin_own_scope(self):
        """A local/project-scope plugin must not default to -s user — the
        CLI rejects uninstall at the wrong scope."""
        local_plugin = dict(SAMPLE_PLUGINS[0], scope="local")
        with patch("plugin_sync.claude_cli.run_claude", return_value=(0, "ok", "")) as mock:
            claude_cli.uninstall_one(local_plugin)
        args = mock.call_args[0][0]
        self.assertIn("-s", args)
        self.assertEqual(args[args.index("-s") + 1], "local")

    def test_keep_data_flag(self):
        with patch("plugin_sync.claude_cli.run_claude", return_value=(0, "ok", "")) as mock:
            claude_cli.uninstall_one(SAMPLE_PLUGINS[0], keep_data=True)
        args = mock.call_args[0][0]
        self.assertIn("--keep-data", args)

    def test_prune_adds_yes(self):
        """--prune must include -y because subprocess is non-TTY."""
        with patch("plugin_sync.claude_cli.run_claude", return_value=(0, "ok", "")) as mock:
            claude_cli.uninstall_one(SAMPLE_PLUGINS[0], prune=True)
        args = mock.call_args[0][0]
        self.assertIn("--prune", args)
        self.assertIn("-y", args)

    def test_no_extra_flags_by_default(self):
        with patch("plugin_sync.claude_cli.run_claude", return_value=(0, "ok", "")) as mock:
            claude_cli.uninstall_one(SAMPLE_PLUGINS[0])
        args = mock.call_args[0][0]
        self.assertNotIn("--keep-data", args)
        self.assertNotIn("--prune", args)
        self.assertNotIn("-y", args)


class TestCmdUninstall(unittest.TestCase):
    def _make_args(self, plugins=None, yes=False, keep_data=False, prune=False):
        class Args:
            pass
        a = Args()
        a.plugins = plugins or []
        a.yes = yes
        a.keep_data = keep_data
        a.prune = prune
        return a

    def test_uninstall_single_with_yes(self):
        with patch("plugin_sync.claude_cli.list_plugins", return_value=SAMPLE_PLUGINS):
            with patch("plugin_sync.claude_cli.uninstall_one", return_value={"id": "caveman@caveman", "status": "uninstalled", "message": "ok"}) as mock:
                with patch("sys.stdout", new_callable=StringIO):
                    claude_cli.cmd_uninstall(self._make_args(plugins=["caveman"], yes=True))
        mock.assert_called_once()
        self.assertEqual(mock.call_args[0][0]["id"], "caveman@caveman")

    def test_uninstall_prompts_without_yes(self):
        with patch("plugin_sync.claude_cli.list_plugins", return_value=SAMPLE_PLUGINS):
            with patch("plugin_sync.claude_cli.uninstall_one") as mock_uninstall:
                with patch("builtins.input", return_value="n"):
                    with patch("sys.stdout", new_callable=StringIO):
                        claude_cli.cmd_uninstall(self._make_args(plugins=["caveman"], yes=False))
        mock_uninstall.assert_not_called()

    def test_uninstall_proceeds_on_yes_input(self):
        with patch("plugin_sync.claude_cli.list_plugins", return_value=SAMPLE_PLUGINS):
            with patch("plugin_sync.claude_cli.uninstall_one", return_value={"id": "caveman@caveman", "status": "uninstalled", "message": "ok"}) as mock:
                with patch("builtins.input", return_value="y"):
                    with patch("sys.stdout", new_callable=StringIO):
                        claude_cli.cmd_uninstall(self._make_args(plugins=["caveman"], yes=False))
        mock.assert_called_once()

    def test_uninstall_multiple(self):
        with patch("plugin_sync.claude_cli.list_plugins", return_value=SAMPLE_PLUGINS):
            with patch("plugin_sync.claude_cli.uninstall_one", return_value={"id": "x", "status": "uninstalled", "message": "ok"}) as mock:
                with patch("sys.stdout", new_callable=StringIO):
                    claude_cli.cmd_uninstall(self._make_args(plugins=["caveman", "ecc"], yes=True))
        self.assertEqual(mock.call_count, 2)

    def test_failure_exits_nonzero(self):
        def side_effect(plugin, **kw):
            return {"id": plugin["id"], "status": "failed", "message": "error"}

        with patch("plugin_sync.claude_cli.list_plugins", return_value=SAMPLE_PLUGINS):
            with patch("plugin_sync.claude_cli.uninstall_one", side_effect=side_effect):
                with patch("sys.stdout", new_callable=StringIO):
                    with self.assertRaises(SystemExit) as ctx:
                        claude_cli.cmd_uninstall(self._make_args(plugins=["caveman"], yes=True))
        self.assertEqual(ctx.exception.code, 1)

    def test_passes_keep_data_flag(self):
        with patch("plugin_sync.claude_cli.list_plugins", return_value=SAMPLE_PLUGINS):
            with patch("plugin_sync.claude_cli.uninstall_one", return_value={"id": "caveman@caveman", "status": "uninstalled", "message": "ok"}) as mock:
                with patch("sys.stdout", new_callable=StringIO):
                    claude_cli.cmd_uninstall(self._make_args(plugins=["caveman"], yes=True, keep_data=True))
        _, kwargs = mock.call_args
        self.assertTrue(kwargs.get("keep_data"))

    def test_passes_prune_flag(self):
        with patch("plugin_sync.claude_cli.list_plugins", return_value=SAMPLE_PLUGINS):
            with patch("plugin_sync.claude_cli.uninstall_one", return_value={"id": "caveman@caveman", "status": "uninstalled", "message": "ok"}) as mock:
                with patch("sys.stdout", new_callable=StringIO):
                    claude_cli.cmd_uninstall(self._make_args(plugins=["caveman"], yes=True, prune=True))
        _, kwargs = mock.call_args
        self.assertTrue(kwargs.get("prune"))

    def test_no_plugins_exits(self):
        with patch("plugin_sync.claude_cli.list_plugins", return_value=SAMPLE_PLUGINS):
            with self.assertRaises(SystemExit):
                claude_cli.cmd_uninstall(self._make_args(plugins=[], yes=True))

    def test_skipped_plugin_reported_but_does_not_exit_nonzero(self):
        """A synced-scope plugin uninstall_one skips must show up in the
        summary as skipped, not as a failure — it was never attempted."""
        with patch("plugin_sync.claude_cli.list_plugins", return_value=SAMPLE_PLUGINS):
            with patch(
                "plugin_sync.claude_cli.uninstall_one",
                return_value={"id": "caveman@caveman", "status": "skipped", "message": claude_cli.UNMANAGEABLE_SCOPE_MESSAGE},
            ):
                with patch("sys.stdout", new_callable=StringIO) as mock_out:
                    claude_cli.cmd_uninstall(self._make_args(plugins=["caveman"], yes=True))
                    output = mock_out.getvalue()
        self.assertIn("Skipped: 1", output)
        self.assertIn("Failed: 0", output)


SAMPLE_MCP_LIST_OUTPUT = """\
claude.ai Gmail: https://gmailmcp.googleapis.com/mcp/v1 - ✔ Connected
plugin:code-graph-mcp:code-graph: node /home/user/.claude/plugins/cache/code-graph-mcp/scripts/mcp-launcher.js - ✔ Connected
plugin:voicemode:voicemode: uv run voicemode - ✘ Failed to connect
firecrawl: npx -y firecrawl-mcp - ✔ Connected
"""


class TestParseMcpList(unittest.TestCase):
    def test_parses_all_lines(self):
        records = claude_cli.parse_mcp_list(SAMPLE_MCP_LIST_OUTPUT)
        self.assertEqual(len(records), 4)

    def test_splits_name_command_status(self):
        records = claude_cli.parse_mcp_list(SAMPLE_MCP_LIST_OUTPUT)
        firecrawl = next(r for r in records if r["name"] == "firecrawl")
        self.assertEqual(firecrawl["command"], "npx -y firecrawl-mcp")
        self.assertEqual(firecrawl["status"], "✔ Connected")

    def test_plugin_name_with_embedded_colons_kept_whole(self):
        records = claude_cli.parse_mcp_list(SAMPLE_MCP_LIST_OUTPUT)
        names = [r["name"] for r in records]
        self.assertIn("plugin:code-graph-mcp:code-graph", names)

    def test_ignores_blank_and_malformed_lines(self):
        records = claude_cli.parse_mcp_list("\n   \nnot a valid line\n" + SAMPLE_MCP_LIST_OUTPUT)
        self.assertEqual(len(records), 4)


class TestClassifyMcpServer(unittest.TestCase):
    def test_plugin_bundled(self):
        self.assertEqual(claude_cli.classify_mcp_server("plugin:code-graph-mcp:code-graph"), "plugin")

    def test_host_connector(self):
        self.assertEqual(claude_cli.classify_mcp_server("claude.ai Gmail"), "host")

    def test_standalone(self):
        self.assertEqual(claude_cli.classify_mcp_server("firecrawl"), "standalone")


class TestCmdDoctor(unittest.TestCase):
    def test_lists_only_standalone_servers(self):
        with patch("plugin_sync.claude_cli.run_claude", return_value=(0, SAMPLE_MCP_LIST_OUTPUT, "")):
            with patch("sys.stdout", new_callable=StringIO) as mock_out:
                claude_cli.cmd_doctor(None)
                output = mock_out.getvalue()
        self.assertIn("firecrawl", output)
        self.assertNotIn("plugin:code-graph-mcp", output)
        self.assertNotIn("claude.ai Gmail", output)

    def test_reports_none_when_all_managed(self):
        managed_only = "claude.ai Gmail: https://gmailmcp.googleapis.com/mcp/v1 - ✔ Connected\n"
        with patch("plugin_sync.claude_cli.run_claude", return_value=(0, managed_only, "")):
            with patch("sys.stdout", new_callable=StringIO) as mock_out:
                claude_cli.cmd_doctor(None)
                output = mock_out.getvalue()
        self.assertIn("none", output)

    def test_exits_on_cli_error(self):
        with patch("plugin_sync.claude_cli.run_claude", return_value=(1, "", "mcp list failed")):
            with self.assertRaises(SystemExit):
                claude_cli.cmd_doctor(None)


SAMPLE_PLUGIN_WITH_MCP = {
    "id": "context-mode@context-mode",
    "version": "1.0.169",
    "scope": "user",
    "enabled": True,
    "installPath": "D:\\works\\.claude\\plugins\\cache\\context-mode\\context-mode\\1.0.169",
    "installedAt": "2026-05-06T13:19:14.661Z",
    "lastUpdated": "2026-09-16T13:54:41.335Z",
    "mcpServers": {
        "context-mode": {
            "command": "C:/nvm4w/nodejs/node.exe",
            "args": ["D:/works/.claude/plugins/cache/context-mode/context-mode/1.0.169/start.mjs"],
        }
    },
}

LEAKY_PLUGIN = {
    "id": "leaky@leaky",
    "version": "1.0.0",
    "scope": "user",
    "enabled": True,
    "installPath": "C:\\Users\\mosjin\\.claude\\plugins\\cache\\leaky\\leaky\\1.0.0",
}


class TestSanitizeLabel(unittest.TestCase):
    def test_lowercases_and_collapses_separators(self):
        self.assertEqual(snapshots._sanitize_label("Work Laptop!!"), "work-laptop")

    def test_empty_after_sanitize_exits(self):
        with self.assertRaises(SystemExit):
            snapshots._sanitize_label("###")


class TestResolveIdentity(unittest.TestCase):
    def _args(self, identity=None):
        class Args:
            pass
        a = Args()
        a.identity = identity
        return a

    def test_uses_flag(self):
        self.assertEqual(snapshots.resolve_identity(self._args("mosjin")), "mosjin")

    def test_falls_back_to_env(self):
        with patch.dict("os.environ", {snapshots.ENV_IDENTITY: "envuser"}, clear=False):
            self.assertEqual(snapshots.resolve_identity(self._args(None)), "envuser")

    def test_missing_both_exits(self):
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(SystemExit):
                snapshots.resolve_identity(self._args(None))

    def test_full_email_rejected(self):
        """The full address must never be written into a snapshot verbatim."""
        with self.assertRaises(SystemExit):
            snapshots.resolve_identity(self._args("mosjin@gmail.com"))

    def test_sanitizes_mixed_case_and_punctuation(self):
        self.assertEqual(snapshots.resolve_identity(self._args("Mo Sjin!")), "mo-sjin")


class TestResolveMachine(unittest.TestCase):
    def _args(self, machine=None):
        class Args:
            pass
        a = Args()
        a.machine = machine
        return a

    def test_uses_flag(self):
        self.assertEqual(snapshots.resolve_machine(self._args("win-desktop")), "win-desktop")

    def test_falls_back_to_env(self):
        with patch.dict("os.environ", {snapshots.ENV_MACHINE: "env-box"}, clear=False):
            self.assertEqual(snapshots.resolve_machine(self._args(None)), "env-box")

    def test_sanitizes_raw_hostname_style_input(self):
        self.assertEqual(snapshots.resolve_machine(self._args("DESKTOP-4F7K2Q1")), "desktop-4f7k2q1")

    def test_missing_both_exits(self):
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(SystemExit):
                snapshots.resolve_machine(self._args(None))


class TestResolveSnapshotDir(unittest.TestCase):
    def _args(self, dir_=None):
        class Args:
            pass
        a = Args()
        a.dir = dir_
        return a

    def test_uses_flag(self):
        result = snapshots.resolve_snapshot_dir(self._args("custom/dir"))
        self.assertEqual(result, Path("custom/dir"))

    def test_falls_back_to_env(self):
        with patch.dict("os.environ", {snapshots.ENV_SNAPSHOT_DIR: "env/dir"}, clear=False):
            result = snapshots.resolve_snapshot_dir(self._args(None))
        self.assertEqual(result, Path("env/dir"))

    def test_falls_back_to_default(self):
        with patch.dict("os.environ", {}, clear=True):
            result = snapshots.resolve_snapshot_dir(self._args(None))
        self.assertEqual(result, snapshots.DEFAULT_SNAPSHOT_DIR)


class TestWhitelistPlugin(unittest.TestCase):
    def test_keeps_only_allowed_fields(self):
        record = snapshots.whitelist_plugin(SAMPLE_PLUGIN_WITH_MCP)
        self.assertEqual(
            set(record.keys()),
            {"id", "version", "scope", "enabled", "mcp_server_names"},
        )

    def test_drops_install_path_value_not_just_key(self):
        """Checking the camelCase KEY is absent isn't enough — a leak could
        arrive under any key. Assert the actual path VALUE is gone."""
        record = snapshots.whitelist_plugin(LEAKY_PLUGIN)
        blob = json.dumps(record)
        self.assertNotIn("installPath", blob)
        self.assertNotIn(LEAKY_PLUGIN["installPath"], blob)
        self.assertNotIn("mosjin", blob)

    def test_drops_mcp_command_and_args(self):
        record = snapshots.whitelist_plugin(SAMPLE_PLUGIN_WITH_MCP)
        blob = json.dumps(record)
        self.assertNotIn("D:/works", blob)
        self.assertNotIn("node.exe", blob)
        self.assertNotIn("command", blob)
        self.assertNotIn("args", blob)

    def test_mcp_server_names_only(self):
        record = snapshots.whitelist_plugin(SAMPLE_PLUGIN_WITH_MCP)
        self.assertEqual(record["mcp_server_names"], ["context-mode"])

    def test_plugin_without_mcp_servers(self):
        record = snapshots.whitelist_plugin(SAMPLE_PLUGINS[0])
        self.assertEqual(record["mcp_server_names"], [])

    def test_mcp_servers_as_list_does_not_crash(self):
        """A future `claude plugin list --json` could change mcpServers'
        shape — the allowlist must degrade safely, not crash `save`."""
        plugin = dict(SAMPLE_PLUGINS[0], mcpServers=["not", "a", "dict"])
        record = snapshots.whitelist_plugin(plugin)
        self.assertEqual(record["mcp_server_names"], [])

    def test_mcp_servers_as_string_does_not_crash(self):
        plugin = dict(SAMPLE_PLUGINS[0], mcpServers="./.mcp.json")
        record = snapshots.whitelist_plugin(plugin)
        self.assertEqual(record["mcp_server_names"], [])


class TestWhitelistMarketplace(unittest.TestCase):
    def test_github_source_keeps_repo(self):
        m = {"name": "caveman", "source": "github", "repo": "JuliusBrussee/caveman", "installLocation": "C:\\Users\\mosjin\\x"}
        self.assertEqual(
            marketplaces.whitelist_marketplace(m),
            {"name": "caveman", "source": "github", "repo": "JuliusBrussee/caveman"},
        )

    def test_git_source_keeps_url(self):
        m = {"name": "ecc", "source": "git", "url": "https://github.com/affaan-m/ECC.git", "installLocation": "/home/mosjin/x"}
        self.assertEqual(
            marketplaces.whitelist_marketplace(m),
            {"name": "ecc", "source": "git", "url": "https://github.com/affaan-m/ECC.git"},
        )

    def test_install_location_never_kept(self):
        m = {"name": "caveman", "source": "github", "repo": "JuliusBrussee/caveman", "installLocation": "C:\\Users\\mosjin\\x"}
        blob = json.dumps(marketplaces.whitelist_marketplace(m))
        self.assertNotIn("installLocation", blob)
        self.assertNotIn("mosjin", blob)

    def test_unknown_source_keeps_name_and_source_only(self):
        """A marketplace added from a local path (or any future source
        type) has nothing portable to record — but name+source still lets
        `apply` explain why it can't auto-add this one, instead of the
        marketplace silently vanishing from the snapshot."""
        m = {"name": "my-local", "source": "directory", "installLocation": "/home/mosjin/skills/my-local"}
        self.assertEqual(marketplaces.whitelist_marketplace(m), {"name": "my-local", "source": "directory"})

    def test_missing_name_returns_none(self):
        self.assertIsNone(marketplaces.whitelist_marketplace({"source": "github", "repo": "x/y"}))

    def test_missing_source_returns_none(self):
        self.assertIsNone(marketplaces.whitelist_marketplace({"name": "x"}))


class TestListMarketplaces(unittest.TestCase):
    def test_returns_parsed_list(self):
        payload = json.dumps([{"name": "caveman", "source": "github", "repo": "a/b", "installLocation": "/x"}])
        with patch("plugin_sync.claude_cli.run_claude", return_value=(0, payload, "")) as mock:
            result = marketplaces.list_marketplaces()
        mock.assert_called_once_with(["plugin", "marketplace", "list", "--json"])
        self.assertEqual(result[0]["name"], "caveman")

    def test_cli_failure_exits(self):
        with patch("plugin_sync.claude_cli.run_claude", return_value=(1, "", "boom")):
            with self.assertRaises(SystemExit):
                marketplaces.list_marketplaces()

    def test_non_list_response_exits(self):
        with patch("plugin_sync.claude_cli.run_claude", return_value=(0, "{}", "")):
            with self.assertRaises(SystemExit):
                marketplaces.list_marketplaces()


class TestScanSkills(unittest.TestCase):
    def test_finds_skill_dirs_with_skill_md(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "skills" / "api-design").mkdir(parents=True)
            (root / "skills" / "api-design" / "SKILL.md").write_text("x")
            result = snapshots.scan_skills(root, owner="user")
        self.assertEqual(result, [{"name": "api-design", "owner": "user"}])

    def test_ignores_dirs_without_skill_md(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "skills" / "not-a-skill").mkdir(parents=True)
            result = snapshots.scan_skills(root, owner="user")
        self.assertEqual(result, [])

    def test_missing_skills_dir_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = snapshots.scan_skills(Path(tmp), owner="user")
        self.assertEqual(result, [])

    def test_no_path_leaks_in_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "skills" / "s1").mkdir(parents=True)
            (root / "skills" / "s1" / "SKILL.md").write_text("x")
            result = snapshots.scan_skills(root, owner="user")
        self.assertNotIn("path", result[0])
        self.assertEqual(set(result[0].keys()), {"name", "owner"})


class TestDiscoverSkillRoots(unittest.TestCase):
    def test_includes_user_project_and_plugin_roots(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            cwd = Path(tmp) / "proj"
            home.mkdir()
            cwd.mkdir()
            with patch("pathlib.Path.home", return_value=home):
                with patch("pathlib.Path.cwd", return_value=cwd):
                    with patch.dict("os.environ", {}, clear=True):
                        roots = snapshots.discover_skill_roots([SAMPLE_PLUGINS[0]])
        owners = {owner for _, owner in roots}
        self.assertIn("user", owners)
        self.assertIn("project", owners)
        self.assertIn("plugin:caveman@caveman", owners)

    def test_config_dir_replaces_default_home_claude(self):
        """CLAUDE_CONFIG_DIR replaces ~/.claude for Claude Code itself — this
        must not scan both, or it reports skills Claude Code never loads."""
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            custom = Path(tmp) / "custom-config"
            home.mkdir()
            with patch("pathlib.Path.home", return_value=home):
                with patch("pathlib.Path.cwd", return_value=Path(tmp) / "elsewhere"):
                    with patch.dict("os.environ", {"CLAUDE_CONFIG_DIR": str(custom)}, clear=True):
                        roots = snapshots.discover_skill_roots([])
        user_roots = [r for r in roots if r[1] == "user"]
        self.assertEqual(len(user_roots), 1)
        self.assertEqual(user_roots[0][0], custom)

    def test_dedupes_same_path_different_case(self):
        """Windows paths that differ only in case are the same directory —
        must not be scanned (and reported) twice."""
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            home.mkdir()
            lower = str(home / ".claude").lower()
            upper = str(home / ".claude").upper()
            with patch("pathlib.Path.home", return_value=home):
                with patch("pathlib.Path.cwd", return_value=Path(tmp) / "elsewhere"):
                    with patch.dict("os.environ", {}, clear=True):
                        roots = snapshots.discover_skill_roots([
                            {"id": "a@a", "installPath": lower},
                            {"id": "a@a", "installPath": upper},
                        ])
        plugin_roots = [r for r in roots if r[1] == "plugin:a@a"]
        expected = 1 if os.path.normcase("A") != "A" else 2
        self.assertEqual(len(plugin_roots), expected)


class TestCaptureSnapshot(unittest.TestCase):
    def test_builds_full_schema(self):
        with patch("plugin_sync.snapshots.discover_skill_roots", return_value=[]):
            with patch("plugin_sync.marketplaces.list_marketplaces", return_value=[]):
                snapshot = snapshots.capture_snapshot("mosjin", "win-desktop", SAMPLE_PLUGINS)
        self.assertEqual(snapshot["schema_version"], snapshots.SNAPSHOT_SCHEMA_VERSION)
        self.assertEqual(snapshot["kind"], "snapshot")
        self.assertEqual(snapshot["identity"], "mosjin")
        self.assertEqual(snapshot["machine"], "win-desktop")
        self.assertEqual(snapshot["platform"], sys.platform)
        self.assertEqual(len(snapshot["plugins"]), 3)
        self.assertEqual(snapshot["skills"], [])
        self.assertEqual(snapshot["marketplaces"], [])

    def test_aggregates_skills_across_roots(self):
        fake_roots = [("root-a", "user"), ("root-b", "plugin:caveman@caveman")]
        with patch("plugin_sync.snapshots.discover_skill_roots", return_value=fake_roots):
            with patch(
                "plugin_sync.snapshots.scan_skills",
                side_effect=[
                    [{"name": "s1", "owner": "user"}],
                    [{"name": "s2", "owner": "plugin:caveman@caveman"}],
                ],
            ):
                with patch("plugin_sync.marketplaces.list_marketplaces", return_value=[]):
                    snapshot = snapshots.capture_snapshot("mosjin", "win-desktop", SAMPLE_PLUGINS)
        self.assertEqual(len(snapshot["skills"]), 2)

    def test_captures_whitelisted_marketplaces(self):
        raw = [
            {"name": "caveman", "source": "github", "repo": "JuliusBrussee/caveman", "installLocation": "C:\\Users\\mosjin\\.claude\\plugins\\marketplaces\\caveman"},
            {"name": "ecc", "source": "git", "url": "https://github.com/affaan-m/ECC.git", "installLocation": "/home/mosjin/.claude/plugins/marketplaces/ecc"},
        ]
        with patch("plugin_sync.snapshots.discover_skill_roots", return_value=[]):
            with patch("plugin_sync.marketplaces.list_marketplaces", return_value=raw):
                snapshot = snapshots.capture_snapshot("mosjin", "win-desktop", SAMPLE_PLUGINS)
        self.assertEqual(snapshot["marketplaces"], [
            {"name": "caveman", "source": "github", "repo": "JuliusBrussee/caveman"},
            {"name": "ecc", "source": "git", "url": "https://github.com/affaan-m/ECC.git"},
        ])
        self.assertNotIn("mosjin", json.dumps(snapshot["marketplaces"]))


class TestSnapshotFilename(unittest.TestCase):
    def _snapshot(self):
        return {"identity": "mo-sjin", "machine": "win-desktop", "platform": "win32",
                "captured_at": "2026-09-16"}

    def test_includes_identity_machine_platform_date(self):
        name = snapshots.snapshot_filename(self._snapshot())
        self.assertTrue(name.startswith("mo-sjin__win-desktop__win32__2026-09-16__"))
        self.assertTrue(name.endswith(".json"))

    def test_no_precise_time_in_filename(self):
        """Only the coarsened date may appear — a precise time-of-day
        would fingerprint the exact thing captured_at was coarsened to
        hide."""
        name = snapshots.snapshot_filename(self._snapshot())
        self.assertNotRegex(name, r"\d{2}:\d{2}|T\d{6}")

    def test_two_calls_produce_different_filenames(self):
        """Same-day saves for the same machine must not collide/overwrite —
        uniqueness comes from a random token, not a timestamp."""
        first = snapshots.snapshot_filename(self._snapshot())
        second = snapshots.snapshot_filename(self._snapshot())
        self.assertNotEqual(first, second)


class TestCmdSave(unittest.TestCase):
    def _args(self, identity="mosjin", machine="win-desktop", dir_=None):
        class Args:
            pass
        a = Args()
        a.identity = identity
        a.machine = machine
        a.dir = dir_
        return a

    def test_writes_snapshot_file_to_custom_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp) / "snaps"
            with patch("plugin_sync.claude_cli.list_plugins", return_value=SAMPLE_PLUGINS):
                with patch("plugin_sync.snapshots.discover_skill_roots", return_value=[]):
                    with patch("plugin_sync.marketplaces.list_marketplaces", return_value=[]):
                        with patch("sys.stdout", new_callable=StringIO):
                            snapshots.cmd_save(self._args(dir_=str(out_dir)))
            files = list(out_dir.glob("*.json"))
            self.assertEqual(len(files), 1)
            data = json.loads(files[0].read_text(encoding="utf-8"))
            self.assertEqual(data["identity"], "mosjin")
            self.assertEqual(len(data["plugins"]), 3)

    def test_no_sync_warning_only_on_default_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp) / "snaps"
            with patch("plugin_sync.claude_cli.list_plugins", return_value=SAMPLE_PLUGINS):
                with patch("plugin_sync.snapshots.discover_skill_roots", return_value=[]):
                    with patch("plugin_sync.marketplaces.list_marketplaces", return_value=[]):
                        with patch("sys.stdout", new_callable=StringIO) as mock_out:
                            snapshots.cmd_save(self._args(dir_=str(out_dir)))
                        output = mock_out.getvalue()
        self.assertNotIn("Warning", output)

    def test_warns_when_using_default_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            cwd = os.getcwd()
            os.chdir(tmp)
            try:
                with patch("plugin_sync.claude_cli.list_plugins", return_value=SAMPLE_PLUGINS):
                    with patch("plugin_sync.snapshots.discover_skill_roots", return_value=[]):
                        with patch("plugin_sync.marketplaces.list_marketplaces", return_value=[]):
                            with patch.dict("os.environ", {}, clear=True):
                                with patch("sys.stdout", new_callable=StringIO) as mock_out:
                                    snapshots.cmd_save(self._args(dir_=None))
                                output = mock_out.getvalue()
            finally:
                os.chdir(cwd)
        self.assertIn("Warning", output)
        self.assertIn("local to this machine only", output)

    def test_leaky_install_path_never_hits_disk(self):
        """T2: the on-disk artifact itself must be clean, not just the
        in-memory record — this is the end-to-end guard for the feature's
        entire threat model."""
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp) / "snaps"
            with patch("plugin_sync.claude_cli.list_plugins", return_value=[LEAKY_PLUGIN]):
                with patch("plugin_sync.snapshots.discover_skill_roots", return_value=[]):
                    with patch("plugin_sync.marketplaces.list_marketplaces", return_value=[]):
                        with patch("sys.stdout", new_callable=StringIO):
                            snapshots.cmd_save(self._args(identity="tester", dir_=str(out_dir)))
            written = (out_dir / os.listdir(out_dir)[0]).read_text(encoding="utf-8")
        self.assertNotIn(LEAKY_PLUGIN["installPath"], written)
        self.assertNotIn("mosjin", written)  # the username baked into LEAKY_PLUGIN's path


class TestLoadSnapshots(unittest.TestCase):
    def _write(self, dir_path, name, data):
        (dir_path / name).write_text(json.dumps(data), encoding="utf-8")

    def test_missing_dir_exits(self):
        with self.assertRaises(SystemExit):
            merge_mod.load_snapshots(Path("no/such/dir"))

    def test_empty_dir_exits(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SystemExit):
                merge_mod.load_snapshots(Path(tmp))

    def test_schema_version_mismatch_exits(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write(Path(tmp), "a.json", {"schema_version": 999})
            with self.assertRaises(SystemExit):
                merge_mod.load_snapshots(Path(tmp))

    def test_loads_multiple_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = {"schema_version": 1, "kind": "snapshot", "identity": "mosjin", "machine": "a",
                    "platform": "win32", "captured_at": "2026-09-16", "plugins": [], "skills": []}
            self._write(Path(tmp), "a.json", base)
            self._write(Path(tmp), "b.json", dict(base, machine="b"))
            result = merge_mod.load_snapshots(Path(tmp))
        self.assertEqual(len(result), 2)

    def test_non_dict_json_exits_cleanly(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write(Path(tmp), "a.json", ["not", "a", "dict"])
            with self.assertRaises(SystemExit):
                merge_mod.load_snapshots(Path(tmp))

    def test_skips_merged_output_sitting_in_same_dir(self):
        """A `merge --out` result living in the snapshot dir must not be
        fed back in as an input — that's how re-merging the same dir would
        otherwise crash (missing 'machine' etc)."""
        with tempfile.TemporaryDirectory() as tmp:
            base = {"schema_version": 1, "kind": "snapshot", "identity": "mosjin", "machine": "a",
                    "platform": "win32", "captured_at": "2026-09-16", "plugins": [], "skills": []}
            self._write(Path(tmp), "a.json", base)
            self._write(Path(tmp), "merged.json", {"schema_version": 1, "kind": "merged", "identity": "mosjin"})
            result = merge_mod.load_snapshots(Path(tmp))
        self.assertEqual(len(result), 1)


def _snap(machine, platform, captured_at, plugins, skills, identity="mosjin"):
    return {
        "schema_version": 1, "kind": "snapshot", "identity": identity, "machine": machine,
        "platform": platform, "captured_at": captured_at, "plugins": plugins, "skills": skills,
    }


def _plugin(pid, version, scope="user", enabled=True):
    return {"id": pid, "version": version, "scope": scope, "enabled": enabled, "mcp_server_names": []}


SNAP_MACHINE_A = _snap(
    "win-desktop", "win32", "2026-09-16",
    [_plugin("caveman@caveman", "v1")],
    [{"name": "api-design", "owner": "user"}],
)
SNAP_MACHINE_B = _snap(
    "linux-box", "linux", "2026-09-15",
    [_plugin("caveman@caveman", "v2"), _plugin("ecc@ecc", "2.0.0")],
    [],
)


class TestMergeSnapshots(unittest.TestCase):
    def test_empty_input_raises(self):
        with self.assertRaises(ValueError):
            merge_mod.merge_snapshots([])

    def test_mismatched_identity_raises(self):
        other = dict(SNAP_MACHINE_B, identity="someone-else")
        with self.assertRaises(ValueError):
            merge_mod.merge_snapshots([SNAP_MACHINE_A, other])

    def test_missing_required_field_raises_valueerror_not_keyerror(self):
        broken = {k: v for k, v in SNAP_MACHINE_A.items() if k != "captured_at"}
        with self.assertRaises(ValueError):
            merge_mod.merge_snapshots([broken])

    def test_non_dict_snapshot_raises_valueerror(self):
        with self.assertRaises(ValueError):
            merge_mod.merge_snapshots(["not a dict"])

    def test_no_process_exit_on_bad_input(self):
        """merge_snapshots must stay pure — ValueError, never SystemExit."""
        with self.assertRaises(ValueError):
            merge_mod.merge_snapshots([])

    def test_kind_marker_set(self):
        merged = merge_mod.merge_snapshots([SNAP_MACHINE_A, SNAP_MACHINE_B])
        self.assertEqual(merged["kind"], "merged")

    def test_version_drift_detected(self):
        merged = merge_mod.merge_snapshots([SNAP_MACHINE_A, SNAP_MACHINE_B])
        self.assertIn("version", merged["plugins"]["caveman@caveman"]["drift"])

    def test_no_drift_when_all_machines_agree(self):
        merged = merge_mod.merge_snapshots([SNAP_MACHINE_A, SNAP_MACHINE_B])
        self.assertEqual(merged["plugins"]["ecc@ecc"]["drift"], [])

    def test_scope_and_enabled_drift_detected(self):
        a = _snap("m-a", "win32", "2026-09-16", [_plugin("x@x", "1.0", scope="user", enabled=True)], [])
        b = _snap("m-b", "linux", "2026-09-16", [_plugin("x@x", "1.0", scope="local", enabled=False)], [])
        merged = merge_mod.merge_snapshots([a, b])
        self.assertEqual(set(merged["plugins"]["x@x"]["drift"]), {"scope", "enabled"})

    def test_plugin_present_only_on_one_machine(self):
        merged = merge_mod.merge_snapshots([SNAP_MACHINE_A, SNAP_MACHINE_B])
        ecc_machines = set(merged["plugins"]["ecc@ecc"]["present_on"])
        self.assertEqual(ecc_machines, {"linux-box@linux"})

    def test_skills_merged_across_machines(self):
        merged = merge_mod.merge_snapshots([SNAP_MACHINE_A, SNAP_MACHINE_B])
        self.assertIn("user/api-design", merged["skills"])

    def test_snapshot_without_marketplaces_field_still_merges(self):
        """SNAP_MACHINE_A/B predate this field — old snapshots (or ones
        just fetched from a gist saved before this feature) must not
        crash merge."""
        merged = merge_mod.merge_snapshots([SNAP_MACHINE_A, SNAP_MACHINE_B])
        self.assertEqual(merged["marketplaces"], {})

    def test_marketplaces_merged_across_machines(self):
        a = dict(SNAP_MACHINE_A, marketplaces=[{"name": "caveman", "source": "github", "repo": "a/b"}])
        b = dict(SNAP_MACHINE_B, marketplaces=[{"name": "caveman", "source": "github", "repo": "a/b"}])
        merged = merge_mod.merge_snapshots([a, b])
        entry = merged["marketplaces"]["caveman"]
        self.assertEqual(set(entry["present_on"]), {"win-desktop@win32", "linux-box@linux"})
        self.assertEqual(entry["drift"], [])
        self.assertEqual(entry["source_record"], {"source": "github", "repo": "a/b"})

    def test_marketplace_source_drift_detected(self):
        """Two machines recording different sources for the same
        marketplace name is a real conflict `apply` needs to know about,
        not silently averaged away."""
        a = dict(SNAP_MACHINE_A, marketplaces=[{"name": "caveman", "source": "github", "repo": "old/repo"}])
        b = dict(SNAP_MACHINE_B, marketplaces=[{"name": "caveman", "source": "github", "repo": "new/repo"}])
        merged = merge_mod.merge_snapshots([a, b])
        self.assertEqual(merged["marketplaces"]["caveman"]["drift"], ["source"])

    def test_idempotent_same_input_same_output(self):
        first = merge_mod.merge_snapshots([SNAP_MACHINE_A, SNAP_MACHINE_B])
        second = merge_mod.merge_snapshots([SNAP_MACHINE_A, SNAP_MACHINE_B])
        self.assertEqual(first, second)

    def test_inputs_not_mutated(self):
        before = json.dumps(SNAP_MACHINE_A)
        merge_mod.merge_snapshots([SNAP_MACHINE_A, SNAP_MACHINE_B])
        self.assertEqual(json.dumps(SNAP_MACHINE_A), before)

    def test_two_snapshots_same_machine_latest_date_wins_wholesale(self):
        """T3: a plugin uninstalled and a skill deleted between an old save
        and a new one for the SAME machine must both disappear — merge
        picks one snapshot per machine wholesale, never unions two of a
        machine's own snapshots field-by-field."""
        old = _snap(
            "win-desktop", "win32", "2026-01-01",
            [_plugin("caveman@caveman", "OLD"), _plugin("gone@gone", "1.0")],
            [{"name": "stale-skill", "owner": "user"}],
        )
        new = _snap(
            "win-desktop", "win32", "2026-09-16",
            [_plugin("caveman@caveman", "NEW")],
            [],
        )
        merged_forward = merge_mod.merge_snapshots([old, new])
        merged_reversed = merge_mod.merge_snapshots([new, old])
        for merged in (merged_forward, merged_reversed):
            self.assertEqual(
                merged["plugins"]["caveman@caveman"]["present_on"]["win-desktop@win32"]["version"],
                "NEW",
            )
            self.assertNotIn("gone@gone", merged["plugins"])
            self.assertNotIn("user/stale-skill", merged["skills"])

    def test_same_day_tie_is_deterministic_and_noted(self):
        a = _snap("win-desktop", "win32", "2026-09-16", [_plugin("x@x", "first")], [])
        b = _snap("win-desktop", "win32", "2026-09-16", [_plugin("x@x", "second")], [])
        merged = merge_mod.merge_snapshots([a, b])
        self.assertEqual(
            merged["plugins"]["x@x"]["present_on"]["win-desktop@win32"]["version"], "second"
        )
        self.assertTrue(any("multiple snapshots dated" in note for note in merged["notes"]))


class TestCmdMerge(unittest.TestCase):
    def _args(self, dir_, out=None):
        class Args:
            pass
        a = Args()
        a.dir = dir_
        a.out = out
        return a

    def test_prints_drift_and_missing_machines(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "a.json").write_text(json.dumps(SNAP_MACHINE_A), encoding="utf-8")
            (Path(tmp) / "b.json").write_text(json.dumps(SNAP_MACHINE_B), encoding="utf-8")
            with patch("sys.stdout", new_callable=StringIO) as mock_out:
                merge_mod.cmd_merge(self._args(tmp))
            output = mock_out.getvalue()
        self.assertIn("version drift", output)
        self.assertIn("missing on", output)

    def test_writes_merged_json_when_out_given(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "a.json").write_text(json.dumps(SNAP_MACHINE_A), encoding="utf-8")
            out_path = Path(tmp) / "merged.json"
            with patch("sys.stdout", new_callable=StringIO):
                merge_mod.cmd_merge(self._args(tmp, out=str(out_path)))
            self.assertTrue(out_path.exists())
            data = json.loads(out_path.read_text(encoding="utf-8"))
            self.assertEqual(data["identity"], "mosjin")

    def test_bad_input_exits_cleanly_not_traceback(self):
        with tempfile.TemporaryDirectory() as tmp:
            other = dict(SNAP_MACHINE_B, identity="someone-else")
            (Path(tmp) / "a.json").write_text(json.dumps(SNAP_MACHINE_A), encoding="utf-8")
            (Path(tmp) / "b.json").write_text(json.dumps(other), encoding="utf-8")
            with patch("sys.stdout", new_callable=StringIO):
                with self.assertRaises(SystemExit):
                    merge_mod.cmd_merge(self._args(tmp))

    def test_out_creates_missing_parent_dirs(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "a.json").write_text(json.dumps(SNAP_MACHINE_A), encoding="utf-8")
            out_path = Path(tmp) / "reports" / "merged.json"
            with patch("sys.stdout", new_callable=StringIO):
                merge_mod.cmd_merge(self._args(tmp, out=str(out_path)))
            self.assertTrue(out_path.exists())

    def test_merge_out_into_snapshot_dir_then_remerge_does_not_crash(self):
        """T4/#4: `--out` writing into the same dir it read from must not
        poison that dir for the next `merge` run."""
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "a.json").write_text(json.dumps(SNAP_MACHINE_A), encoding="utf-8")
            out_path = Path(tmp) / "merged.json"
            with patch("sys.stdout", new_callable=StringIO):
                merge_mod.cmd_merge(self._args(tmp, out=str(out_path)))
                merge_mod.cmd_merge(self._args(tmp))  # re-merge same dir


class TestRunGh(unittest.TestCase):
    def test_finds_gh_executable(self):
        with patch("plugin_sync.gist.shutil.which", return_value="/usr/bin/gh"):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = type("R", (), {"returncode": 0, "stdout": "ok", "stderr": ""})()
                code, out, err = gist.run_gh(["gist", "list"])
        self.assertEqual(code, 0)
        args = mock_run.call_args[0][0]
        self.assertEqual(args[0], "/usr/bin/gh")
        self.assertIn("gist", args)

    def test_exits_if_gh_not_in_path(self):
        with patch("plugin_sync.gist.shutil.which", return_value=None):
            with self.assertRaises(SystemExit):
                gist.run_gh(["gist", "list"])


VALID_SNAPSHOT_CONTENT = json.dumps({
    "schema_version": 1, "kind": "snapshot", "identity": "mosjin", "machine": "m",
    "platform": "win32", "captured_at": "2026-09-16", "plugins": [], "skills": [],
})


class TestCleanGhError(unittest.TestCase):
    def test_prefers_err_when_present(self):
        self.assertEqual(gist._clean_gh_error("stdout stuff", "real error", 1), "real error (exit 1)")

    def test_falls_back_to_out_when_err_is_whitespace_only(self):
        self.assertEqual(gist._clean_gh_error("useful message", "  \n", 1), "useful message (exit 1)")

    def test_no_output_at_all(self):
        self.assertEqual(gist._clean_gh_error("", "", 1), "no output (exit 1)")


class TestIsValidSnapshotFile(unittest.TestCase):
    def test_valid_snapshot_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "a.json"
            p.write_text(VALID_SNAPSHOT_CONTENT, encoding="utf-8")
            self.assertTrue(gist._is_valid_snapshot_file(p))

    def test_merged_kind_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "a.json"
            p.write_text(json.dumps({"schema_version": 1, "kind": "merged"}), encoding="utf-8")
            self.assertFalse(gist._is_valid_snapshot_file(p))

    def test_garbage_json_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "a.json"
            p.write_text("{}", encoding="utf-8")
            self.assertFalse(gist._is_valid_snapshot_file(p))

    def test_non_json_rejected_not_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "a.json"
            p.write_text("not json at all", encoding="utf-8")
            self.assertFalse(gist._is_valid_snapshot_file(p))


class TestResolveGistId(unittest.TestCase):
    def _args(self, gist_id=None):
        class Args:
            pass
        a = Args()
        a.gist_id = gist_id
        return a

    def test_uses_flag(self):
        self.assertEqual(gist.resolve_gist_id(self._args("abc123")), "abc123")

    def test_falls_back_to_env(self):
        with patch.dict("os.environ", {gist.ENV_GIST_ID: "env-gist"}, clear=False):
            self.assertEqual(gist.resolve_gist_id(self._args(None)), "env-gist")

    def test_falls_back_to_dir_marker_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            snapshot_dir = Path(tmp)
            (snapshot_dir / ".gist_id").write_text("cached-id-123", encoding="utf-8")
            with patch.dict("os.environ", {}, clear=True):
                result = gist.resolve_gist_id(self._args(None), snapshot_dir)
        self.assertEqual(result, "cached-id-123")

    def test_flag_wins_over_marker_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            snapshot_dir = Path(tmp)
            (snapshot_dir / ".gist_id").write_text("cached-id", encoding="utf-8")
            result = gist.resolve_gist_id(self._args("flag-id"), snapshot_dir)
        self.assertEqual(result, "flag-id")

    def test_missing_everywhere_returns_none(self):
        """Unlike identity/machine, no gist id is a VALID state for upload
        (it means: create a new gist) — must not exit. Zero auto-discovery
        matches falls all the way through to None, same as before."""
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict("os.environ", {}, clear=True):
                with patch("plugin_sync.gist.list_snapshot_gists", return_value=[]):
                    self.assertIsNone(gist.resolve_gist_id(self._args(None), Path(tmp)))

    def test_auto_discovers_single_match(self):
        """The core UX fix: a second machine with no cached id, no env var,
        and no --gist-id flag should still find the one gist this tool
        created, with zero typing."""
        with tempfile.TemporaryDirectory() as tmp:
            gists = [{"id": "only-one", "description": gist.SNAPSHOT_GIST_DESCRIPTION, "files": "1 file", "visibility": "secret", "updated_at": "2026-09-17T00:00:00Z"}]
            with patch.dict("os.environ", {}, clear=True):
                with patch("plugin_sync.gist.list_snapshot_gists", return_value=gists):
                    with patch("sys.stdout", new_callable=StringIO):
                        result = gist.resolve_gist_id(self._args(None), Path(tmp))
            self.assertEqual(result, "only-one")
            self.assertEqual((Path(tmp) / ".gist_id").read_text(encoding="utf-8"), "only-one")

    def test_auto_discovery_ambiguous_multiple_matches_exits(self):
        """Two or more candidates must error instead of guessing — silently
        picking one (or letting `upload` create a third gist) would
        fragment the "one shared gist" model this tool assumes."""
        with tempfile.TemporaryDirectory() as tmp:
            gists = [
                {"id": "first", "description": gist.SNAPSHOT_GIST_DESCRIPTION, "files": "1 file", "visibility": "secret", "updated_at": "t"},
                {"id": "second", "description": gist.SNAPSHOT_GIST_DESCRIPTION, "files": "1 file", "visibility": "secret", "updated_at": "t"},
            ]
            with patch.dict("os.environ", {}, clear=True):
                with patch("plugin_sync.gist.list_snapshot_gists", return_value=gists):
                    with self.assertRaises(SystemExit) as ctx:
                        gist.resolve_gist_id(self._args(None), Path(tmp))
        self.assertIn("gist-list", str(ctx.exception))


class TestExtractGistId(unittest.TestCase):
    def test_finds_url_anywhere_in_output(self):
        out = "Some preamble line\nhttps://gist.github.com/mosjin/5b0e0062eb8e9654adad7bb1d81cc75f\nmore text\n"
        self.assertEqual(gist._extract_gist_id(out), "5b0e0062eb8e9654adad7bb1d81cc75f")

    def test_no_url_present_exits_loudly(self):
        """The bug this replaces: silently returning '' on unparseable
        output instead of failing where the mistake is easy to notice."""
        with self.assertRaises(SystemExit):
            gist._extract_gist_id("no url in here at all")


SAMPLE_GIST_LIST_OUTPUT = (
    "1cd6200ecc8a99f2cf03d59954bda507\tclaude-plugin-skill-sync snapshots\t1 file\tsecret\t2026-09-17T00:34:47Z\n"
    "cf3ea934043827ca541e97d238cb1804\tsome other gist\t2 files\tpublic\t2026-04-27T15:13:31Z\n"
)


class TestParseGistList(unittest.TestCase):
    def test_parses_tsv_rows(self):
        result = gist.parse_gist_list(SAMPLE_GIST_LIST_OUTPUT)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0], {
            "id": "1cd6200ecc8a99f2cf03d59954bda507",
            "description": "claude-plugin-skill-sync snapshots",
            "files": "1 file",
            "visibility": "secret",
            "updated_at": "2026-09-17T00:34:47Z",
        })

    def test_empty_output_returns_empty_list(self):
        self.assertEqual(gist.parse_gist_list(""), [])

    def test_malformed_line_skipped_not_crash(self):
        result = gist.parse_gist_list("not-enough-columns\there\n")
        self.assertEqual(result, [])


class TestListSnapshotGists(unittest.TestCase):
    def test_filters_by_description_via_gh_flag(self):
        with patch("plugin_sync.gist.run_gh", return_value=(0, SAMPLE_GIST_LIST_OUTPUT, "")) as mock:
            gist.list_snapshot_gists()
        args = mock.call_args[0][0]
        self.assertIn("--filter", args)
        self.assertEqual(args[args.index("--filter") + 1], gist.SNAPSHOT_GIST_DESCRIPTION)

    def test_cli_failure_exits(self):
        with patch("plugin_sync.gist.run_gh", return_value=(1, "", "auth error")):
            with self.assertRaises(SystemExit) as ctx:
                gist.list_snapshot_gists()
        self.assertIn("auth error", str(ctx.exception))


class TestCmdGistList(unittest.TestCase):
    def test_no_gists_found_prints_hint(self):
        with patch("plugin_sync.gist.list_snapshot_gists", return_value=[]):
            with patch("sys.stdout", new_callable=StringIO) as mock_out:
                gist.cmd_gist_list(None)
                output = mock_out.getvalue()
        self.assertIn("No gists tagged", output)

    def test_lists_gist_ids_and_usage_hint(self):
        gists = [{"id": "abc123", "description": gist.SNAPSHOT_GIST_DESCRIPTION, "files": "1 file", "visibility": "secret", "updated_at": "2026-09-17T00:00:00Z"}]
        with patch("plugin_sync.gist.list_snapshot_gists", return_value=gists):
            with patch("sys.stdout", new_callable=StringIO) as mock_out:
                gist.cmd_gist_list(None)
                output = mock_out.getvalue()
        self.assertIn("abc123", output)
        self.assertIn("1 gist found", output)
        self.assertIn("fetch --gist-id", output)


class TestCmdUpload(unittest.TestCase):
    def _args(self, file, gist_id=None, dir_=None, keep_history=False):
        class Args:
            pass
        a = Args()
        a.file = file
        a.gist_id = gist_id
        a.dir = dir_
        a.keep_history = keep_history
        return a

    def _write_snapshot(self, tmp, name="a.json"):
        p = Path(tmp) / name
        p.write_text(VALID_SNAPSHOT_CONTENT, encoding="utf-8")
        return p

    def test_missing_file_exits(self):
        with self.assertRaises(SystemExit):
            gist.cmd_upload(self._args("no/such/file.json"))

    def test_non_snapshot_file_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            merged = Path(tmp) / "merged.json"
            merged.write_text(json.dumps({"schema_version": 1, "kind": "merged"}), encoding="utf-8")
            with self.assertRaises(SystemExit):
                gist.cmd_upload(self._args(str(merged), gist_id="x"))

    def test_adds_to_existing_gist_exact_argv_no_public_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            snap = self._write_snapshot(tmp)
            with patch("plugin_sync.gist.list_gist_files", return_value=[]):
                with patch("plugin_sync.gist.run_gh", return_value=(0, "", "")) as mock:
                    with patch("sys.stdout", new_callable=StringIO):
                        gist.cmd_upload(self._args(str(snap), gist_id="existing123", dir_=tmp))
        mock.assert_called_once_with(["gist", "edit", "existing123", "--add", str(snap)])
        self.assertNotIn("--public", mock.call_args[0][0])

    def test_creates_new_gist_exact_argv_and_caches_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            snap = self._write_snapshot(tmp)
            with patch("plugin_sync.gist.list_snapshot_gists", return_value=[]):
                with patch(
                    "plugin_sync.gist.run_gh",
                    return_value=(0, "https://gist.github.com/mosjin/aabbcc1122\n", ""),
                ) as mock:
                    with patch("sys.stdout", new_callable=StringIO) as mock_out:
                        gist.cmd_upload(self._args(str(snap), dir_=tmp))
                        output = mock_out.getvalue()
            mock.assert_called_once_with(["gist", "create", str(snap), "-d", "claude-plugin-skill-sync snapshots"])
            self.assertNotIn("--public", mock.call_args[0][0])
            self.assertIn("aabbcc1122", output)
            self.assertEqual((Path(tmp) / ".gist_id").read_text(encoding="utf-8"), "aabbcc1122")

    def test_second_upload_reuses_cached_id_without_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / ".gist_id").write_text("cached999", encoding="utf-8")
            snap = self._write_snapshot(tmp, "b.json")
            with patch("plugin_sync.gist.list_gist_files", return_value=[]):
                with patch("plugin_sync.gist.run_gh", return_value=(0, "", "")) as mock:
                    with patch("sys.stdout", new_callable=StringIO):
                        gist.cmd_upload(self._args(str(snap), dir_=tmp))
        mock.assert_called_once_with(["gist", "edit", "cached999", "--add", str(snap)])

    def test_gh_failure_exits_with_real_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            snap = self._write_snapshot(tmp)
            with patch("plugin_sync.gist.run_gh", return_value=(1, "", "not found")):
                with self.assertRaises(SystemExit) as ctx:
                    gist.cmd_upload(self._args(str(snap), gist_id="bad-id", dir_=tmp))
        self.assertIn("not found", str(ctx.exception))

    def test_removes_this_machines_stale_snapshots_after_adding(self):
        """Default behavior: uploading again from the same machine
        overwrites (adds the new file, then removes the old one(s))
        instead of accumulating one file per save forever. Add must
        happen first — a gist can't have zero files, so removing the
        last remaining file before the replacement exists would fail
        GitHub's own validation (verified live)."""
        with tempfile.TemporaryDirectory() as tmp:
            snap = self._write_snapshot(tmp, "new.json")
            gist_files = [
                "mosjin__m__win32__2026-09-01__aaaa1111.json",   # stale, same machine
                "mosjin__other-machine__linux__2026-09-01__bb.json",  # different machine
                "new.json",  # the file about to be added itself
            ]
            with patch("plugin_sync.gist.list_gist_files", return_value=gist_files):
                with patch("plugin_sync.gist.run_gh", return_value=(0, "", "")) as mock:
                    with patch("sys.stdout", new_callable=StringIO) as mock_out:
                        gist.cmd_upload(self._args(str(snap), gist_id="g1", dir_=tmp))
                        output = mock_out.getvalue()
        calls = [c[0][0] for c in mock.call_args_list]
        self.assertEqual(calls[0], ["gist", "edit", "g1", "--add", str(snap)])
        self.assertIn(["gist", "edit", "g1", "--remove", "mosjin__m__win32__2026-09-01__aaaa1111.json"], calls)
        self.assertNotIn(["gist", "edit", "g1", "--remove", "mosjin__other-machine__linux__2026-09-01__bb.json"], calls)
        self.assertIn("Removed superseded snapshot", output)

    def test_keep_history_skips_cleanup(self):
        with tempfile.TemporaryDirectory() as tmp:
            snap = self._write_snapshot(tmp, "new.json")
            with patch("plugin_sync.gist.list_gist_files") as mock_list:
                with patch("plugin_sync.gist.run_gh", return_value=(0, "", "")) as mock:
                    with patch("sys.stdout", new_callable=StringIO):
                        gist.cmd_upload(self._args(str(snap), gist_id="g1", dir_=tmp, keep_history=True))
        mock_list.assert_not_called()
        mock.assert_called_once_with(["gist", "edit", "g1", "--add", str(snap)])

    def test_remove_failure_exits_with_real_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            snap = self._write_snapshot(tmp, "new.json")
            with patch("plugin_sync.gist.list_gist_files", return_value=["mosjin__m__win32__old__x.json"]):
                with patch("plugin_sync.gist.run_gh", side_effect=[
                    (0, "", ""),                     # add succeeds
                    (1, "", "remove failed"),         # remove fails
                ]):
                    with self.assertRaises(SystemExit) as ctx:
                        gist.cmd_upload(self._args(str(snap), gist_id="g1", dir_=tmp))
        self.assertIn("remove failed", str(ctx.exception))


class TestListGistFiles(unittest.TestCase):
    def test_parses_one_filename_per_line(self):
        with patch("plugin_sync.gist.run_gh", return_value=(0, "a.json\nb.json\n", "")) as mock:
            result = gist.list_gist_files("g1")
        mock.assert_called_once_with(["gist", "view", "g1", "--files"])
        self.assertEqual(result, ["a.json", "b.json"])

    def test_blank_lines_dropped(self):
        with patch("plugin_sync.gist.run_gh", return_value=(0, "a.json\n\n\nb.json\n", "")):
            result = gist.list_gist_files("g1")
        self.assertEqual(result, ["a.json", "b.json"])

    def test_cli_failure_exits(self):
        with patch("plugin_sync.gist.run_gh", return_value=(1, "", "boom")):
            with self.assertRaises(SystemExit):
                gist.list_gist_files("g1")


class TestFindStaleGistSnapshots(unittest.TestCase):
    def test_matches_same_machine_prefix_only(self):
        snapshot = {"identity": "mosjin", "machine": "m", "platform": "win32"}
        files = [
            "mosjin__m__win32__2026-09-01__aaaa.json",
            "mosjin__other__win32__2026-09-01__bbbb.json",
            "someone-else__m__win32__2026-09-01__cccc.json",
        ]
        result = gist.find_stale_gist_snapshots(files, snapshot, "mosjin__m__win32__2026-09-17__new.json")
        self.assertEqual(result, ["mosjin__m__win32__2026-09-01__aaaa.json"])

    def test_excludes_the_new_filename_itself(self):
        snapshot = {"identity": "mosjin", "machine": "m", "platform": "win32"}
        new_name = "mosjin__m__win32__2026-09-17__new.json"
        result = gist.find_stale_gist_snapshots([new_name], snapshot, new_name)
        self.assertEqual(result, [])

    def test_no_files_returns_empty(self):
        snapshot = {"identity": "mosjin", "machine": "m", "platform": "win32"}
        self.assertEqual(gist.find_stale_gist_snapshots([], snapshot, "x.json"), [])


class TestCmdFetch(unittest.TestCase):
    def _args(self, gist_id=None, dir_=None):
        class Args:
            pass
        a = Args()
        a.gist_id = gist_id
        a.dir = dir_
        return a

    def test_missing_gist_id_exits(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict("os.environ", {}, clear=True):
                with patch("plugin_sync.gist.list_snapshot_gists", return_value=[]):
                    with self.assertRaises(SystemExit) as ctx:
                        gist.cmd_fetch(self._args(dir_=str(Path(tmp) / "snaps")))
        self.assertIn("gist id required", str(ctx.exception))

    def test_clone_failure_exits_with_real_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch("plugin_sync.gist.run_gh", return_value=(1, "", "gist not found")):
                with self.assertRaises(SystemExit) as ctx:
                    gist.cmd_fetch(self._args(gist_id="abc", dir_=str(Path(tmp) / "snaps")))
        self.assertIn("gist not found", str(ctx.exception))

    def test_fetch_with_no_gist_id_auto_discovers_single_match(self):
        """The second-machine happy path this feature exists for: no
        --gist-id, no env var, no cached marker — but exactly one gist
        tagged by this tool exists, so fetch must still work."""
        gists = [{"id": "auto-found", "description": gist.SNAPSHOT_GIST_DESCRIPTION, "files": "1 file", "visibility": "secret", "updated_at": "t"}]
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp) / "snaps"

            def fake_run_gh(args):
                if args[:2] == ["gist", "clone"]:
                    self.assertEqual(args[2], "auto-found")
                    clone_dir = Path(args[3])
                    clone_dir.mkdir(parents=True, exist_ok=True)
                    return (0, "", "")
                raise AssertionError(f"unexpected run_gh call: {args}")

            with patch.dict("os.environ", {}, clear=True):
                with patch("plugin_sync.gist.list_snapshot_gists", return_value=gists):
                    with patch("plugin_sync.gist.run_gh", side_effect=fake_run_gh):
                        with patch("sys.stdout", new_callable=StringIO) as mock_out:
                            gist.cmd_fetch(self._args(dir_=str(out_dir)))
                            output = mock_out.getvalue()
        self.assertIn("Auto-detected gist auto-found", output)
        self.assertIn("Fetched gist auto-found", output)

    def _fake_clone_with(self, files: dict):
        def fake_clone(args):
            self.assertEqual(args[:3], ["gist", "clone", "abc"])
            clone_dir = Path(args[3])
            clone_dir.mkdir(parents=True, exist_ok=True)
            for name, content in files.items():
                (clone_dir / name).write_text(content, encoding="utf-8")
            return (0, "", "")
        return fake_clone

    def test_copies_new_files_skips_existing(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp) / "snaps"
            out_dir.mkdir()
            (out_dir / "already-here.json").write_text('{"a": 1}', encoding="utf-8")

            fake_clone = self._fake_clone_with({
                "already-here.json": VALID_SNAPSHOT_CONTENT,
                "new-one.json": VALID_SNAPSHOT_CONTENT,
            })
            with patch("plugin_sync.gist.run_gh", side_effect=fake_clone):
                with patch("sys.stdout", new_callable=StringIO) as mock_out:
                    gist.cmd_fetch(self._args(gist_id="abc", dir_=str(out_dir)))
                    output = mock_out.getvalue()

            self.assertTrue((out_dir / "new-one.json").exists())
            # local copy must not be clobbered by a same-named remote file
            self.assertEqual((out_dir / "already-here.json").read_text(encoding="utf-8"), '{"a": 1}')
        self.assertIn("2 snapshot(s) found, 1 new one(s)", output)

    def test_skips_invalid_files_reports_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp) / "snaps"
            fake_clone = self._fake_clone_with({
                "good.json": VALID_SNAPSHOT_CONTENT,
                "bad.json": json.dumps({"schema_version": 1, "kind": "merged"}),
            })
            with patch("plugin_sync.gist.run_gh", side_effect=fake_clone):
                with patch("sys.stdout", new_callable=StringIO) as mock_out:
                    gist.cmd_fetch(self._args(gist_id="abc", dir_=str(out_dir)))
                    output = mock_out.getvalue()
            self.assertTrue((out_dir / "good.json").exists())
            self.assertFalse((out_dir / "bad.json").exists())
        self.assertIn("skipped 1 file(s)", output)

    def test_zero_json_files_in_gist_does_not_claim_already_synced(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp) / "snaps"
            fake_clone = self._fake_clone_with({})
            with patch("plugin_sync.gist.run_gh", side_effect=fake_clone):
                with patch("sys.stdout", new_callable=StringIO) as mock_out:
                    gist.cmd_fetch(self._args(gist_id="abc", dir_=str(out_dir)))
                    output = mock_out.getvalue()
        self.assertIn("0 snapshot(s) found", output)


MERGED_FIXTURE = {
    "schema_version": 1, "kind": "merged", "identity": "mosjin",
    "machines": {
        "linux-box@linux": {"machine": "linux-box", "platform": "linux", "captured_at": "2026-09-16"},
    },
    "plugins": {
        "caveman@caveman": {"present_on": {"linux-box@linux": {"version": "1.0", "scope": "user", "enabled": True}}, "drift": []},
        "unknown@no-such-marketplace": {"present_on": {"linux-box@linux": {"version": "1.0", "scope": "user", "enabled": True}}, "drift": []},
    },
    "skills": {},
    "notes": [],
}


class TestMarketplaceInstallLocations(unittest.TestCase):
    def test_parses_name_to_path(self):
        payload = json.dumps([{"name": "caveman", "installLocation": "/home/user/.claude/plugins/marketplaces/caveman"}])
        with patch("plugin_sync.claude_cli.run_claude", return_value=(0, payload, "")):
            result = marketplaces.marketplace_install_locations()
        self.assertEqual(result["caveman"], Path("/home/user/.claude/plugins/marketplaces/caveman"))

    def test_cli_failure_exits(self):
        with patch("plugin_sync.claude_cli.run_claude", return_value=(1, "", "boom")):
            with self.assertRaises(SystemExit):
                marketplaces.marketplace_install_locations()

    def test_entries_missing_fields_skipped_not_crash(self):
        payload = json.dumps([{"name": "no-location"}, {"installLocation": "/x"}])
        with patch("plugin_sync.claude_cli.run_claude", return_value=(0, payload, "")):
            result = marketplaces.marketplace_install_locations()
        self.assertEqual(result, {})


class TestPluginDescription(unittest.TestCase):
    def test_reads_description_from_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            loc = Path(tmp)
            (loc / ".claude-plugin").mkdir()
            manifest = {"plugins": [{"name": "caveman", "description": "terse mode"}]}
            (loc / ".claude-plugin" / "marketplace.json").write_text(json.dumps(manifest), encoding="utf-8")
            result = marketplaces.plugin_description("caveman@caveman", {"caveman": loc})
        self.assertEqual(result, "terse mode")

    def test_marketplace_not_available_returns_none(self):
        self.assertIsNone(marketplaces.plugin_description("x@unknown", {}))

    def test_missing_manifest_file_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = marketplaces.plugin_description("x@m", {"m": Path(tmp)})
        self.assertIsNone(result)

    def test_plugin_not_listed_in_manifest_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            loc = Path(tmp)
            (loc / ".claude-plugin").mkdir()
            (loc / ".claude-plugin" / "marketplace.json").write_text(
                json.dumps({"plugins": [{"name": "other"}]}), encoding="utf-8"
            )
            result = marketplaces.plugin_description("x@m", {"m": loc})
        self.assertIsNone(result)

    def test_malformed_manifest_json_returns_none_not_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            loc = Path(tmp)
            (loc / ".claude-plugin").mkdir()
            (loc / ".claude-plugin" / "marketplace.json").write_text("not json", encoding="utf-8")
            result = marketplaces.plugin_description("x@m", {"m": loc})
        self.assertIsNone(result)


class TestLoadMerged(unittest.TestCase):
    def test_missing_file_exits(self):
        with self.assertRaises(SystemExit):
            apply_mod.load_merged(Path("no/such/file.json"))

    def test_wrong_kind_exits(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "a.json"
            p.write_text(VALID_SNAPSHOT_CONTENT, encoding="utf-8")  # kind="snapshot", not "merged"
            with self.assertRaises(SystemExit):
                apply_mod.load_merged(p)

    def test_valid_merged_file_loads(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "merged.json"
            p.write_text(json.dumps(MERGED_FIXTURE), encoding="utf-8")
            result = apply_mod.load_merged(p)
        self.assertEqual(result["identity"], "mosjin")


class TestMissingPluginIds(unittest.TestCase):
    def test_returns_ids_not_locally_installed(self):
        result = apply_mod.missing_plugin_ids(MERGED_FIXTURE, {"ecc@ecc"})
        self.assertEqual(result, ["caveman@caveman", "unknown@no-such-marketplace"])

    def test_already_installed_excluded(self):
        result = apply_mod.missing_plugin_ids(MERGED_FIXTURE, {"caveman@caveman", "unknown@no-such-marketplace"})
        self.assertEqual(result, [])


class TestAddableMarketplaceSource(unittest.TestCase):
    def test_github_record_returns_repo(self):
        self.assertEqual(
            marketplaces.addable_marketplace_source({"source": "github", "repo": "a/b"}),
            "a/b",
        )

    def test_git_record_returns_url(self):
        self.assertEqual(
            marketplaces.addable_marketplace_source({"source": "git", "url": "https://x/y.git"}),
            "https://x/y.git",
        )

    def test_unknown_source_returns_none(self):
        self.assertIsNone(marketplaces.addable_marketplace_source({"source": "directory"}))

    def test_empty_record_returns_none(self):
        self.assertIsNone(marketplaces.addable_marketplace_source({}))


class TestClassifyMissingPlugins(unittest.TestCase):
    def test_local_marketplace_wins_over_merged_source(self):
        """Already-local beats "would need to add" even if the merged view
        also has a portable source for it — no point re-adding what's
        already here."""
        merged_marketplaces = {"caveman": {"source_record": {"source": "github", "repo": "a/b"}}}
        installable, addable, skipped, sources = marketplaces.classify_missing_plugins(
            ["caveman@caveman"], {"caveman": Path("/local")}, merged_marketplaces
        )
        self.assertEqual(installable, ["caveman@caveman"])
        self.assertEqual(addable, [])

    def test_portable_source_goes_to_addable(self):
        merged_marketplaces = {"caveman": {"source_record": {"source": "github", "repo": "a/b"}}}
        installable, addable, skipped, sources = marketplaces.classify_missing_plugins(
            ["caveman@caveman"], {}, merged_marketplaces
        )
        self.assertEqual(addable, ["caveman@caveman"])
        self.assertEqual(sources, {"caveman": "a/b"})

    def test_no_source_on_record_goes_to_skipped(self):
        installable, addable, skipped, sources = marketplaces.classify_missing_plugins(
            ["unknown@no-such-marketplace"], {}, {}
        )
        self.assertEqual(skipped, ["unknown@no-such-marketplace"])

    def test_local_path_source_goes_to_skipped_not_addable(self):
        """A marketplace whose only recorded source is a local directory
        on the machine that saved the snapshot has nothing portable to
        auto-add here."""
        merged_marketplaces = {"my-local": {"source_record": {"source": "directory"}}}
        installable, addable, skipped, sources = marketplaces.classify_missing_plugins(
            ["x@my-local"], {}, merged_marketplaces
        )
        self.assertEqual(skipped, ["x@my-local"])
        self.assertEqual(addable, [])


MERGED_FIXTURE_WITH_ADDABLE = dict(MERGED_FIXTURE, marketplaces={
    "caveman": {"source_record": {"source": "github", "repo": "JuliusBrussee/caveman"}},
})


class TestLangMessagesParity(unittest.TestCase):
    def test_en_and_zh_have_identical_keys(self):
        """_msg() does table[key] — a key present in one language dict but
        not the other is a KeyError the moment a user passes --lang zh (or
        omits it) after a key gets added to only one side, as almost
        happened when addable_header/adding_marketplace were added."""
        self.assertEqual(
            set(apply_mod.LANG_MESSAGES["en"]),
            set(apply_mod.LANG_MESSAGES["zh"]),
        )


class TestCmdApply(unittest.TestCase):
    def _args(self, merged_file, plugins=None, all_=False, yes=False, scope=None, lang=None):
        class Args:
            pass
        a = Args()
        a.merged_file = merged_file
        a.plugins = plugins or []
        a.all = all_
        a.yes = yes
        a.scope = scope
        a.lang = lang
        return a

    def _write_merged(self, tmp):
        p = Path(tmp) / "merged.json"
        p.write_text(json.dumps(MERGED_FIXTURE), encoding="utf-8")
        return p

    def test_nothing_missing_prints_and_returns(self):
        with tempfile.TemporaryDirectory() as tmp:
            merged = self._write_merged(tmp)
            with patch("plugin_sync.claude_cli.list_plugins", return_value=[{"id": "caveman@caveman"}, {"id": "unknown@no-such-marketplace"}]):
                with patch("sys.stdout", new_callable=StringIO) as mock_out:
                    apply_mod.cmd_apply(self._args(str(merged)))
                    output = mock_out.getvalue()
        self.assertIn("Nothing missing", output)

    def test_dry_run_lists_installable_and_skipped_with_description(self):
        with tempfile.TemporaryDirectory() as tmp:
            merged = self._write_merged(tmp)
            loc = Path(tmp) / "mp"
            (loc / ".claude-plugin").mkdir(parents=True)
            (loc / ".claude-plugin" / "marketplace.json").write_text(
                json.dumps({"plugins": [{"name": "caveman", "description": "terse mode"}]}), encoding="utf-8"
            )
            with patch("plugin_sync.claude_cli.list_plugins", return_value=[]):
                with patch("plugin_sync.claude_cli.run_claude", return_value=(0, json.dumps([{"name": "caveman", "installLocation": str(loc)}]), "")):
                    with patch("sys.stdout", new_callable=StringIO) as mock_out:
                        apply_mod.cmd_apply(self._args(str(merged), all_=True))
                        output = mock_out.getvalue()
        self.assertIn("caveman@caveman", output)
        self.assertIn("terse mode", output)
        self.assertIn("unknown@no-such-marketplace", output)  # listed under skipped
        self.assertIn("Dry run", output)

    def test_yes_without_scope_exits(self):
        with tempfile.TemporaryDirectory() as tmp:
            merged = self._write_merged(tmp)
            loc = Path(tmp) / "mp"
            loc.mkdir()
            with patch("plugin_sync.claude_cli.list_plugins", return_value=[]):
                with patch("plugin_sync.claude_cli.run_claude", return_value=(0, json.dumps([{"name": "caveman", "installLocation": str(loc)}]), "")):
                    with patch("sys.stdout", new_callable=StringIO):
                        with self.assertRaises(SystemExit):
                            apply_mod.cmd_apply(self._args(str(merged), all_=True, yes=True))

    def test_all_with_yes_installs_and_reports(self):
        with tempfile.TemporaryDirectory() as tmp:
            merged = self._write_merged(tmp)
            loc = Path(tmp) / "mp"
            loc.mkdir()
            with patch("plugin_sync.claude_cli.list_plugins", return_value=[]):
                with patch("plugin_sync.claude_cli.run_claude", side_effect=[
                    (0, json.dumps([{"name": "caveman", "installLocation": str(loc)}]), ""),  # marketplace list
                    (0, "installed", ""),  # plugin install
                ]) as mock_claude:
                    with patch("sys.stdout", new_callable=StringIO) as mock_out:
                        apply_mod.cmd_apply(self._args(str(merged), all_=True, yes=True, scope="user"))
                        output = mock_out.getvalue()
        install_call = mock_claude.call_args_list[-1][0][0]
        self.assertEqual(install_call, ["plugin", "install", "caveman@caveman", "-s", "user", "-y"])
        self.assertIn("Installed: 1", output)

    def test_install_failure_counted_and_exits_nonzero(self):
        with tempfile.TemporaryDirectory() as tmp:
            merged = self._write_merged(tmp)
            loc = Path(tmp) / "mp"
            loc.mkdir()
            with patch("plugin_sync.claude_cli.list_plugins", return_value=[]):
                with patch("plugin_sync.claude_cli.run_claude", side_effect=[
                    (0, json.dumps([{"name": "caveman", "installLocation": str(loc)}]), ""),
                    (1, "", "install failed"),
                ]):
                    with patch("sys.stdout", new_callable=StringIO) as mock_out:
                        with self.assertRaises(SystemExit):
                            apply_mod.cmd_apply(self._args(str(merged), all_=True, yes=True, scope="user"))
                        output = mock_out.getvalue()
        self.assertIn("Failed: 1", output)

    def test_specific_plugin_name_not_in_installable_exits(self):
        with tempfile.TemporaryDirectory() as tmp:
            merged = self._write_merged(tmp)
            loc = Path(tmp) / "mp"
            loc.mkdir()
            with patch("plugin_sync.claude_cli.list_plugins", return_value=[]):
                with patch("plugin_sync.claude_cli.run_claude", return_value=(0, json.dumps([{"name": "caveman", "installLocation": str(loc)}]), "")):
                    with patch("sys.stdout", new_callable=StringIO):
                        with self.assertRaises(SystemExit):
                            apply_mod.cmd_apply(self._args(str(merged), plugins=["nope@nope"], yes=True, scope="user"))

    def test_interactive_all_selection(self):
        with tempfile.TemporaryDirectory() as tmp:
            merged = self._write_merged(tmp)
            loc = Path(tmp) / "mp"
            loc.mkdir()
            with patch("plugin_sync.claude_cli.list_plugins", return_value=[]):
                with patch("plugin_sync.claude_cli.run_claude", return_value=(0, json.dumps([{"name": "caveman", "installLocation": str(loc)}]), "")):
                    with patch("builtins.input", return_value="all"):
                        with patch("sys.stdout", new_callable=StringIO) as mock_out:
                            apply_mod.cmd_apply(self._args(str(merged)))  # no plugins/all/yes -> dry run after selection
                            output = mock_out.getvalue()
        self.assertIn("Dry run", output)

    def test_interactive_none_selection_installs_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            merged = self._write_merged(tmp)
            loc = Path(tmp) / "mp"
            loc.mkdir()
            with patch("plugin_sync.claude_cli.list_plugins", return_value=[]):
                with patch("plugin_sync.claude_cli.run_claude", return_value=(0, json.dumps([{"name": "caveman", "installLocation": str(loc)}]), "")):
                    with patch("builtins.input", return_value="none"):
                        with patch("sys.stdout", new_callable=StringIO) as mock_out:
                            apply_mod.cmd_apply(self._args(str(merged)))
                            output = mock_out.getvalue()
        self.assertNotIn("Dry run", output)  # returned before reaching the dry-run note

    def test_lang_zh_switches_messages(self):
        with tempfile.TemporaryDirectory() as tmp:
            merged = self._write_merged(tmp)
            with patch("plugin_sync.claude_cli.list_plugins", return_value=[{"id": "caveman@caveman"}, {"id": "unknown@no-such-marketplace"}]):
                with patch("sys.stdout", new_callable=StringIO) as mock_out:
                    apply_mod.cmd_apply(self._args(str(merged), lang="zh"))
                    output = mock_out.getvalue()
        self.assertIn("没有缺的插件", output)

    def _write_addable_merged(self, tmp):
        p = Path(tmp) / "merged.json"
        p.write_text(json.dumps(MERGED_FIXTURE_WITH_ADDABLE), encoding="utf-8")
        return p

    def test_dry_run_shows_addable_without_calling_marketplace_add(self):
        """Preview must never mutate machine state — adding a marketplace
        is a real side effect, gated behind -y like everything else."""
        with tempfile.TemporaryDirectory() as tmp:
            merged = self._write_addable_merged(tmp)
            with patch("plugin_sync.claude_cli.list_plugins", return_value=[]):
                with patch("plugin_sync.claude_cli.run_claude", return_value=(0, "[]", "")) as mock:
                    with patch("sys.stdout", new_callable=StringIO) as mock_out:
                        apply_mod.cmd_apply(self._args(str(merged), all_=True))
                        output = mock_out.getvalue()
        self.assertIn("caveman@caveman", output)
        self.assertIn("JuliusBrussee/caveman", output)
        self.assertIn("Dry run", output)
        calls = [c[0][0] for c in mock.call_args_list]
        self.assertTrue(all(c[:3] != ["plugin", "marketplace", "add"] for c in calls))

    def test_yes_adds_marketplace_then_installs(self):
        with tempfile.TemporaryDirectory() as tmp:
            merged = self._write_addable_merged(tmp)
            with patch("plugin_sync.claude_cli.list_plugins", return_value=[]):
                with patch("plugin_sync.claude_cli.run_claude", side_effect=[
                    (0, "[]", ""),                       # marketplace list (none local)
                    (0, "added", ""),                    # marketplace add
                    (0, "installed", ""),                # plugin install
                ]) as mock:
                    with patch("sys.stdout", new_callable=StringIO) as mock_out:
                        apply_mod.cmd_apply(self._args(str(merged), all_=True, yes=True, scope="user"))
                        output = mock_out.getvalue()
        calls = [c[0][0] for c in mock.call_args_list]
        self.assertEqual(calls[1], ["plugin", "marketplace", "add", "JuliusBrussee/caveman", "--scope", "user"])
        self.assertEqual(calls[2], ["plugin", "install", "caveman@caveman", "-s", "user", "-y"])
        self.assertIn("Installed: 1", output)

    def test_marketplace_add_failure_skips_its_plugins_not_others(self):
        merged_fixture = dict(MERGED_FIXTURE_WITH_ADDABLE)
        merged_fixture["plugins"] = dict(MERGED_FIXTURE["plugins"])
        # give unknown@no-such-marketplace an addable source too, distinct
        # from caveman's, so a failed add for one doesn't block the other
        merged_fixture["marketplaces"] = dict(
            MERGED_FIXTURE_WITH_ADDABLE["marketplaces"],
            **{"no-such-marketplace": {"source_record": {"source": "github", "repo": "x/y"}}},
        )
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "merged.json"
            p.write_text(json.dumps(merged_fixture), encoding="utf-8")
            with patch("plugin_sync.claude_cli.list_plugins", return_value=[]):
                with patch("plugin_sync.claude_cli.run_claude", side_effect=[
                    (0, "[]", ""),                                # marketplace list
                    (1, "", "add failed: not found"),             # add caveman -> fails
                    (0, "added", ""),                             # add no-such-marketplace -> succeeds
                    (0, "installed", ""),                         # install unknown@no-such-marketplace
                ]):
                    with patch("sys.stdout", new_callable=StringIO) as mock_out:
                        with self.assertRaises(SystemExit):
                            apply_mod.cmd_apply(self._args(str(p), all_=True, yes=True, scope="user"))
                        output = mock_out.getvalue()
        self.assertIn("add failed: not found", output)
        self.assertIn("Installed: 1", output)
        self.assertIn("Failed: 1", output)

    def test_dedups_marketplace_add_across_two_plugins_same_marketplace(self):
        merged_fixture = dict(MERGED_FIXTURE_WITH_ADDABLE)
        merged_fixture["plugins"] = {
            "caveman@caveman": MERGED_FIXTURE["plugins"]["caveman@caveman"],
            "other@caveman": MERGED_FIXTURE["plugins"]["caveman@caveman"],
        }
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "merged.json"
            p.write_text(json.dumps(merged_fixture), encoding="utf-8")
            with patch("plugin_sync.claude_cli.list_plugins", return_value=[]):
                with patch("plugin_sync.claude_cli.run_claude", side_effect=[
                    (0, "[]", ""),
                    (0, "added", ""),
                    (0, "installed", ""),
                    (0, "installed", ""),
                ]) as mock:
                    with patch("sys.stdout", new_callable=StringIO):
                        apply_mod.cmd_apply(self._args(str(p), all_=True, yes=True, scope="user"))
        add_calls = [c[0][0] for c in mock.call_args_list if c[0][0][:3] == ["plugin", "marketplace", "add"]]
        self.assertEqual(len(add_calls), 1)


if __name__ == "__main__":
    unittest.main()
