# Handoff: Aion 2 Cleric Assist, Claude to Codex (GPT-6-Astra)

**Why.** The owner ordered it at 2026-11-15 07:01–07:03Z (1:01–1:03 AM CST) to save Claude quota: this lead moves
off Claude Opus to Codex GPT-6-Astra. Coordinator task 2e32f40b717b. From this handoff commit on, the Codex lead is
the only writer. The Claude session `claude-aion2-cleric-assist` (lead from 2026-11-12 14:51Z) stops writing here.

## Codex continuation (2026-11-15): 0.3.3
- Fixed offline-reproduced capture error handling: a failed GStreamer process now returns failure even after
  saving frames. Errors stopping the process no longer skip screen-share/bus cleanup or the capture summary.
- Saved frames and diagnostic logs are preserved; cleanup errors appear in the summary. Portal denial still
  returns exit 3, and normal owner interruption can still keep the captured frames successfully.
- Focused check: `python3 -m unittest tests.test_capture` passes all 17 tests, using fake portals/processes only.
  The full suite was not repeated for this patch. Private build receipts are under `dist/`.
- **The owner capture requirement below remains unsatisfied; `live_verified` is still false.** No live
  capture, calibration, approval or game-input trial was performed for this patch. Nothing was pushed.

## State at handoff (2026-11-15 07:11Z)
- **Version 0.3.2. Nothing has run against the game: `live_verified` is false.**
- **Branch `aion2-assist-0.3.2`.** This commit sits on top of `e8f3290`. `main` is still at `d5c7a68`; the
  fast-forward was offered but never run, and nothing was pushed. Commits on the branch:

  | Commit | Contents | Tests |
  |---|---|---|
  | `03d3d6c` | 0.2.0, the earlier Codex lane's workflow layer and build tool | 187 |
  | `44f596c` | 0.3.0, the `capture` command and `deploy-alienware.sh` | 197 |
  | `b01db83` | README note that the Codex lane was closed | 197 |
  | `772166b` | 0.3.1, right-anchored bars and the 2560x1600 drafts | 208 |
  | `e8f3290` | 0.3.2, coordinator decisions | 217 |

  Every run passes, with 12 skipped. Run `python3 -m unittest` to repeat it.
- **Last build:** `dist/aion2-assist-0.3.2-20261113T213258737353Z.tar.gz`, sha256
  `40656e8d95c40408699ed2de4e3205d5892e1e888a6ef5851f0c7316999b4ad9`, `source_head` `e8f3290`, identical to
  `git archive e8f3290`. Deployed to
  `alienware-bazzite:~/Projects/aion2-cleric-assist` at 2026-11-13 21:33:13Z. This handoff changes docs only, so
  there was no rebuild.

## Waiting on owner evidence; do not invent or infer it
None of this exists yet; the PC had no `shots/` folder at 07:11Z:
1. frames with an enemy targeted, to measure `regions.target`, which is unset, so `calibrate` refuses the draft;
2. every calibrated slot (1, 2, Q, E) on cooldown in one frame;
3. HP at several levels, with MP partly spent;
4. how the Q and E stacks look after each press.

Optional: binding evidence for the X/F5/F7 item slots, and a verified keyboard key for Earth's Retribution.

Steps for the owner, with Aion 2 running:
1. Run `cd ~/Projects/aion2-cleric-assist && .venv/bin/python -m aion2_assist capture` and play for about three
   minutes. Pass through an enemy targeted, no target, HP at full, about half and low, an NPC dialog, a vendor, and
   a loading screen.
2. With an enemy targeted, run it again with `--every 1 --seconds 60`. Press 1 and 2 once each, then keep pressing Q
   and E until nothing more casts.

## When the evidence arrives
1. Copy `alienware-bazzite:~/Projects/aion2-cleric-assist/shots/<stamp>/` to the devbox `shots/` folder. It is
   gitignored. Frames never go into commits or builds; the build list in `tools/build_private.py` is fixed.
2. Calibrate:
   - Measure `target` on a targeted frame and add it to `regions.alienware-2560x1600.draft.json`.
   - Run `calibrate`, then `read` on fresh frames.
   - Check the HP reading at each level. Known limit 1: the magenta tip makes HP read low.
   - Check the MP direction; "left" is assumed.
3. Set the Q and E cooldowns from the cooldown capture (README known limit 2). Keep a slot in `regions.slots` only
   if it changes look on cooldown; otherwise `calibrate` refuses the whole profile.
4. Pin `profile_hash` in the config. Only the owner runs `approve`, and only the owner does the first live trial. The
   README lists the activation gates.

## Decisions in force (coordinator task 49df99b370f6)
- Keep the evidenced binds: Q (heal over time), 1, 2 and E.
- X, F5 and F7 stay off; their on-screen positions are only `rect_inferred`.
- Controls are F8 toggle and F9 emergency stop, and startup is disarmed.
- MP restoration stays off. `mp_restore` is an explicitly unset keyboard slot, and nothing automates mouse clicks.

`tests/test_draft_decisions.py` enforces these.

## Rules carried over from this lane
- **No evasion.** Use only screen capture plus portal keyboard input at fixed spacing. Never memory reading, never
  humanized timing or jitter, never `uinput`. That rules out `gw1-rotation`'s `humantiming.py`, the macro's
  `aion2_cleric_human.py`, and its `retry_jitter`/timing fields.
- **The old macro is read-only.** `alienware-bazzite:~/Projects/aion2-cleric-macro` is the owner's earlier project.
  Take data only (binds, cooldowns, real frames) and never write there. Its `import -window` capture is an option
  to raise with the owner, not an approved change.
- **Deploy only with `./deploy-alienware.sh`.** It copies project files and never deletes anything on the PC, so the
  owner's config, profile, approvals, journals and `shots/` there are untouched.
- **Leave task 6b52dbf3ae9f alone.** It is cancelled. Its board text says 15:05Z, but the real closure was 22:49:35Z;
  it was left as is on purpose. Never report on it.
- **Reporting.** Use `coordinator.py report` for assigned tasks, and end each turn with a
  `DEVBOX-REPORT working|blocked|completed: …` line.
- **Leave the backup alone.** The untracked `README.md.bak-pre-gw1-aion2-20261112T0025` is preserved and never
  committed.
