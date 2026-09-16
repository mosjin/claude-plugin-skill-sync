"""Tests for plugin_manager.py — all subprocess calls are mocked."""

import json
import sys
import unittest
from io import StringIO
from unittest.mock import patch, call

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))
import plugin_manager


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
        with patch("plugin_manager.run_claude", return_value=(0, json.dumps(SAMPLE_PLUGINS), "")) as mock:
            result = plugin_manager.list_plugins()
        mock.assert_called_once_with(["plugin", "list", "--json"])
        self.assertEqual(len(result), 3)
        self.assertEqual(result[0]["id"], "caveman@caveman")

    def test_exits_on_cli_error(self):
        with patch("plugin_manager.run_claude", return_value=(1, "", "some error")):
            with self.assertRaises(SystemExit):
                plugin_manager.list_plugins()


class TestResolvePlugins(unittest.TestCase):
    def test_exact_id_match(self):
        result = plugin_manager.resolve_plugins(["caveman@caveman"], SAMPLE_PLUGINS)
        self.assertEqual(result[0]["id"], "caveman@caveman")

    def test_partial_name_match(self):
        result = plugin_manager.resolve_plugins(["caveman"], SAMPLE_PLUGINS)
        self.assertEqual(result[0]["id"], "caveman@caveman")

    def test_multiple_names(self):
        result = plugin_manager.resolve_plugins(["caveman", "ecc"], SAMPLE_PLUGINS)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["id"], "caveman@caveman")
        self.assertEqual(result[1]["id"], "ecc@ecc")

    def test_not_found_exits(self):
        with self.assertRaises(SystemExit):
            plugin_manager.resolve_plugins(["nonexistent"], SAMPLE_PLUGINS)

    def test_empty_names_returns_empty(self):
        result = plugin_manager.resolve_plugins([], SAMPLE_PLUGINS)
        self.assertEqual(result, [])


class TestUpdateOne(unittest.TestCase):
    """update_one is now a thin subprocess wrapper — it no longer guesses
    status from stdout wording. That's resolve_update_status's job, tested
    below against real before/after version state.
    """

    def test_returns_raw_code_and_message(self):
        with patch("plugin_manager.run_claude", return_value=(0, "Plugin updated successfully", "")):
            result = plugin_manager.update_one(SAMPLE_PLUGINS[0])
        self.assertEqual(result["id"], "caveman@caveman")
        self.assertEqual(result["code"], 0)
        self.assertIn("updated successfully", result["message"])

    def test_nonzero_code_preserved(self):
        with patch("plugin_manager.run_claude", return_value=(1, "", "Failed to update: network error")):
            result = plugin_manager.update_one(SAMPLE_PLUGINS[0])
        self.assertEqual(result["code"], 1)
        self.assertIn("network error", result["message"])

    def test_calls_with_full_id(self):
        with patch("plugin_manager.run_claude", return_value=(0, "updated", "")) as mock:
            plugin_manager.update_one(SAMPLE_PLUGINS[0])
        mock.assert_called_once_with(["plugin", "update", "caveman@caveman", "-s", "user"])

    def test_passes_plugin_own_scope(self):
        """A local/project-scope plugin must not default to -s user; the
        CLI rejects update/uninstall at the wrong scope."""
        local_plugin = dict(SAMPLE_PLUGINS[0], scope="local")
        with patch("plugin_manager.run_claude", return_value=(0, "updated", "")) as mock:
            plugin_manager.update_one(local_plugin)
        mock.assert_called_once_with(["plugin", "update", "caveman@caveman", "-s", "local"])


