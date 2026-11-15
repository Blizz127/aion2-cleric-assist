# Handoff: Aion 2 Cleric Assist, Claude to Codex (GPT-6-Astra)

**Why.** The owner ordered it at 2026-11-15 07:01–07:03Z (1:01–1:03 AM CST) to save Claude quota: this lead moves
off Claude Opus to Codex GPT-6-Astra. Coordinator task 2e32f40b717b. From this handoff commit on, the Codex lead is
the only writer. The Claude session `claude-aion2-cleric-assist` (lead from 2026-11-12 14:51Z) stops writing here.

## Codex continuation (2026-11-15): 0.4.2
- Reproduced and fixed combat calibration silently overwriting an existing output file. Profiles now serialize
  and flush to a same-directory temporary file, then atomically publish only at a new path. Existing files,
  directories, symlinks and concurrently claimed destinations are preserved; there is no overwrite fallback.
- Calibration reports input/output failures cleanly and rejects malformed, duplicate or unknown shot labels;
  configured custom state labels remain supported. Tests use synthetic files and injected I/O failures only.
- README documents the new output contract and removes the former overwrite warning from the capture checklist.
  No owner frames, approval, input, geometry or rotation settings changed; `live_verified=false` remains in force.
- All 20 focused checks pass locally:
  `python3 -m unittest tests.test_calibration_output tests.test_perception_calibration.Calibration`.
  `BUILD.json` records source; delivery/backup/rollback details go in `dist/deploy-0.4.2-*.json`. Nothing is pushed.

## Codex continuation (2026-11-15): 0.4.1
- Added labeled synthetic PNG rotation regressions using the unchanged Alienware draft config and a test-only
  mapping of demo pixels to 1/2/Q/E. Expectations check perception first, then the real decider/controller with
  mock input only. Generated PNGs remain temporary; only synthetic scenario labels and tests are packaged.
- Cases cover priority/fixed spacing, HP boundaries/prediction, Q upkeep, observed/tracked cooldowns, disabled
  items, unsafe/stale frames and emergency disarm. The existing Q-ready-after-press limitation is reproduced:
  at steady half HP, Q presses at 0/6/12 seconds and output latches at 13.5 seconds after three misses.
  This does not disarm (`enabled` stays true), and no Q/E policy or timing was changed without owner evidence.
- README now joins capture, preservation of both sessions/logs/terminal summary, copying, labeling, intake,
  calibration and fresh-frame checks into one checklist. Owner approval/first live trial remain separate.
- All nine focused regressions pass locally across 24 labeled scenarios. Run
  `python3 -m unittest tests.test_rotation_fixtures` to repeat them; private delivery includes
  the existing ten offline build smokes. `BUILD.json` records source; delivery/backup/rollback receipt goes in
  `dist/deploy-0.4.1-*.json`. Owner captures remain outstanding, `live_verified=false`, and nothing is pushed.

## Codex continuation (2026-11-15): 0.4.0
- Added offline `ingest fixtures`, `ingest validate` and `ingest demo`. Fixtures are clearly synthetic,
  use the existing 320x180 demo UI, and never establish owner captures, real binds or Q/E stack semantics.
- The read-only validator checks labeled full-frame PNGs, dimensions, structure/pixel decoding, input hashes,
  coverage and an in-memory calibration build. It reports `calibration_buildable` and
  `evidence_coverage_complete` separately; `owner_evidence_satisfied`, `live_verified` and `input_sent` stay false.
  Owner-declared sets that pass technical checks get a manual calibration argument array, never execution.
- README documents the manifest and owner-intake steps. Missing target geometry, semantic evidence review,
  owner approval and the first live trial remain outstanding. No real frames were supplied or invented.
