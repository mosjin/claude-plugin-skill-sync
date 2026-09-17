# Ideas

## diff

- [ ] `diff` with only one machine in the snapshot dir could print a soft note ("only one machine's data here — did you forget to `save` on this machine too?") — discussed 2026-09-17 after a user ran `diff` with only a fetched snapshot and no local `save`, got confused by the single-entry `Machines:` line. Not implemented: the conversation moved to other feedback before a decision was made on wording/placement. Should be a non-blocking print, not an error — a genuinely single-machine `diff` (just inspecting one machine's own state) is a valid use case too.
