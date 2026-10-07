# aion2-cleric-assist (OFFLINE prototype)

Solo auto-cast assist for an Aion 2 Cleric on Bazzite/KDE Wayland (Steam appid 3393110, Proton).

## OFFLINE ONLY. Live use risks an NC "Integrated Account Ban".
Nothing here has been run against the game, the portals, or the network. The owner has accepted the
account-ban risk for any future live trial; that trial is still an explicit, separate step. There is no `run`
command, no live adapter that can start (the real D-Bus transport is a stub), and the controller starts
**OFF**. Pure stdlib Python 3.12 (numpy and PIL are not installed; a minimal PNG reader is used, PIL is
used automatically if present).

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
3. Real portal capture and input adapters: a persistent session-bus transport (see blockers) plus a PipeWire frame
   reader, then portal consent dialogs (ScreenCast, RemoteDesktop, GlobalShortcuts).
4. A manual-input signal and a focus signal that actually work (see blockers) — until then the controller refuses to enable.
5. The owner's explicit live trial.

## What is stubbed
`SessionBusTransport` (real D-Bus), `PipeWireReader` (frames from PipeWire). Everything else in `portal.py` is
implemented but only exercised against `FakeTransport`.

## Blockers (stated exactly)
- **Manual-input detection**: no user-session API on KDE Wayland reports other applications' key/mouse events
  (RemoteDesktop/libei is output-only; GlobalShortcuts only fires for shortcuts we register; evdev/uinput/root are
  out of scope). `GlobalShortcutsOwnerInput.provides_manual_input = False`, so the controller REFUSES to enable with
  the real stack. Nothing is substituted. Resolving it needs an owner-approved signal.
- **Focus detection**: the KWin scripting route needs a process that owns a D-Bus name and receives the script's
  `callDBus` report. Only `gdbus`/`busctl` (one connection per call) exist; a D-Bus library is not installed and
  installing one is out of scope. `KWinFocusProbe.available` follows `transport.supports_receive` (False for the stub).
- **Persistent portal sessions**: portal sessions die with the creating connection, so per-call `gdbus` cannot hold one.
- Pointing at the right window is by `window_match` substring only (class/title unknown until observed).

## Cannot be verified offline
Whether Proton/XWayland accepts portal (libei) keyboard input; NC detection behaviour; real capture latency and
timestamps (`max_stale_ms` 250 is a guess); real cast/GCD/cooldown timings and HUD response delay (`response_ms`,
`desync_n`); HUD variance (scaling, effects, transparency, dialogs over the HUD, cooldown-sweep rendering, buff
icons); whether a heal with an enemy targeted still lands on self; actual window class/title; GlobalShortcuts hold
semantics with the real keys; KWin scripting API differences between Plasma versions.