- Focused ingest/packaging checks and the private build's ten offline smoke checks cover the new path.
  No full-suite rerun or live adapters. `BUILD.json` records the source commit; build/delivery/backup details
  go in `dist/deploy-0.4.0-*.json`. Rollback follows the existing separate-directory/deploy-script procedure.
  Nothing is pushed; the capture hold stays in force.

## Codex continuation (2026-11-15): 0.3.7
- Hardened stdlib PNG pixel decoding after synthetic fixtures showed unsupported filters accepted as raw pixels
  and extra decoded bytes ignored. The reader now rejects these and incomplete/trailing compressed data,
  invalid dimensions/formats, and invalid indexed palettes/references with `ValueError`.
- Decompression is limited to the declared scanline size plus one byte; the five supported 8-bit color modes and
  filters preserve their existing RGB values. This does not replace capture completeness or game-state checks.
- All 15 focused decoder/PNG-round-trip/synthetic-calibration checks pass locally. No full-suite rerun; no owner
  screenshots, real calibration, approval, portal consent or game-input trial. The capture hold and
  `live_verified=false` remain in force, and nothing was pushed.
- `BUILD.json` records the source commit; build/delivery/backup details go in `dist/deploy-0.3.7-*.json`.
  Rollback follows the existing separate-directory/deploy-script procedure documented below.

## Codex continuation (2026-11-15): 0.3.6
- Fixed an offline-confirmed packaging omission: private archives and source deployments now include
  `HANDOFF.md` and executable `deploy-alienware.sh`, both referenced by the README.
- The build now runs nine smoke checks, including the packaged deployer's network-free `--check` mode. Its
  declared inventory must match the fixed package entries and every listed path must exist.
- Three focused packaging regressions pass: standalone extracted-archive entry points, manifest hashes/private
  root-file exclusion, and rejection of missing or unexpected deployment entries. Fixture markers are synthetic,
  never owner captures or configuration. No full-suite rerun is required for this packaging-only patch.
- The archive's `BUILD.json` records its exact source commit. Devbox build/deployment receipts are in `dist/`;
  use `deploy-0.3.6-*.json` for its target backup and delivery details. Rollback uses the existing deploy script
  from a separate directory containing the pre-deployment source backup (as described under 0.3.3 below).
- The owner capture requirement remains unsatisfied, `live_verified=false`, and nothing was pushed.

## Codex continuation (2026-11-15): 0.3.5
- Fixed offline-reproduced PipeWire reader shutdown defects: helpers are now waited/reaped, killed after a
  termination timeout, and their reader threads joined before buffered stdout is closed. Waits are bounded.
- Closing is idempotent, discards cached frames, prevents new starts/reads, and prevents a frame already being
  processed from publishing after closure. A stuck reader is reported so orchestrator cleanup can continue.
- Focused checks cover the reader and orchestrator teardown using fakes and a harmless local Python child,
  including ignored termination. No real GStreamer/portal/game-input trial was performed; owner captures and
  approval remain pending, `live_verified=false`, and nothing was pushed.
- Source commit `3cafe12`; all 22 focused reader/full-flow/teardown tests pass locally and on Alienware
  Python 3.14.8. The full suite was not repeated. Private artifact:
  `dist/aion2-assist-0.3.5-20261115T141534784735Z.tar.gz`, sha256
  `85143de4dd943ed9a1437ccbcd28d5adea70d003cd0812550e78a28fc31dfe33`; all eight build smoke checks passed.
- Deployed via `./deploy-alienware.sh` at 14:15:53Z after idle-process check and backup. All 55 packaged source
  hashes matched, draft config validation passed, and protected files were unchanged. Target `shots/` is absent.
  Receipt: `dist/deploy-0.3.5-20261115T141553Z.json`.
- Pre-deployment source backup on Alienware:
  `~/.local/share/aion2-assist/backups/pre-0.3.5-20261115T141549133750Z.tar.gz`, sha256
  `d279d5d6e502a8e0a75d98b1a7158aa217fd5bb0f0a50eaf411a1dcc07631196`.
  Rollback uses the separate-directory/existing-deploy-script procedure recorded under 0.3.3 below.

