# aion2-cleric-assist 0.3.0 (live behavior unverified)

> **Lane owner since 2026-11-12 14:51Z:** Claude session `claude-aion2-cleric-assist`. The owner reassigned the
> lane in that session's terminal ("take it from the other repo please" / "and lets get it up and running to finish
> the goal"). Codex task 6b52dbf3ae9f is parked. It should not write here unless the owner hands the lane back.

Combat assistance and calibrated visual workflows for an Aion 2 Cleric on Bazzite/KDE Wayland
(Steam appid 3393110, Proton). The full-game autonomy goal is **not complete**: the seven-stage
quest/navigation/combat/loot/turn-in/buy/sell demonstration is synthetic, and no real route or merchant
has been calibrated or verified.

## OFFLINE ONLY. Live use risks an NC "Integrated Account Ban".
No live game/portal trial has been performed. The owner has accepted the account-ban risk for any future
live trial; the existing explicit trial and native permission gates remain. `run` wires the real adapters,
refuses missing prerequisites, and starts the controller **OFF**. Core is stdlib Python 3.12
(minimal PNG reader); optional live dependency: jeepney (below).

## Visual workflows and current evidence
```
python3 -m aion2_assist workflow demo
python3 -m aion2_assist doctor
python3 -m aion2_assist workflow calibrate --spec workflow-spec.json --profile profile.json --out workflow.json
python3 -m aion2_assist workflow validate --recipe workflow.json --profile profile.json
python3 -m aion2_assist workflow replay --recipe workflow.json --profile profile.json --config config.json --frames shots --dt 0.3
```
`workflow demo` runs a synthetic feedback world through the same Controller used by the live orchestrator.
It completes seven stages and emits eight mock keys: `q,w,1,4,f,q,b,s` (the `1` is an emergency heal).
These keys, UI patterns, item names, quantities and prices are **demo data, not Aion bindings or game knowledge**.
`doctor` only reads local configuration and executable availability; it opens no bus, portal or input device.

A workflow spec contains `budget`, `checkpoints` and ordered `steps`. Each checkpoint names a full-frame
positive PNG (`image`), a full-frame negative PNG (`absent`), a `(x,y,w,h)` `rect`, a `threshold` (0.9–1),
and `state` (`null` or `"dialog"`). Image paths resolve relative to the spec. Calibration embeds the pixels,
verifies that the negative example is separated from the positive, and pins the combat profile hash.
Existing output files are refused. Checkpoint rectangles must capture the distinctive condition being tested.

Each step has a unique `id`, `kind` (`navigate`, `quest`, `loot`, `buy`, `sell`, `combat`, `wait`),
`before`/`after` checkpoint names and a finite `timeout_s` (0.1–3600). Noncombat actions also specify one real
`key`; it is a single short press. Combat stages use the existing skill decider. A missing checkpoint waits;
an expired step blocks. An action is never considered successful merely because input was sent: its postcondition
must have been absent before dispatch and present on two distinct subsequent frames. Ambiguous checkpoints block.
Only the active step's recognized dialog can produce a workflow key; combat stays suppressed in dialogs.
Freshness, focus, manual activity, hold/toggle, emergency stop, HP, rate and casting checks still apply.

Buy/sell steps also declare `trade: {"item":"...", "quantity":1, "total":5, "currency":"in_game"}`.
Sales require `"protected": false`; purchase totals must fit the recipe's budget. Both trade checkpoint
thresholds must be exactly `1` to avoid approximate digit/item matches. The before rectangle must include the
selected item, quantity and total; the after rectangle must show an inventory/balance receipt that changes.
These are explicit recipe declarations and pixel checks, **not OCR, live price discovery, inventory classification,
or automatic protection detection**. Premium currency and real-money trade declarations are refused.

Navigation currently means calibrated key-driven transitions. There is no map pathfinder, continuous movement,
mouse targeting, general quest understanding or automatic recovery from arbitrary terrain/UI states. New routes
need real screenshots, binds and outcome checks. Low-HP healing can preempt a normal-world stage; unknown states,
enemy targets outside the combat stage, and ambiguous vendor results stop workflow inputs.

