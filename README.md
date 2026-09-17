<div align="center">

# claude-plugin-skill-sync

**跨机同步你的 Claude Code 插件与技能 —— 一份快照，装到哪台机器都一样**

[![Python](https://img.shields.io/badge/python-3.8%2B-3776AB?logo=python&logoColor=white)](#环境要求)
[![Dependencies](https://img.shields.io/badge/dependencies-zero-success)](#环境要求)
[![Tests](https://img.shields.io/badge/tests-187%20passing-brightgreen)](#测试)
[![Platform](https://img.shields.io/badge/platform-windows%20%7C%20linux%20%7C%20macos-lightgrey)](#跨平台)

[English](README.en.md)

</div>

---

## 目录

- [关于本项目](#关于本项目)
- [核心亮点](#核心亮点)
- [快速上手](#快速上手新手向)
- [跨机同步完整流程](#跨机同步完整流程)
- [命令一览](#命令一览)
- [环境要求](#环境要求)
- [bootstrap_tools.py](#bootstrap_toolspy)
- [测试](#测试)
- [跨平台](#跨平台)

---

## 关于本项目

跨平台 Python CLI，管理 Claude Code 插件（plugin），并在多台机器间同步已安装的
插件/技能（skill）清单。

> 一个人常在多台机器上用 Claude Code，插件装了哪些、版本是否一致，全靠手动对照。

本工具把「装了什么」做成可保存、可脱敏分享、可合并对比、可一键补装的快照 ——
不依赖云端账号体系，纯本地 JSON + 可选 GitHub Gist 传输。只管 plugin，不碰
`~/.claude` 之外的东西；非 plugin 形式的 MCP server 用 [`doctor`](#命令一览) 列出提醒，不代管。

## 核心亮点

| 特性 | 说明 |
|---|---|
| **零依赖** | 纯标准库，免安装第三方包 |
| **跨机同步** | `save` → `merge` → `apply`，一键补装本机缺的插件 |
| **脱敏快照** | `identity`/`machine` 必须是标签，**不能填真实邮箱或主机名** |
| **`apply` 默认 dry-run** | 不加 `-y` 只预览，不动真格；加了 `-y` 还必须给 `--scope` |
| **只装认识的** | 目标机器没加对应 marketplace 的插件，只列出跳过，**绝不瞎猜安装** |
| **187 项测试** | 全 mock，测试不碰真实插件 |

## 快速上手（新手向）

不用装任何东西，克隆仓库后直接跑：

```bash
git clone <本仓库地址>
cd claude-plugin-skill-sync

# 第 1 步：看看自己机器上装了哪些插件
python plugin_manager.py list

# 第 2 步：一键更新全部插件
python plugin_manager.py update --all
```

只用单台机器？到这两步就够了。想在多台机器间同步插件，看下一节的完整流程。

## 跨机同步完整流程

典型场景：机器 A 装了一堆插件，想让机器 B 也装成一样的。全程只有第一次 `fetch`
可能要多敲一个 id，其余步骤照抄命令即可。

```bash
# ① 机器 A —— 保存本机快照
python plugin_manager.py save --identity mosjin --machine work-laptop

# ② 机器 A —— 上传到 GitHub Gist
#    首次上传自动建一个 secret gist，id 缓存进 snapshots/.gist_id
python plugin_manager.py upload snapshots/<file>.json
```

```bash
# ③ 机器 B —— 保存本机快照（可选，让下面的差异视图里也能看到机器 B 装了什么）
python plugin_manager.py save --identity mosjin --machine home-pc

# ④ 机器 B —— 拉取机器 A 的快照
#    这台机器从没 fetch/upload 过，没有缓存的 id —— 但只要这个 GitHub
#    账号下只有一个这个工具建的 gist，fetch 会自动找到并缓存，不用手敲 id：
python plugin_manager.py fetch

# 账号下有不止一个这类 gist（同步过好几套）？先看一眼再指定：
python plugin_manager.py gist-list
python plugin_manager.py fetch --gist-id <id>

# ⑤ 机器 B —— 合并两份快照，生成差异视图
python plugin_manager.py merge --dir snapshots --out merged.json

# ⑥ 机器 B —— 先预览会装什么，确认没问题再加 -y 真正安装
python plugin_manager.py apply merged.json --all --scope user
python plugin_manager.py apply merged.json --all -y --scope user

# ⑦ 机器 B —— 把所有插件（含刚补装的）都更新到最新版本
python plugin_manager.py update --all
```

之后想反向同步（机器 B 装的东西同步回机器 A）？在机器 B 上 `save` + `upload`
即可 —— 这时 `.gist_id` 已经缓存过，会直接把新快照加进同一个 gist，不会另建一个；
回机器 A `fetch` 就能拿到。

## 命令一览

| 命令 | 作用 |
|---|---|
| [`list`](#list) | 列出所有已安装插件 |
| [`update`](#update) | 更新一个/多个/全部插件 |
| [`doctor`](#doctor) | 列出本工具管不到的独立 MCP server |
| [`uninstall` / `remove`](#uninstall--remove) | 卸载插件 |
| [`save`](#save--merge--upload--fetch--apply) | 保存本机插件/技能的脱敏快照 |
| [`merge`](#save--merge--upload--fetch--apply) | 合并多台机器的快照成差异视图 |
| [`upload` / `fetch`](#save--merge--upload--fetch--apply) | 用 GitHub Gist 同步快照 |
| [`gist-list`](#gist-list) | 列出本工具建过的 gist，找 id 不用开浏览器 |
| [`apply`](#save--merge--upload--fetch--apply) | 把本机缺的插件按快照补装上 |

### list

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

### update

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

状态靠比较更新前后的版本号判定，不靠猜 CLI 输出文字（只检测版本号变化，
非任意内容变化）。

> **注意**：有实际更新发生时会提示重启 Claude Code —— 旧插件的 MCP server/skill
> 在当前会话里仍跑旧代码，重启才生效。

### doctor

有些 MCP server 是用 `claude mcp add` 直接注册的，不打包在任何插件里，
`update` 碰不到它们。`doctor` 把这批列出来，避免悄悄漏管。

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

### uninstall / remove

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

### save / merge / upload / fetch / apply

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
# 没缓存、没传 --gist-id 时：账号下只有一个本工具建的 gist 就自动用它；
# 有不止一个就报错，提示先用 gist-list 看一眼再指定 --gist-id。
python plugin_manager.py upload snapshots/<file>.json
python plugin_manager.py fetch --gist-id <id>

# 把合并视图里「本机缺的」装上。默认 dry-run 预览；
# 加 -y（须同时给 --scope）才真正安装。
# 只有本机已经加过对应 marketplace 的插件才能被装 ——
# 其余的只列出来跳过，不瞎猜。
python plugin_manager.py apply merged.json --all -y --scope user
python plugin_manager.py apply merged.json --lang zh   # 中文提示
```

### gist-list

列出这个工具建过的 gist（按 upload 打的标签过滤），不用开浏览器找 id。

```bash
python plugin_manager.py gist-list
```

```
Gist ID                           Visibility  Files      Updated
──────────────────────────────────────────────────────────────────
1cd6200ecc8a99f2cf03d59954bda507  secret      1 file     2026-09-17T00:34:47Z

1 gist found.
Use one with: fetch --gist-id <id>  (or upload --gist-id <id>)
```

## 环境要求

- Python 3.8+
- PATH 中有 `claude` CLI（Claude Code 本体）

无第三方依赖 —— 只用标准库。

## bootstrap_tools.py

独立脚本，装 `~/.claude` hooks/规则依赖但非 plugin 形式的 CLI 二进制
（`rtk`、`gh-asset`、`node`/`uv`）。免 sudo，全部装到 `~/.local` 下。

```bash
python bootstrap_tools.py          # 装缺的
python bootstrap_tools.py --check  # 只报状态，不装
```

`sqz` 是已知缺口 —— 原因见脚本里 `KNOWN_UNAVAILABLE` 注释。

## 测试

```bash
python -m pytest tests/ -v
```

187 项测试，所有 subprocess 调用均已 mock —— 测试过程不会动到真实插件。

## 跨平台

Windows / Linux / macOS 均可用。用 `shutil.which` 定位 `claude`/`claude.cmd`/`claude.exe`。

---

<div align="center">

[English README](README.en.md)

</div>
