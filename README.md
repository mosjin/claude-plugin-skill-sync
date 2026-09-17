# claude-plugin-skill-sync

[中文](#中文) | [English](#english)

---

<a id="中文"></a>
## 中文

### About / 关于本项目

跨平台 Python CLI，管理 Claude Code 插件（plugin），并在多台机器间同步已安装的
插件/技能（skill）清单。核心痛点：一个人常在多台机器（工作本、家用机、服务器）
上用 Claude Code，插件装了哪些、版本是否一致，全靠手动对照。本工具把「装了什么」
做成可保存、可脱敏分享、可合并对比、可一键补装的快照，不依赖任何云端账号体系，
纯本地 JSON 文件 + 可选 GitHub Gist 传输。

不碰 `~/.claude` 之外的东西，也不管理非 plugin 形式安装的 MCP server（那类用
`doctor` 子命令列出来提醒你，不代管）。

### 核心亮点

- **零依赖**：纯标准库，免安装第三方包。
- **跨机同步**：`save` → `merge` → `apply`，一键补装本机缺的插件。
- **脱敏快照**：`identity`/`machine` 必须是标签，**不能填真实邮箱或主机名**。
- **`apply` 默认 dry-run**：不加 `-y` 只预览，不动真格；加了 `-y` 还必须给 `--scope`。
- **只装认识的**：目标机器没加对应 marketplace 的插件，只列出跳过，**绝不瞎猜安装**。
- **170 项测试**全 mock，测试不碰真实插件。

### 快速上手（新手向）

不用装任何东西，克隆仓库后直接跑：

```bash
git clone <本仓库地址>
cd claude-plugin-skill-sync

# 第 1 步：看看自己机器上装了哪些插件
python plugin_manager.py list

# 第 2 步：一键更新全部插件
python plugin_manager.py update --all
```

只用单台机器？到这两步就够了，下面「跨机同步」部分可以先跳过。

想在多台机器间同步插件，最简三步：

```bash
# 机器 A：存一份本机快照
python plugin_manager.py save --identity 你的名字 --machine 机器A标签

# 机器 B：也存一份（identity 用同一个，machine 换成机器 B 的标签）
python plugin_manager.py save --identity 你的名字 --machine 机器B标签

# 把两份快照放进同一个目录后，合并 + 预览 + 补装（先不加 -y 看看会装什么）
python plugin_manager.py merge --dir /path/to/synced/dir --out merged.json
python plugin_manager.py apply merged.json --all --scope user
```

确认预览没问题，再加 `-y` 真正安装：`python plugin_manager.py apply merged.json --all -y --scope user`。

### 环境要求

- Python 3.8+
- PATH 中有 `claude` CLI（Claude Code 本体）

无第三方依赖 —— 只用标准库。

### bootstrap_tools.py

独立脚本，独立职责：安装 `~/.claude` 的 hooks 和 CLAUDE.md 规则依赖、但本身不是
Claude 插件的那些独立 CLI 二进制（`rtk`、`gh-asset`，以及部分 hook/MCP server
需要的 `node`/`uv` 运行时）。`plugin_manager.py` 本体只和 `claude plugin ...`
打交道，不装这些。免 sudo，全部装到 `~/.local` 下。

```bash
python bootstrap_tools.py          # 装缺的
python bootstrap_tools.py --check  # 只报状态，不装
```

背景：2026-09-03 把开发环境从 Windows 迁到 Ubuntu 时，发现新机器上这些工具全部
缺失，才补了这个脚本。`sqz` 是已知缺口 —— 原因见脚本里 `KNOWN_UNAVAILABLE` 的注释。

### 命令

#### list

以紧凑表格列出所有已安装插件。

```bash
python plugin_manager.py list
```

```
Plugin                     Source                    Version        Scope    Status
───────────────────────────────────────────────────────────────────────────────────
caveman                    caveman                   655b7d9c5431   user     ✔
ecc                        ecc                       2.0.0-rc.1     user     ✔
context7                   claude-plugins-official   cda114029ef8   user     ✗
...

27 plugins installed
```

#### update

更新一个、多个或全部插件。插件名支持部分匹配（不用打全 `caveman@caveman`）。

```bash
# 单个插件
python plugin_manager.py update caveman

# 多个插件
python plugin_manager.py update caveman ecc eduforge

# 全部插件（顺序执行）
python plugin_manager.py update --all

# 全部插件（并行 —— 插件多时更快）
python plugin_manager.py update --all --parallel
```

```
Updating 3 plugins...

[1/3] caveman@caveman... ✔
[2/3] ecc@ecc... ─
[3/3] eduforge@eduforge... ✔

────────────────────────────────────────
Updated: 2  Already current: 1  Failed: 0
```

图标含义：`✔` 已更新 · `─` 已是最新 · `✗` 失败

状态判定靠比较更新前后的版本号（更新全部跑完后再拉一次
`claude plugin list --json`），不靠猜 CLI 输出文字 —— 退出码 0 但版本号没变，
现在会正确判为「已是最新」而非「已更新」。这检测的是版本号变化，不是任意内容
变化 —— 如果某个 marketplace 用同一版本号重新推送了内容，这里仍会读作
「已是最新」。只要有实际更新发生，会提示你重启 Claude Code：已更新插件里
打包的 MCP server/skill，在当前会话里仍跑旧代码，直到重启才生效。

#### doctor

有些 MCP server 不是打包在任何插件里的 —— 它们是直接用 `claude mcp add`
注册的。`claude mcp` 没有 `update` 子命令，`plugin_manager.py update` 碰不到它们；
只能靠它们自己的包管理器（npm/uv/pip）刷新。`doctor` 就是把这批列出来，
避免它们被悄悄漏管。

```bash
python plugin_manager.py doctor
```

```
Standalone MCP servers (not bundled in any plugin):

  firecrawl: npx -y firecrawl-mcp  [✔ Connected]

1 standalone server found.
`claude mcp` has no update subcommand — refresh these via their own
package manager (npm/uv/pip), not this tool.
```

#### uninstall / remove

卸载一个或多个插件。默认弹确认提示，传 `-y` 跳过。

```bash
# 单个插件（带确认提示）
python plugin_manager.py uninstall caveman

# 跳过提示
python plugin_manager.py uninstall caveman -y

# 多个插件
python plugin_manager.py uninstall caveman ecc -y

# 保留插件数据目录
python plugin_manager.py uninstall caveman -y --keep-data

# 顺带清理不再使用的自动装依赖
python plugin_manager.py uninstall caveman -y --prune

# 'remove' 是别名
python plugin_manager.py remove caveman -y
```

#### save / merge / upload / fetch / apply

保存已安装插件/技能的脱敏快照，把多台机器的快照合并成一份差异视图，
（可选）把某台机器上缺的东西一键补装上。

```bash
# 保存本机快照（identity/machine 是必填标签，不能填真实邮箱或主机名 —— 原因见 --help）
python plugin_manager.py save --identity mosjin --machine work-laptop

# 默认输出目录是 ./snapshots（已 gitignore，仅本机可见）。
# --dir 指向你自己的私有仓库/云同步目录即可自行跨机同步，
# 或者直接用下面的 upload/fetch。
python plugin_manager.py save --identity mosjin --machine work-laptop --dir /path/to/synced/dir

# 把某目录下所有快照合并成一份「每台机器」差异视图
python plugin_manager.py merge --dir /path/to/synced/dir

# 同时把合并结果写到文件（供后面 apply 用）
python plugin_manager.py merge --dir /path/to/synced/dir --out merged.json

# 用 GitHub Gist 做同步传输（复用 `gh` 的登录态）。
# 首次上传会建一个 secret gist，并把 id 缓存在快照目录旁；
# 同目录下后续 upload/fetch 会自动复用这个 id。
python plugin_manager.py upload snapshots/<file>.json
python plugin_manager.py fetch --gist-id <id>

# 把合并视图里「本机缺的」装上。默认 dry-run 预览；
# 加 -y（须同时给 --scope）才真正安装。
# 只有本机已经加过对应 marketplace 的插件才能被装 ——
# 其余的只列出来跳过，不瞎猜。
python plugin_manager.py apply merged.json --all -y --scope user
python plugin_manager.py apply merged.json --lang zh   # 中文提示
```

### 测试

```bash
python -m pytest tests/ -v
```

170 项测试，所有 subprocess 调用均已 mock —— 测试过程不会动到真实插件。

### 跨平台

Windows / Linux / macOS 均可用。用 `shutil.which` 定位 `claude`/`claude.cmd`/`claude.exe`。

---

<a id="english"></a>
## English

### About

A cross-platform Python CLI for managing Claude Code plugins and syncing
installed plugins/skills across machines. The problem it solves: anyone
running Claude Code on more than one machine (work laptop, home desktop,
a server) ends up manually tracking which plugins are installed and
whether versions line up. This tool turns "what's installed" into a
snapshot you can save, desensitize for sharing, merge for a drift view
across machines, and apply to auto-install what's missing — no cloud
account system required, just local JSON files plus an optional GitHub
Gist transport.

It never touches anything outside `~/.claude`, and it does not manage
MCP servers installed outside the plugin system — those are surfaced
(not managed) via the `doctor` subcommand.

### Highlights

- **Zero dependencies** — stdlib only, nothing to install.
- **Cross-machine sync** — `save` → `merge` → `apply` installs whatever's missing on a machine.
- **Desensitized snapshots** — `identity`/`machine` are required labels, **never a raw email or hostname**.
- **`apply` defaults to dry-run** — preview only unless `-y`; `-y` also requires `--scope`.
- **Never guesses installs** — plugins whose marketplace isn't on the target machine are listed and skipped, **not force-installed**.
- **170 tests**, all mocked — real plugins are never touched during testing.

### Quick Start (beginner-friendly)

Nothing to install — clone and run:

```bash
git clone <this-repo-url>
cd claude-plugin-skill-sync

# Step 1: see what's installed on this machine
python plugin_manager.py list

# Step 2: update everything in one go
python plugin_manager.py update --all
```

Only using one machine? That's it — skip the "cross-machine sync" part below.

Want to sync plugins across machines? Three steps:

```bash
# Machine A: save a snapshot
python plugin_manager.py save --identity your-name --machine machineA

# Machine B: save one too (same identity, different machine label)
python plugin_manager.py save --identity your-name --machine machineB

# Put both snapshot files in one shared dir, then merge + preview + install
python plugin_manager.py merge --dir /path/to/synced/dir --out merged.json
python plugin_manager.py apply merged.json --all --scope user
```

Preview looks right? Add `-y` to actually install: `python plugin_manager.py apply merged.json --all -y --scope user`.

### Requirements

- Python 3.8+
- `claude` CLI in PATH (Claude Code)

No external dependencies — stdlib only.

### bootstrap_tools.py

Separate script, separate concern: installs the standalone CLI binaries
(`rtk`, `gh-asset`, plus the `node`/`uv` runtimes several hooks and MCP
servers need) that `~/.claude`'s hooks and CLAUDE.md rules assume are on
PATH. These are not Claude plugins — `plugin_manager.py` above only ever
talks to `claude plugin ...`. No sudo required; everything installs under
`~/.local`.

```bash
python bootstrap_tools.py          # install what's missing
python bootstrap_tools.py --check  # report status only, install nothing
```

Written after migrating dev from Windows to Ubuntu (2026-09-03) surfaced
that none of these were present on a fresh machine. `sqz` is a known gap —
see the script's `KNOWN_UNAVAILABLE` note for why.

### Commands

#### list

Show all installed plugins in a compact table.

```bash
python plugin_manager.py list
```

```
Plugin                     Source                    Version        Scope    Status
───────────────────────────────────────────────────────────────────────────────────
caveman                    caveman                   655b7d9c5431   user     ✔
ecc                        ecc                       2.0.0-rc.1     user     ✔
context7                   claude-plugins-official   cda114029ef8   user     ✗
...

27 plugins installed
```

#### update

Update one, multiple, or all plugins. Partial plugin names are accepted (no need to type `caveman@caveman`).

```bash
# Single plugin
python plugin_manager.py update caveman

# Multiple plugins
python plugin_manager.py update caveman ecc eduforge

# All plugins (sequential)
python plugin_manager.py update --all

# All plugins (parallel — faster for many plugins)
python plugin_manager.py update --all --parallel
```

```
Updating 3 plugins...

[1/3] caveman@caveman... ✔
[2/3] ecc@ecc... ─
[3/3] eduforge@eduforge... ✔

────────────────────────────────────────
Updated: 2  Already current: 1  Failed: 0
```

Icons: `✔` updated · `─` already current · `✗` failed

Status is resolved by comparing the plugin's version string before and
after (via a second `claude plugin list --json` once all updates finish),
not by guessing at the CLI's stdout wording — a `0` exit code with an
unchanged version string now correctly reads as "current" instead of
"updated". This detects a changed version string, not arbitrary content
changes — a marketplace reinstalling identical content under the same
version still reads as "current". If anything did update, a note reminds
you to restart Claude Code: an updated plugin's bundled MCP servers/skills
keep running the old code in the current session until then.

#### doctor

Some MCP servers aren't bundled inside any plugin — they were registered
directly with `claude mcp add`. `claude mcp` has no `update` subcommand, so
`plugin_manager.py update` cannot touch them; they must be refreshed through
their own package manager (npm/uv/pip). `doctor` lists exactly those, so
they don't go silently unmanaged.

```bash
python plugin_manager.py doctor
```

```
Standalone MCP servers (not bundled in any plugin):

  firecrawl: npx -y firecrawl-mcp  [✔ Connected]

1 standalone server found.
`claude mcp` has no update subcommand — refresh these via their own
package manager (npm/uv/pip), not this tool.
```

#### uninstall / remove

Remove one or more plugins. Prompts for confirmation unless `-y` is passed.

```bash
# Single plugin (with confirmation prompt)
python plugin_manager.py uninstall caveman

# Skip prompt
python plugin_manager.py uninstall caveman -y

# Multiple plugins
python plugin_manager.py uninstall caveman ecc -y

# Preserve plugin data directory
python plugin_manager.py uninstall caveman -y --keep-data

# Remove unused auto-installed dependencies
python plugin_manager.py uninstall caveman -y --prune

# 'remove' is an alias
python plugin_manager.py remove caveman -y
```

#### save / merge / upload / fetch / apply

Save a desensitized snapshot of installed plugins and skills, merge
snapshots from multiple machines into one drift view, and (optionally)
install whatever's missing on a given machine.

```bash
# Save a snapshot of this machine (identity/machine are required labels,
# never a raw email or hostname — see --help for why)
python plugin_manager.py save --identity mosjin --machine work-laptop

# Default dir is ./snapshots (gitignored, local to this machine only).
# Point --dir at a private repo/cloud-sync folder you control to sync it
# across machines yourself, or use upload/fetch below instead.
python plugin_manager.py save --identity mosjin --machine work-laptop --dir /path/to/synced/dir

# Merge every snapshot in a directory into one per-machine drift view
python plugin_manager.py merge --dir /path/to/synced/dir

# Also write the merged view to a file (for `apply` later)
python plugin_manager.py merge --dir /path/to/synced/dir --out merged.json

# Sync via a GitHub Gist instead of your own transport (reuses `gh` auth).
# First upload creates a secret gist and caches its id next to the
# snapshot dir; later uploads/fetches in that dir reuse it automatically.
python plugin_manager.py upload snapshots/<file>.json
python plugin_manager.py fetch --gist-id <id>

# Install on this machine whatever the merged view shows is missing here.
# Dry-run by default; -y (plus --scope) actually installs. Only plugins
# whose marketplace is already added on this machine are installable —
# others are listed and skipped, never guessed at.
python plugin_manager.py apply merged.json --all -y --scope user
python plugin_manager.py apply merged.json --lang zh   # Chinese prompts
```

### Tests

```bash
python -m pytest tests/ -v
```

170 tests, all subprocess calls mocked — no real plugins are modified during testing.

### Cross-platform

Works on Windows, Linux, macOS. Uses `shutil.which` to locate `claude`/`claude.cmd`/`claude.exe`.