The optional live path is `run ... --workflow workflow.json --workflow-journal progress.jsonl`.
It requires the workflow's hash **in addition to** the combat profile hash in the existing approval file.
Only the owner performs `approve --profile profile.json --workflow workflow.json --i-accept-live-risk`;
this command backs up an existing approval before replacing it. It does not satisfy native portal consent or
the outstanding target verification. No approvals are created by replay, doctor or build commands.

Live workflow progress uses an exclusive journal lock and fsyncs dispatch intent **before** sending input.
Confirmed steps and reserved purchase totals survive restart. An unresolved dispatch or malformed journal
refuses continuation; it never retries an uncertain purchase or sale. Preserve the journal for reconciliation:
deleting it or choosing another journal deliberately starts a new run and loses duplicate-action protection.
Workflow completion/blocked outcomes stop the live orchestrator and release inputs. Replay returns 0 only for
confirmed completion, 4 for incomplete/blocked execution, and 2 for invalid inputs.

## Private build artifact
```
python3 tools/build_private.py --out dist
```
Builds a versioned source archive with per-file and archive SHA-256 checksums and runs startup, combat replay,
workflow demo and default-refusal smoke checks from an isolated staged copy. The build does not publish or
start live adapters.

## Getting it running on the Alienware
Delivery goes to the owner's PC, where the game runs: `./deploy-alienware.sh` copies only this project's
files to `alienware-bazzite:~/Projects/aion2-cleric-assist` (no `--delete`, so `config.json`, `profile.json`,
approvals, journals and `shots/` there are never touched), byte-compiles them and runs the read-only `doctor`.
`--setup-venv` creates `.venv` there and installs `requirements.txt` (jeepney). `--check` lists the files.
First deploy: 2026-11-12 14:55Z, 0.2.0 on Python 3.14.8. All dependencies were present; workflow demo, replay
demo and the `run` refusal behaved the same as on the devbox.

**Calibration capture.** Real screenshots are the main blocker, so `capture` records them through the same
ScreenCast + PipeWire path that `run` uses:
```
cd ~/Projects/aion2-cleric-assist && .venv/bin/python -m aion2_assist capture --every 3 --seconds 180
```
The portal asks which screen to share. Frames go to a new `shots/<UTC stamp>/` folder as lossless PNGs at the
stream's native size. Nothing is scaled, so the pixels match the live reader. It opens no RemoteDesktop
session and cannot send input. While it records, play normally and pass through these states:
an enemy targeted with every slot ready, every slot on cooldown, no target, an ally targeted, low HP if it
happens, an NPC dialog, a loading screen, a vendor buy and sell window, and the quest tracker. Then
`calibrate`, `read` and the workflow `calibrate` run on the chosen frames (devbox or PC).
Checked on the PC: every pipeline element exists, and a synthetic `videotestsrc` run through the same pipeline
produced PNGs that the stdlib reader loads. The real screen-share path has not run yet.

## Architecture
```
FrameSource ──> Perceiver ──> Observation ──> Decider ──> Controller ──> InputSink
(capture)       (readers)     (value,conf,ts)  (pure)      (safety gate)   (key down/up)
FocusProbe ────────────────────────────────────────────────┘      ^
OwnerInput (toggle / emergency / hold / manual-input) ─────────────┘
```
- `frame.py` Frame = full-screen RGB rows of bytes + capture timestamp. Rects are full-frame `(x,y,w,h)`.
- `perception.py` readers return `(value, confidence, frame_ts)`: HP/MP bar fill (colour-run), slot
  ready/cooldown (brightness+saturation vs calibrated templates), target kind (enemy/none/friendly),
  HUD anchor template match, dialog/loading/death templates.
- `profile.py` calibration profile: source frame size, UI scale, regions, reference templates, SHA-256
  hash (tampering is detected; config can pin the hash). Frame-size mismatch raises `ProfileMismatch`.
