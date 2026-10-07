# aion2-cleric-assist (OFFLINE prototype)

Solo auto-cast assist for an Aion 2 Cleric on Bazzite/KDE Wayland (Steam appid 3393110, Proton).

## OFFLINE ONLY. Live use risks an NC "Integrated Account Ban".
Nothing here has been run against the game, the portals, or the network. The owner has accepted the
account-ban risk for any future live trial; that trial is still an explicit, separate step. There is no `run`
command, no live adapter that can start (the real transport exists only as source tested against a fake connection), and the controller starts
**OFF**. Core is stdlib Python 3.12 (numpy/PIL not installed; minimal PNG reader). Optional venv dep: jeepney (below).

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
4. A manual-input signal (UNKNOWN today, see above) - until then the controller refuses to enable.
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
- No `run` command exists; nothing starts any of this.

## Focus (KWin) - corrected design
Earlier draft assumed D-Bus `loadScript`/`run`/`unloadScript` on `/Scripting`; those are NOT in the KWin docs
(develop.kde.org/docs/plasma/kwin/api/) and were removed. `workspace.activeWindow` is a KWin *scripting* property,
not a D-Bus method. Now: the owner installs the package in `kwin_script/aion2assist_focus` (documented route:
`kpackagetool6 --type=KWin/Script -i kwin_script/aion2assist_focus`, then
`kwriteconfig6 --file kwinrc --group Plugins --key aion2assist_focusEnabled true`, then
`qdbus org.kde.KWin /KWin reconfigure`). The script uses only documented API (`workspace.windowActivated`,
`workspace.activeWindow`, `Window.resourceClass/desktopFileName/caption`, `callDBus`) and pushes
`class|desktopFile|caption` to our bus name `org.aion2assist.focus` (`/focus`, `org.aion2assist.Focus.Report`).
`KWinFocusProbe` accepts reports only from the owner of `org.kde.KWin`. Residual, UNVERIFIED: it also calls
`org.kde.kwin.Scripting.isScriptLoaded` at each query to detect a dead script; that method is not in the fetched docs,
and any error there makes focus unavailable (controller fails closed). Package metadata/layout is unverified on a
real Plasma 6 install. `window_match` is a substring test on the combined string.

## Manual-input detection: UNKNOWN (hard no-enable gate)
`provides_manual_input` is False and `Controller` refuses to enable (`enable_refused:no_manual_input_signal`). Nothing
substitutes for it. What would be required: a user-session API that delivers *global* keyboard/mouse events of the
owner while another app has focus. None exists among the approved ones: RemoteDesktop/libei is output-only;
GlobalShortcuts fires only for shortcuts we register (and grabbing the game binds would swallow them);
InputCapture portal captures input only after a pointer-barrier activation and is for forwarding, not monitoring;
KWin scripts expose no raw key stream. Candidates outside the approved set (not implemented, need owner decision):
libinput/evdev reading (`input` group or root), or a compositor-side extension. Until such a signal exists, enable is
impossible by design.

## Remaining blockers (exact)
1. Manual-input signal: UNKNOWN, no approved API (above). Hard gate.
2. Real-bus behaviour of `JeepneyTransport`, `gst-launch-1.0` availability/latency on the Alienware, and the KWin
   script package + `isScriptLoaded`: none verifiable offline.
3. Real calibration, real skill/bind config, `window_match` value (all owner inputs).

## Cannot be verified offline
Whether Proton/XWayland accepts portal (libei) keyboard input; NC detection behaviour; real capture latency and
timestamps (`max_stale_ms` 250 is a guess); real cast/GCD/cooldown timings and HUD response delay (`response_ms`,
`desync_n`); HUD variance (scaling, effects, transparency, dialogs over the HUD, cooldown-sweep rendering, buff
icons); whether a heal with an enemy targeted still lands on self; actual window class/title; GlobalShortcuts hold
semantics with the real keys; KWin scripting API differences between Plasma versions.