## Codex continuation (2026-11-15): 0.3.4
- Fixed another offline-reproduced capture bug: empty or truncated PNGs previously counted as successful frames.
  Capture now checks PNG chunk structure and CRCs through `IEND`, without decoding pixels.
- Summary `frames` counts structurally complete files, `frame_files` counts all matching files, and
  `invalid_frames` lists rejected filenames/reasons. Incomplete, corrupt or unreadable files cause exit 4;
  every file is preserved. This does not validate pixel decoding or satisfy calibration evidence.
- Focused validation uses `python3 -m unittest tests.test_capture`, with fake portals/processes and synthetic
  PNG files only. The owner capture hold and `live_verified=false` remain in force. Nothing was pushed.
- Source commit `24b597c`; all 22 focused capture tests pass locally and on Alienware Python 3.14.8.
  Private artifact: `dist/aion2-assist-0.3.4-20261115T133932247662Z.tar.gz`, sha256
  `4b0181841d94e03344fe2952d7594f4350826a9c301cbfe04cd1072198aaeb35`; all eight build smoke checks passed.
- Deployed via `./deploy-alienware.sh` at 13:40:23Z after an idle-process check and source backup. All 55
  packaged source files matched their hashes, draft config validation passed, and protected files were unchanged.
  Target `shots/` remains absent. Receipt: `dist/deploy-0.3.4-20261115T134023Z.json`.
- Pre-deployment source backup on Alienware:
  `~/.local/share/aion2-assist/backups/pre-0.3.4-20261115T134020710102Z.tar.gz`, sha256
  `9f39d9cced6e993ef732e8b7962058b30e52f117d1990d5d757bc06d92615a20`.
  Rollback uses the same separate-directory/existing-deploy-script procedure described for 0.3.3 below.

## Codex continuation (2026-11-15): 0.3.3
- Fixed offline-reproduced capture error handling: a failed GStreamer process now returns failure even after
  saving frames. Errors stopping the process no longer skip screen-share/bus cleanup or the capture summary.
- Saved frames and diagnostic logs are preserved; cleanup errors appear in the summary. Portal denial still
  returns exit 3, and normal owner interruption can still keep the captured frames successfully.
- Focused check: `python3 -m unittest tests.test_capture` passes all 17 tests, using fake portals/processes only.
  The full suite was not repeated for this patch. Private build receipts are under `dist/`.
- Source commit: `71d6bd9`. Private artifact: `dist/aion2-assist-0.3.3-20261115T122930166662Z.tar.gz`,
  sha256 `ea2eedff07a7986b8b615b88d07b0e3881a93e57abb72c3d484e8d42cfcfd211`; all eight build smoke checks passed.
- Deployed with `./deploy-alienware.sh` to `alienware-bazzite:~/Projects/aion2-cleric-assist` at 12:31:07Z.
  No Aion/assist processes were found by the final pre-deployment check. All 17 capture tests and draft config
  validation passed on Python 3.14.8 there; all 55 packaged source files matched their build hashes.
- Pre-deployment source backup on Alienware:
  `~/.local/share/aion2-assist/backups/pre-0.3.3-20261115T123103799630Z.tar.gz`,
  sha256 `a8a1d3ec16332403feb3c876868122c72c36eb15de80b83760d8ce0b3ac83c77`.
  For rollback, copy/extract that source archive to a separate devbox directory, put `deploy-alienware.sh`
  from `cc2fdd8` there, and deploy that directory with the script after checking the target is idle.
  The deployment receipt is `dist/deploy-0.3.3-20261115T123107Z.json`; protected files were unchanged.
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
   - Label the selected frames in a manifest using README's 0.4.0 intake format; run `ingest validate`.
     Technical success does not satisfy owner evidence; review the actual states and sequences first.
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