- `decision.py` deterministic `Decider` (no clock, no randomness). Order: emergency heal (HP < X, or EWMA-slope
  predicted HP over horizon H < X) -> HoT/shield/buff upkeep -> damage only when safe (HP >= Y, enemy targeted,
  fresh + confident readings, no unknown state, MP reserve kept for the cheapest heal). Tracks cooldown/GCD/cast
  from config and cross-checks against the observed slot state (screen says cooldown => do not press).
  Heals are self-only: refused if an ally is targeted or the target reading is unclear.
- `safety.py` `Controller.tick()`: default OFF, enable needs an explicit toggle; hold-to-run default
  (`mode: "toggle"` optional). Emits nothing (and logs the reason) on: stale frame (> `max_stale_ms`, 250),
  future-dated frame, capture error, focus not AION2 / focus error, frame-size/profile mismatch, anchor missing,
  low-confidence hp/mp/state/anchor, NaN/inf/out-of-range HP/MP, dialog/loading/death, N consecutive presses with
  no cooldown response (desync, latched until explicit re-enable), watchdog stall, manual-input pause
  (`manual_pause_ms`), emergency stop (always disables immediately), rate cap (`max_keys_per_sec`, fixed spacing
  = 1/cap, no jitter). Any exception => disable, release every key (try/finally), log. Enabling is REFUSED when
  the focus probe is unavailable or the owner-input source cannot see manual input.
  One structured JSON log record per tick.
- `adapters.py` interfaces + mocks. `portal.py` real adapter source over an injectable D-Bus transport
  (ScreenCast, RemoteDesktop `NotifyKeyboardKeycode` down/up, KWin scripting focus query, GlobalShortcuts hotkeys),
  tested only against `FakeTransport`.
- `synth.py` / `replay.py` synthetic HUD frames and a replay simulator (scripted fight -> exact key sequence).

## Usage (offline)
```
python3 -m unittest -v
python3 -m aion2_assist calibrate --regions regions.json \
    --shot normal=a.png --shot cooldown=b.png --shot no_target=c.png [--shot friendly_target=..] \
    [--shot dialog=..] [--shot loading=..] [--shot death=..] --out profile.json
python3 -m aion2_assist read --profile profile.json fresh_screenshot.png   # verify readings
python3 -m aion2_assist check-config --config config.json                  # rejects UNSET_* keys
```

## Calibration steps the owner must do
1. Take FULL-FRAME screenshots (no crops; the profile records the source frame size and refuses other sizes) at
   the exact resolution and UI scale used in play:
   `normal` (HP/MP bars non-empty, enemy targeted, every bar slot ready), `cooldown` (all slots on cooldown),
   `no_target`; recommended: `friendly_target`, and one each of `dialog`, `loading`, `death` (otherwise those
   states are detected only by anchor absence).
2. Measure the rectangles (`regions.example.json`): anchor (a static, distinctive HUD element), hp_bar, mp_bar
   (tight to the bar interior), target indicator, one rect per skill slot, state rects.
3. Run `calibrate`, then `read` on fresh screenshots (including mid-cooldown-sweep and low HP) and check values.
4. Copy `config.example.json`; fill the real slots (key, role, cooldown/cast/gcd, mp_cost, priority, duration),
   mark level-locked slots `"enabled": false`, set `window_match` to the real window class/title, optionally pin
   `profile_hash`.

## Activation gates still required (in order)
1. Real screenshot calibration (above) with verified readings.
2. Real skill/bind config (no skill names or binds are invented here; `UNSET_*` keys are refused).
3. Exercise the real adapters on the target (first contact with real portals/bus/gst): consent dialogs for
   ScreenCast, RemoteDesktop, GlobalShortcuts; install the KWin focus package; measure capture latency.
4. Manual-input monitor LIVE on the Alienware plus the live check that injected keys are not seen by it.
5. The owner's explicit live trial.

## Dependencies (isolated venv only)
`python3 -m venv .venv` (gitignored); `.venv/bin/pip install -r requirements.txt` -> **jeepney==0.9.0** (pure Python,
no other deps). Only `session_bus.py` and its test need it; everything else is stdlib, and without jeepney that
test module is skipped. No global/--user installs.