class TestResolveUpdateStatus(unittest.TestCase):
    """The version-diff replacement for the old regex-on-stdout guess."""

    def test_version_changed_is_updated(self):
        status = plugin_manager.resolve_update_status("1.0.0", "1.1.0", code=0)
        self.assertEqual(status, "updated")

    def test_version_unchanged_is_current(self):
        status = plugin_manager.resolve_update_status("1.0.0", "1.0.0", code=0)
        self.assertEqual(status, "current")

    def test_nonzero_exit_is_failed_even_if_version_changed(self):
        status = plugin_manager.resolve_update_status("1.0.0", "1.1.0", code=1)
        self.assertEqual(status, "failed")

    def test_missing_after_version_is_failed(self):
        """Plugin vanished from the post-update list (e.g. it got removed) —
        cannot claim success without evidence the new version exists."""
        status = plugin_manager.resolve_update_status("1.0.0", None, code=0)
        self.assertEqual(status, "failed")

    def test_success_message_but_unchanged_version_is_not_updated(self):
        """The bug this replaces: a zero exit code alone used to be read
        as success regardless of whether anything actually changed."""
        status = plugin_manager.resolve_update_status("655b7d9c5431", "655b7d9c5431", code=0)
        self.assertEqual(status, "current")


class TestCmdList(unittest.TestCase):
    def test_output_contains_plugin_names(self):
        with patch("plugin_manager.list_plugins", return_value=SAMPLE_PLUGINS):
            with patch("sys.stdout", new_callable=StringIO) as mock_out:
                plugin_manager.cmd_list(None)
                output = mock_out.getvalue()
        self.assertIn("caveman", output)
        self.assertIn("ecc", output)
        self.assertIn("context7", output)

    def test_output_shows_disabled_status(self):
        with patch("plugin_manager.list_plugins", return_value=SAMPLE_PLUGINS):
            with patch("sys.stdout", new_callable=StringIO) as mock_out:
                plugin_manager.cmd_list(None)
                output = mock_out.getvalue()
        self.assertIn("✗", output)  # context7 is disabled

    def test_output_shows_plugin_count(self):
        with patch("plugin_manager.list_plugins", return_value=SAMPLE_PLUGINS):
            with patch("sys.stdout", new_callable=StringIO) as mock_out:
                plugin_manager.cmd_list(None)
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
        with patch("plugin_manager.list_plugins", side_effect=[SAMPLE_PLUGINS, after]):
            with patch("plugin_manager.update_one", return_value={"id": "caveman@caveman", "code": 0, "message": "ok"}) as mock_update:
                with patch("sys.stdout", new_callable=StringIO):
                    plugin_manager.cmd_update(self._make_args(plugins=["caveman"]))
        mock_update.assert_called_once()
        self.assertEqual(mock_update.call_args[0][0]["id"], "caveman@caveman")

    def test_update_all(self):
        with patch("plugin_manager.list_plugins", side_effect=[SAMPLE_PLUGINS, SAMPLE_PLUGINS]):
            with patch("plugin_manager.update_one", return_value={"id": "x", "code": 0, "message": "ok"}) as mock_update:
                with patch("sys.stdout", new_callable=StringIO):
                    plugin_manager.cmd_update(self._make_args(all_=True))
        self.assertEqual(mock_update.call_count, 3)

    def test_update_all_continues_on_failure(self):
        """One failure must not abort remaining updates."""
        def side_effect(plugin):
            if plugin["id"] == "ecc@ecc":
                return {"id": "ecc@ecc", "code": 1, "message": "network error"}
            return {"id": plugin["id"], "code": 0, "message": "ok"}

        after = [dict(SAMPLE_PLUGINS[0], version="new"), SAMPLE_PLUGINS[1], dict(SAMPLE_PLUGINS[2], version="new")]
        with patch("plugin_manager.list_plugins", side_effect=[SAMPLE_PLUGINS, after]):
            with patch("plugin_manager.update_one", side_effect=side_effect):
                with patch("sys.stdout", new_callable=StringIO):
                    with self.assertRaises(SystemExit) as ctx:
                        plugin_manager.cmd_update(self._make_args(all_=True))
        self.assertEqual(ctx.exception.code, 1)

    def test_summary_shows_counts(self):
        """caveman updates (version changes), ecc is already current
        (version unchanged), context7 fails (nonzero exit)."""
        def side_effect(plugin):
            if plugin["id"] == "context7@claude-plugins-official":
                return {"id": plugin["id"], "code": 1, "message": "error"}
            return {"id": plugin["id"], "code": 0, "message": "ok"}

        after = [dict(SAMPLE_PLUGINS[0], version="new-version"), SAMPLE_PLUGINS[1], SAMPLE_PLUGINS[2]]
        with patch("plugin_manager.list_plugins", side_effect=[SAMPLE_PLUGINS, after]):
            with patch("plugin_manager.update_one", side_effect=side_effect):
                with patch("sys.stdout", new_callable=StringIO) as mock_out:
                    with self.assertRaises(SystemExit):
                        plugin_manager.cmd_update(self._make_args(all_=True))
                    output = mock_out.getvalue()
        self.assertIn("Updated: 1", output)
        self.assertIn("Already current: 1", output)
        self.assertIn("Failed: 1", output)

    def test_updated_plugin_shows_restart_note(self):
        after = [dict(SAMPLE_PLUGINS[0], version="new-version"), SAMPLE_PLUGINS[1], SAMPLE_PLUGINS[2]]
        with patch("plugin_manager.list_plugins", side_effect=[SAMPLE_PLUGINS, after]):
            with patch("plugin_manager.update_one", return_value={"id": "caveman@caveman", "code": 0, "message": "ok"}):
                with patch("sys.stdout", new_callable=StringIO) as mock_out:
                    plugin_manager.cmd_update(self._make_args(plugins=["caveman"]))
                    output = mock_out.getvalue()
        self.assertIn("restart Claude Code", output)

    def test_vanished_plugin_reports_honest_message_not_success_text(self):
        """code=0 but the plugin is absent from the post-update list must
        not print its own success message next to a ✗ — that reads as a
        contradiction."""
        after = [SAMPLE_PLUGINS[1], SAMPLE_PLUGINS[2]]  # caveman missing
        with patch("plugin_manager.list_plugins", side_effect=[SAMPLE_PLUGINS, after]):
            with patch("plugin_manager.update_one", return_value={"id": "caveman@caveman", "code": 0, "message": "Plugin updated successfully"}):
                with patch("sys.stdout", new_callable=StringIO) as mock_out:
                    with self.assertRaises(SystemExit):
                        plugin_manager.cmd_update(self._make_args(plugins=["caveman"]))
                    output = mock_out.getvalue()
        self.assertNotIn("Plugin updated successfully", output)
        self.assertIn("no longer listed", output)

    def test_results_line_shows_before_after_version(self):
        """The complaint this fixes: per-item 'done' during the run says
        nothing about whether a version actually changed. The results
        block must spell out before → after so it's unambiguous."""
        after = [dict(SAMPLE_PLUGINS[0], version="new-version"), SAMPLE_PLUGINS[1], SAMPLE_PLUGINS[2]]
        with patch("plugin_manager.list_plugins", side_effect=[SAMPLE_PLUGINS, after]):
            with patch("plugin_manager.update_one", return_value={"id": "caveman@caveman", "code": 0, "message": "ok"}):
                with patch("sys.stdout", new_callable=StringIO) as mock_out:
                    plugin_manager.cmd_update(self._make_args(plugins=["caveman"]))
                    output = mock_out.getvalue()
        self.assertIn("655b7d9c5431 → new-version", output)

    def test_results_line_shows_unchanged_version_when_current(self):
        with patch("plugin_manager.list_plugins", side_effect=[SAMPLE_PLUGINS, SAMPLE_PLUGINS]):
            with patch("plugin_manager.update_one", return_value={"id": "caveman@caveman", "code": 0, "message": "already current"}):
                with patch("sys.stdout", new_callable=StringIO) as mock_out:
                    plugin_manager.cmd_update(self._make_args(plugins=["caveman"]))
                    output = mock_out.getvalue()
        self.assertIn("655b7d9c5431 (unchanged)", output)

    def test_no_restart_note_when_nothing_updated(self):
        with patch("plugin_manager.list_plugins", side_effect=[SAMPLE_PLUGINS, SAMPLE_PLUGINS]):
            with patch("plugin_manager.update_one", return_value={"id": "caveman@caveman", "code": 0, "message": "already current"}):
                with patch("sys.stdout", new_callable=StringIO) as mock_out:
                    plugin_manager.cmd_update(self._make_args(plugins=["caveman"]))
                    output = mock_out.getvalue()
        self.assertNotIn("restart Claude Code", output)

    def test_no_plugins_and_no_all_exits(self):
        """Calling update with no args should error."""
        with patch("plugin_manager.list_plugins", return_value=SAMPLE_PLUGINS):
            with patch("sys.stdout", new_callable=StringIO):
                with self.assertRaises(SystemExit):
                    plugin_manager.cmd_update(self._make_args(plugins=[], all_=False))

    def test_parallel_reports_updates_correctly(self):
        """--all --parallel is the README's documented fast path — must be
        covered directly, not just inferred from the sequential branch."""
        after = [dict(SAMPLE_PLUGINS[0], version="new"), dict(SAMPLE_PLUGINS[1], version="new"), SAMPLE_PLUGINS[2]]
        with patch("plugin_manager.list_plugins", side_effect=[SAMPLE_PLUGINS, after]):
            with patch("plugin_manager.update_one", return_value={"id": "placeholder", "code": 0, "message": "ok"}) as mock_update:
                def side_effect(plugin):
                    return {"id": plugin["id"], "code": 0, "message": "ok"}
                mock_update.side_effect = side_effect
                with patch("sys.stdout", new_callable=StringIO) as mock_out:
                    plugin_manager.cmd_update(self._make_args(all_=True, parallel=True))
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
        with patch("plugin_manager.list_plugins", side_effect=[SAMPLE_PLUGINS, after]):
            with patch("plugin_manager.update_one", side_effect=side_effect):
                with patch("sys.stdout", new_callable=StringIO) as mock_out:
                    with self.assertRaises(SystemExit) as ctx:
                        plugin_manager.cmd_update(self._make_args(all_=True, parallel=True))
                    output = mock_out.getvalue()
        self.assertEqual(ctx.exception.code, 1)
        self.assertIn("subprocess exploded", output)
        self.assertIn("✗ ecc@ecc", output)


