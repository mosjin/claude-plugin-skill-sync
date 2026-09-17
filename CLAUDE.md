<!-- code-graph-mcp:begin v2 -->
## Code Graph (repo-wide AST index)

AST + FTS + vector index of the whole repo — prefer over multi-round Grep/Read for
structural queries (LSP only sees open files; this sees everything). Fastest path = Bash CLI:

| Intent | Command |
|--------|---------|
| Who calls X / what X calls | `code-graph-mcp callgraph X` |
| Impact before editing a fn | `code-graph-mcp impact X` |
| Unfamiliar dir / module | `code-graph-mcp overview <dir>` |
| Symbol source / signature | `code-graph-mcp show X` |
| Concept search (no exact name) | `code-graph-mcp search "…"` (vector: MCP `semantic_code_search`) |
| grep + AST context | `code-graph-mcp grep "pat" [paths] [-t lang] [-g glob] [-c]` |

Not on PATH? A plugin-only install keeps its own copy — same commands, run
`~/.cache/code-graph/bin/code-graph-mcp` (or `npm i -g @sdsrs/code-graph` once).

Still use Grep for literal strings/regex in non-code files; still Read files you'll edit.
Full command + MCP-tool table: `.claude/plugin_code_graph_mcp.md`
<!-- code-graph-mcp:end -->

## README maintenance (this repo)

Follow the global README/docs rule (`~/.claude/CLAUDE.md` rule 9) for every
edit to `README.md`/`README.en.md`. Two things specific to this repo:

- `upload`/`fetch` here talk to a **real** GitHub Gist for the maintainer's
  own account (`snapshots/.gist_id` is already cached, pointing at a real
  gist). When capturing real output for docs, never run `upload` without
  an explicit `--gist-id` pointed at a disposable gist — auto-discovery
  will find and reuse that real one (happened once, 2026-09-17, reverted
  via `gh gist edit --remove`). Prefer capturing `save`/`diff`/`apply`
  output from small local synthetic snapshots instead; only touch the
  real gist for `upload`/`fetch` output when there's no other way, and
  clean up anything added.
- `diff`'s two required steps for `apply` to work are `fetch` (or
  otherwise getting a snapshot file from another machine into the same
  `--dir`) and `diff --out <file>` (its output is `apply`'s first
  argument — cannot be skipped). The destination machine's own `save` is
  the one genuinely optional step: `apply` checks `claude plugin list`
  live, not the snapshot dir.