## What is implemented vs stubbed
- `session_bus.JeepneyTransport`: persistent connection, owns a bus name, answers method calls to us (focus
  reports), tracks `Request::Response` and `Session::Closed` (accepted only from the current owner of
  org.freedesktop.portal.Desktop), subscribed signals, fd passthrough. Tested ONLY against a fake connection;
  `open_session_bus()` (the one real-bus entry point) is never called by tests.
- `pipewire.GStreamerPipeWireReader`: subprocess to `gst-launch-1.0 pipewiresrc ... ! fdsink` (stdlib). Devbox has no
  gst/pipewire tools, so never executed. Target requirement: `gst-launch-1.0` + `gst-plugin-pipewire`, width*3
  divisible by 4, capture at exactly the profile resolution (no scaling; mismatch fails closed). Frame timestamp =
  receipt time, so real latency is unmeasured.
- Portal adapters follow the xdg-desktop-portal docs: `persist_mode` 0, no `restore_token`, keyboard device only
  (pointer only with `pointer_buttons=True`), clipboard never requested and a grant that includes clipboard or
  anything not requested is refused and closed; a denied/cancelled Request raises `PortalDenied`, is never retried
  or auto-accepted; a `Session.Closed` stops capture/input/hotkeys (fail closed). GlobalShortcuts: CreateSession,
  BindShortcuts (toggle/emergency/hold; triggers are only *preferred*, the user confirms in the portal UI),
  Activated/Deactivated signals.
- `run` selects these adapters only after static live gates pass; all automated verification uses fakes.

## Focus (KWin) - corrected design
Earlier draft assumed D-Bus `loadScript`/`run`/`unloadScript` on `/Scripting`; those are NOT in the KWin docs
(develop.kde.org/docs/plasma/kwin/api/) and were removed. `workspace.activeWindow` is a KWin *scripting* property,
not a D-Bus method. Now: the owner installs the package in `kwin_script/aion2assist_focus` (documented route:
`kpackagetool6 --type=KWin/Script -i kwin_script/aion2assist_focus`, then
`kwriteconfig6 --file kwinrc --group Plugins --key aion2assist_focusEnabled true`, then
`qdbus org.kde.KWin /KWin reconfigure`). The script uses only documented API (`workspace.windowActivated`,
`workspace.activeWindow`, `Window.resourceClass/desktopFileName/caption`, `callDBus`) and pushes
`class|desktopFile|caption` to our bus name `org.aion2assist.focus` (`/focus`, `org.aion2assist.Focus.Report`).
`KWinFocusProbe` accepts reports only from the owner of `org.kde.KWin`. It also calls
`org.kde.kwin.Scripting.isScriptLoaded(pluginName)` on `/Scripting` at each query to detect a dead script. VERIFIED
against KWin master source (`src/scripting/scripting.h`: `Q_SCRIPTABLE bool isScriptLoaded(const QString &pluginName)`,
interface `org.kde.kwin.Scripting`; `scripting.cpp`: object registered at `/Scripting`, scripts keyed by the package
`KPlugin.Id`, loaded from `contents/code/main.js` when `X-Plasma-API` is `javascript` and `[Plugins] <Id>Enabled` is true) -
not on the docs page, and NOT against the Plasma version installed on the Alienware. Any error makes focus unavailable
(controller fails closed). `window_match` is a substring test on the combined string.

## Manual-input detection (evdev, read-only) - owner-authorised
Owner decision: the assist may read the owner's keyboard/mouse on the Alienware to know when to pause. User blizz is
already in group `input` and `/dev/input/event*` are `crw-rw-rw- root:input`, so no permission change is made.
`evdev_monitor.ManualInputMonitor` (stdlib `struct`, no extra dependency):
- opens event nodes `O_RDONLY|O_NONBLOCK|O_CLOEXEC` only; never grabs a device, never writes to one, never creates a
  virtual device (tests assert the flags, patch `os.write`/`fcntl.ioctl` to fail during operation, and scan the source
  for those APIs);