class TestRunClaude(unittest.TestCase):
    def test_finds_claude_executable(self):
        with patch("plugin_manager.shutil.which", return_value="/usr/bin/claude"):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = type("R", (), {"returncode": 0, "stdout": "[]", "stderr": ""})()
                code, out, err = plugin_manager.run_claude(["plugin", "list", "--json"])
        self.assertEqual(code, 0)
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        self.assertEqual(args[0], "/usr/bin/claude")
        self.assertIn("plugin", args)

    def test_exits_if_claude_not_in_path(self):
        with patch("plugin_manager.shutil.which", return_value=None):
            with self.assertRaises(SystemExit):
                plugin_manager.run_claude(["plugin", "list", "--json"])


class TestUninstallOne(unittest.TestCase):
    def test_successful_uninstall(self):
        with patch("plugin_manager.run_claude", return_value=(0, "Plugin uninstalled successfully", "")):
            result = plugin_manager.uninstall_one(SAMPLE_PLUGINS[0])
        self.assertEqual(result["status"], "uninstalled")
        self.assertEqual(result["id"], "caveman@caveman")

    def test_failed_uninstall(self):
        with patch("plugin_manager.run_claude", return_value=(1, "", "Plugin not found")):
            result = plugin_manager.uninstall_one(SAMPLE_PLUGINS[0])
        self.assertEqual(result["status"], "failed")
        self.assertIn("not found", result["message"])

    def test_calls_with_full_id(self):
        with patch("plugin_manager.run_claude", return_value=(0, "ok", "")) as mock:
            plugin_manager.uninstall_one(SAMPLE_PLUGINS[0])
        args = mock.call_args[0][0]
        self.assertEqual(args[:2], ["plugin", "uninstall"])
        self.assertIn("caveman@caveman", args)

    def test_passes_plugin_own_scope(self):
        """A local/project-scope plugin must not default to -s user; the
        CLI rejects uninstall at the wrong scope."""
        local_plugin = dict(SAMPLE_PLUGINS[0], scope="local")
        with patch("plugin_manager.run_claude", return_value=(0, "ok", "")) as mock:
            plugin_manager.uninstall_one(local_plugin)
        args = mock.call_args[0][0]
        self.assertIn("-s", args)
        self.assertEqual(args[args.index("-s") + 1], "local")

    def test_keep_data_flag(self):
        with patch("plugin_manager.run_claude", return_value=(0, "ok", "")) as mock:
            plugin_manager.uninstall_one(SAMPLE_PLUGINS[0], keep_data=True)
        args = mock.call_args[0][0]
        self.assertIn("--keep-data", args)

    def test_prune_adds_yes(self):
        """--prune must include -y because subprocess is non-TTY."""
        with patch("plugin_manager.run_claude", return_value=(0, "ok", "")) as mock:
            plugin_manager.uninstall_one(SAMPLE_PLUGINS[0], prune=True)
        args = mock.call_args[0][0]
        self.assertIn("--prune", args)
        self.assertIn("-y", args)

    def test_no_extra_flags_by_default(self):
        with patch("plugin_manager.run_claude", return_value=(0, "ok", "")) as mock:
            plugin_manager.uninstall_one(SAMPLE_PLUGINS[0])
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
        with patch("plugin_manager.list_plugins", return_value=SAMPLE_PLUGINS):
            with patch("plugin_manager.uninstall_one", return_value={"id": "caveman@caveman", "status": "uninstalled", "message": "ok"}) as mock:
                with patch("sys.stdout", new_callable=StringIO):
                    plugin_manager.cmd_uninstall(self._make_args(plugins=["caveman"], yes=True))
        mock.assert_called_once()
        self.assertEqual(mock.call_args[0][0]["id"], "caveman@caveman")

    def test_uninstall_prompts_without_yes(self):
        with patch("plugin_manager.list_plugins", return_value=SAMPLE_PLUGINS):
            with patch("plugin_manager.uninstall_one") as mock_uninstall:
                with patch("builtins.input", return_value="n"):
                    with patch("sys.stdout", new_callable=StringIO):
                        plugin_manager.cmd_uninstall(self._make_args(plugins=["caveman"], yes=False))
        mock_uninstall.assert_not_called()

    def test_uninstall_proceeds_on_yes_input(self):
        with patch("plugin_manager.list_plugins", return_value=SAMPLE_PLUGINS):
            with patch("plugin_manager.uninstall_one", return_value={"id": "caveman@caveman", "status": "uninstalled", "message": "ok"}) as mock:
                with patch("builtins.input", return_value="y"):
                    with patch("sys.stdout", new_callable=StringIO):
                        plugin_manager.cmd_uninstall(self._make_args(plugins=["caveman"], yes=False))
        mock.assert_called_once()

    def test_uninstall_multiple(self):
        with patch("plugin_manager.list_plugins", return_value=SAMPLE_PLUGINS):
            with patch("plugin_manager.uninstall_one", return_value={"id": "x", "status": "uninstalled", "message": "ok"}) as mock:
                with patch("sys.stdout", new_callable=StringIO):
                    plugin_manager.cmd_uninstall(self._make_args(plugins=["caveman", "ecc"], yes=True))
        self.assertEqual(mock.call_count, 2)

    def test_failure_exits_nonzero(self):
        def side_effect(plugin, **kw):
            return {"id": plugin["id"], "status": "failed", "message": "error"}

        with patch("plugin_manager.list_plugins", return_value=SAMPLE_PLUGINS):
            with patch("plugin_manager.uninstall_one", side_effect=side_effect):
                with patch("sys.stdout", new_callable=StringIO):
                    with self.assertRaises(SystemExit) as ctx:
                        plugin_manager.cmd_uninstall(self._make_args(plugins=["caveman"], yes=True))
        self.assertEqual(ctx.exception.code, 1)

    def test_passes_keep_data_flag(self):
        with patch("plugin_manager.list_plugins", return_value=SAMPLE_PLUGINS):
            with patch("plugin_manager.uninstall_one", return_value={"id": "caveman@caveman", "status": "uninstalled", "message": "ok"}) as mock:
                with patch("sys.stdout", new_callable=StringIO):
                    plugin_manager.cmd_uninstall(self._make_args(plugins=["caveman"], yes=True, keep_data=True))
        _, kwargs = mock.call_args
        self.assertTrue(kwargs.get("keep_data"))

    def test_passes_prune_flag(self):
        with patch("plugin_manager.list_plugins", return_value=SAMPLE_PLUGINS):
            with patch("plugin_manager.uninstall_one", return_value={"id": "caveman@caveman", "status": "uninstalled", "message": "ok"}) as mock:
                with patch("sys.stdout", new_callable=StringIO):
                    plugin_manager.cmd_uninstall(self._make_args(plugins=["caveman"], yes=True, prune=True))
        _, kwargs = mock.call_args
        self.assertTrue(kwargs.get("prune"))

    def test_no_plugins_exits(self):
        with patch("plugin_manager.list_plugins", return_value=SAMPLE_PLUGINS):
            with self.assertRaises(SystemExit):
                plugin_manager.cmd_uninstall(self._make_args(plugins=[], yes=True))


