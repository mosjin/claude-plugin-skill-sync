# Lessons Learned

## Local directory rename while it's an active session's CWD (Windows)

- **Lesson:** renaming `D:\works\claude_plugin_updater` itself failed with "Device or resource busy" (bash `mv`) / "Cannot rename the item ... because it is in use" (PowerShell `Rename-Item`, a *separate* process) — even after `cd`-ing the bash shell out of the directory first.
  - **Why:** the Claude Code CLI host process this session runs in has that directory as its own working directory; Windows refuses to rename a directory any live process still has open as CWD, regardless of which tool/process issues the rename command.
  - **Avoid:** don't keep retrying the rename with different tools once two independent processes both fail with the same "in use" class of error — that's confirmation, not a fluke. Local project-folder renames need to happen from outside the active session (a different terminal, or after closing this session), not from within it.

## Desensitization / privacy-sensitive data export

- **Lesson:** "sanitize the filename" is not the same as "sanitize the data" — the first cmd_save implementation slugged identity/machine only for the output filename, then wrote the *raw* (unsanitized, potentially-full-email) values into the JSON body itself.
  - **Why:** the sanitization function existed and was called somewhere, which made the feature *feel* covered without a check of every place the raw value could still reach.
  - **Avoid:** sanitize once, at the single point a value enters the system (`resolve_identity`/`resolve_machine`), and have every downstream consumer (filename, JSON body, merge key) use only that already-sanitized value. One source of truth, not "remember to sanitize at each output site."

## Merge / conflict-resolution semantics

- **Lesson:** a docstring claiming "most recent snapshot wins" was only implemented for one of three data structures (`machines`) — `plugins` and `skills` still did a naive union across every snapshot passed in, so a plugin uninstalled (or skill deleted) between an old save and a new one for the *same* machine lingered forever in the merged view.
  - **Why:** the union logic was the easy/obvious thing to write first (`entry.setdefault(...); entry[machine_key] = ...` inside a loop over all snapshots), and it happened to produce plausible-looking output in every manually-tried example, so it read as correct until an adversarial test (`old, new` → `new, old`, asserting order-independence) caught it.
  - **Avoid:** when a data structure has a "current state" semantic (what's installed *right now*), decide *which single source* supplies that state before writing the merge loop — never let a loop's natural iteration order become the de facto conflict-resolution rule.

## Test quality

- **Lesson:** an early test asserted `"installPath" not in blob` (checking the JSON *key* name) as if it verified the value was scrubbed — but the record under test had already renamed the field, so the assertion passed trivially without ever testing whether the leaked *path string* was gone.
  - **Why:** it's easy to write an assertion that's true for the right reason and also true for a subtly wrong implementation, and only actually running it against a deliberately-broken version reveals the gap.
  - **Avoid:** for a security/privacy-sensitive guarantee, assert against the actual value expected to be absent (`self.assertNotIn(plugin["installPath"], blob)`), not a proxy like a key name — and prefer feeding a fixture engineered to fail if the check is weak.