- selects devices from `/sys/class/input/event*/device/capabilities` (EV_KEY with letter/enter/space keys, or BTN_LEFT);
  virtual devices are excluded by default (`include_virtual`); rescans every 2 s for hot-plug;
- counts EV_KEY press/release/repeat of keys and mouse buttons (not gamepad/touch-tool codes) and keeps ONLY an activity boolean and the
  last-activity timestamp (no counters, key codes or raw bytes; a test asserts this), so it is not a keylogger; `MonitoredOwnerInput` turns a count into the `manual` edge, so the controller
  pauses on the very next tick for `manual_pause_ms`;
- reports UNKNOWN (and enable is refused / a running assist is disabled with `signal_lost:...`, keys released) when no
  keyboard device is readable, the thread dies, or the loop stalls (> `stall_s`).
Enable requires BOTH signals live: focus (KWin script reporting) and manual input (monitor LIVE).
Things the owner must set: the evdev codes of the assist's own toggle/emergency/hold hotkeys go in `ignore_codes`
(otherwise pressing them counts as manual play and pauses the assist).
**Live-verification gate (cannot be checked offline):** the assumption that the assist's injected keys (portal/libei into
KWin) never appear on a `/dev/input` node. `startup_self_check` sends nothing, confirms the monitor is live and that the
assist emitted no key during an idle window, and reports (without blaming the assist) any owner input seen; it returns
`live_verified=False` always. If the assumption is wrong the assist would pause itself (the fail-safe direction).

## Live run (`run`) - source complete, default-refusing
`orchestrator.py` wires the REAL adapters: JeepneyTransport (session bus) -> KWin focus receiver -> evdev monitor (must be
LIVE) -> GlobalShortcuts -> ScreenCast + GStreamerPipeWireReader -> RemoteDesktop keyboard sink -> Controller (starts OFF)
-> startup self-check (sends nothing). Teardown (try/finally + SIGTERM/SIGINT/SIGHUP handlers, idempotent, never raises)
releases every key FIRST, then closes the RemoteDesktop/ScreenCast/GlobalShortcuts sessions, the gst process, the monitor
and the bus. A loop watchdog thread stops the run if the main loop stalls; if it is wedged (e.g. blocked in a bus call) it
ends the process, which drops the bus connection and with it every portal session. Any portal session closing mid-run
stops everything (`portal_session_closed:<which>`).
`python3 -m aion2_assist run --config C --profile P --i-accept-live-risk [--gates-file F]` starts ONLY if all hold, else it
prints each missing gate and exits 2 (startup failure after the gates: exit 3):
1. `--i-accept-live-risk`; 2. config valid for run (real key names, no UNSET); 3. profile loads with a matching internal
hash AND the config's `profile_hash` pins that hash; 4. explicit non-empty `window_match`; 5. explicit non-empty
`ignore_codes`; 6. gates file (default `~/.config/aion2-assist/LIVE-APPROVED`: owned by you, not group/world-writable,
contains the profile hash; the owner writes it, or runs `approve --profile P --i-accept-live-risk`); 7. the startup
self-check passes (live read-only monitor, assist sent nothing).
Even after start the controller stays OFF until the toggle hotkey (hold mode also needs the hold hotkey held).

## KWin 6.7.5 recheck (read-only source fetch of the `v6.7.5` tag URLs, all resolved with no 404)
`scripting.h`: interface `org.kde.kwin.Scripting`, `Q_SCRIPTABLE bool isScriptLoaded(const QString &pluginName) const`,
`loadScript`, `unloadScript`; `scripting.cpp`: registered at `/Scripting`, scripts keyed by KPlugin Id, loaded from
`contents/code/main.js` for `X-Plasma-API` `javascript`, enable key `<Id>Enabled` in `[Plugins]`, `callDBus` exposed to
scripts; `workspace_wrapper.h`: `Q_PROPERTY(KWin::Window *activeWindow ... NOTIFY windowActivated)`, signal
`windowActivated(KWin::Window *)`, no `activeClient`; `window.h`: read-only `resourceClass`, `caption`, `desktopFileName`.
All match the package. (The fetch tool cannot echo a tag/commit id, so "tag v6.7.5" is established only by the URL resolving.)