SAMPLE_MCP_LIST_OUTPUT = """\
claude.ai Gmail: https://gmailmcp.googleapis.com/mcp/v1 - ✔ Connected
plugin:code-graph-mcp:code-graph: node /home/user/.claude/plugins/cache/code-graph-mcp/scripts/mcp-launcher.js - ✔ Connected
plugin:voicemode:voicemode: uv run voicemode - ✘ Failed to connect
firecrawl: npx -y firecrawl-mcp - ✔ Connected
"""


class TestParseMcpList(unittest.TestCase):
    def test_parses_all_lines(self):
        records = plugin_manager.parse_mcp_list(SAMPLE_MCP_LIST_OUTPUT)
        self.assertEqual(len(records), 4)

    def test_splits_name_command_status(self):
        records = plugin_manager.parse_mcp_list(SAMPLE_MCP_LIST_OUTPUT)
        firecrawl = next(r for r in records if r["name"] == "firecrawl")
        self.assertEqual(firecrawl["command"], "npx -y firecrawl-mcp")
        self.assertEqual(firecrawl["status"], "✔ Connected")

    def test_plugin_name_with_embedded_colons_kept_whole(self):
        records = plugin_manager.parse_mcp_list(SAMPLE_MCP_LIST_OUTPUT)
        names = [r["name"] for r in records]
        self.assertIn("plugin:code-graph-mcp:code-graph", names)

    def test_ignores_blank_and_malformed_lines(self):
        records = plugin_manager.parse_mcp_list("\n   \nnot a valid line\n" + SAMPLE_MCP_LIST_OUTPUT)
        self.assertEqual(len(records), 4)