## Offline CLI modes (mock sink; nothing is ever sent anywhere)
```
python3 -m aion2_assist replay --demo                       # bundled synthetic timeline, zero owner inputs
python3 -m aion2_assist replay TIMELINE.json|FRAMES_DIR --config C --profile P [--dt 0.1] [--json]
python3 -m aion2_assist dry-run --demo                      # synthetic PNG screenshots through the PNG loader
python3 -m aion2_assist dry-run --frames DIR --config C --profile P [--dt 1.0]
```
Both run the real Controller with a fake clock and mock adapters, print every decision and suppression reason and the
emitted key sequence. Frames-dir modes toggle the mock owner on at the first frame and keep the hold key held; the
watchdog is widened to the fake-clock spacing. Timeline JSON: `{"dt":0.1,"hold":true,"steps":[{"n":3,"hp":0.3,
"cooldown":["s2"],"target":"enemy","state":"dialog","anchor":false,"age":0.5,"ctl":["toggle"]}, ...]}`.
The bundled demo config/profile are synthetic and are NOT the owner's binds.

## Stub audit
No `NotImplementedError` remains. Interfaces (`FrameSource`, `FocusProbe`, `InputSink`, `OwnerInput`, `DBusTransport`,
`PipeWireReader`) are ABCs with `@abstractmethod` (cannot be instantiated). Every real adapter is implemented source;
the remaining "stub-like" limits are listed under Remaining blockers.

## Binds
The AION2 files on the Alienware (`Saved_Steam/Config/Windows`: Input.ini and EnhancedInput.ini are 2 bytes,
GameUserSettings.ini has no keys) do not hold skill binds (likely server-side), so none are invented.
`python3 -m aion2_assist binds template binds.json --profile profile.json` writes every calibrated slot position with a
blank key/role and `enabled: false`; fill it in, then `binds validate binds.json [--profile profile.json]` refuses
blank/UNSET keys, names that are not real pressable keys (digits, letters, F1-F12, TAB, SPACE, GRAVE, MINUS, EQUAL;
no chords), duplicates, bad roles, unknown slots and a profile-hash mismatch. `check-config` applies the same key rule.

## Remaining blockers (exact)
1. Live verification that injected keys are invisible to `/dev/input` (self-check cannot prove it offline).
2. Real-bus behaviour of `JeepneyTransport`, `gst-launch-1.0` availability/latency on the Alienware, the KWin script
   package on the installed Plasma version: none verifiable offline (`isScriptLoaded` itself is verified in KWin source).
3. Owner inputs: real calibration, the filled-in binds/config, `window_match`, `ignore_codes` for the control hotkeys.

## Cannot be verified offline
Whether Proton/XWayland accepts portal (libei) keyboard input; NC detection behaviour; real capture latency and
timestamps (`max_stale_ms` 250 is a guess); real cast/GCD/cooldown timings and HUD response delay (`response_ms`,
`desync_n`); HUD variance (scaling, effects, transparency, dialogs over the HUD, cooldown-sweep rendering, buff
icons); whether a heal with an enemy targeted still lands on self; actual window class/title; GlobalShortcuts hold
semantics with the real keys; KWin scripting API differences between Plasma versions.


## STANDING OWNER INSTRUCTION (Blizz 12:27 AM CST Nov 12 2026)

"Here’s what I want you to do: proceed autonomously until you’ve finished this Aion 2 PRoject as 100% automated, includes qusets and combat buying selling etc. 7t. I want you to monitor your own progress and evaluate whether the result aligns with what’s expected. Feel free to consult any sources you need [solution specific details and past tries] I want you to assess everything and implement the necessary changes, you have full authority to correct or alter anything that’s already been done. Keep in mind that we have specific needs feel free to rework that system if you deem it necessary. Organize your work however you see fit, investigate, reinvent, and innovate. You have the freedom to do all of this, and to create any tools or algorithms required to reach the goal. Get started"