class TestClassifyMcpServer(unittest.TestCase):
    def test_plugin_bundled(self):
        self.assertEqual(plugin_manager.classify_mcp_server("plugin:code-graph-mcp:code-graph"), "plugin")

    def test_host_connector(self):
        self.assertEqual(plugin_manager.classify_mcp_server("claude.ai Gmail"), "host")

    def test_standalone(self):
        self.assertEqual(plugin_manager.classify_mcp_server("firecrawl"), "standalone")


class TestCmdDoctor(unittest.TestCase):
    def test_lists_only_standalone_servers(self):
        with patch("plugin_manager.run_claude", return_value=(0, SAMPLE_MCP_LIST_OUTPUT, "")):
            with patch("sys.stdout", new_callable=StringIO) as mock_out:
                plugin_manager.cmd_doctor(None)
                output = mock_out.getvalue()
        self.assertIn("firecrawl", output)
        self.assertNotIn("plugin:code-graph-mcp", output)
        self.assertNotIn("claude.ai Gmail", output)

    def test_reports_none_when_all_managed(self):
        managed_only = "claude.ai Gmail: https://gmailmcp.googleapis.com/mcp/v1 - ✔ Connected\n"
        with patch("plugin_manager.run_claude", return_value=(0, managed_only, "")):
            with patch("sys.stdout", new_callable=StringIO) as mock_out:
                plugin_manager.cmd_doctor(None)
                output = mock_out.getvalue()
        self.assertIn("none", output)

    def test_exits_on_cli_error(self):
        with patch("plugin_manager.run_claude", return_value=(1, "", "mcp list failed")):
            with self.assertRaises(SystemExit):
                plugin_manager.cmd_doctor(None)


if __name__ == "__main__":
    unittest.main()
