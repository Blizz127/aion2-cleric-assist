"""Live-session orchestrator: wires the REAL adapters. It must only be started through `run` (default-refusing gates).

Startup order (each step can refuse; teardown always runs, in reverse, even on exception/signal):
  session bus -> KWin focus receiver -> evdev monitor (must go LIVE) -> GlobalShortcuts -> ScreenCast capture
  -> RemoteDesktop keyboard sink -> Controller (starts OFF) -> startup self-check (sends nothing).
The controller stays OFF until the owner presses the toggle hotkey (hold mode additionally needs the hold hotkey).
"""
import os
import signal
import threading
import time
from dataclasses import dataclass
from typing import Callable, List, Optional

from . import gates as gates_mod
from .adapters import FocusProbe
from .config import Config
from .evdev_monitor import ManualInputMonitor, MonitoredOwnerInput, startup_self_check
from .portal import (DBusTransport, GlobalShortcutsOwnerInput, KWinFocusProbe, PortalRemoteDesktopSink,
                     PortalScreenCastSource, SESSION_IFACE)
from .profile import Profile
from .safety import Controller, json_logger


class StartupRefused(Exception):
    """A startup step refused to proceed (e.g. monitor not live, self-check failed)."""
    def __init__(self, gate, why):
        super().__init__(f"{gate}: {why}")
        self.gate, self.why = gate, why


@dataclass
class Factories:
    transport: Callable[[], DBusTransport]
    reader: Callable[[Callable], object]                       # (clock) -> PipeWireReader
    monitor: Callable[[Callable, tuple], ManualInputMonitor]   # (clock, ignore_codes) -> monitor
    clock: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep


def real_factories() -> Factories:
    """The ONLY place real adapters are chosen. Imported lazily (needs jeepney from the venv)."""
    from .pipewire import GStreamerPipeWireReader
    from .session_bus import open_session_bus
    return Factories(transport=open_session_bus, reader=lambda clock: GStreamerPipeWireReader(clock),
                     monitor=lambda clock, ignore: ManualInputMonitor(clock, ignore_codes=ignore))


class Watchdog:
    """Trips when the main loop's heartbeat goes stale. Stage 1 (`on_trip`): request stop + disable. Stage 2 (`on_hard`):
    the loop is wedged (e.g. blocked in a bus call), so the process is ended; dropping the bus connection ends every
    portal session, which releases any held key on the compositor side."""
    def __init__(self, heartbeat: Callable[[], float], clock, on_trip, on_hard, timeout_s=1.0, hard_s=2.0):
        self.heartbeat, self.clock, self.on_trip, self.on_hard = heartbeat, clock, on_trip, on_hard
        self.timeout_s, self.hard_s = timeout_s, hard_s
        self.tripped = self.hard_done = False
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def check(self):
        age = self.clock() - self.heartbeat()
        if age > self.timeout_s and not self.tripped:
            self.tripped = True
            self.on_trip(age)
        if age > self.timeout_s + self.hard_s and not self.hard_done:
            self.hard_done = True
            self.on_hard(age)

    def start(self, period_s=0.25):
        def loop():
            while not self._stop.wait(period_s):
                self.check()
        self._thread = threading.Thread(target=loop, name="orchestrator-watchdog", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(1.0)


class Orchestrator:
    def __init__(self, cfg: Config, profile: Profile, factories: Factories, log=None, tick_s=0.05,
                 self_check_s=1.0, monitor_wait_s=3.0, watchdog=True, watchdog_timeout_s=1.0, watchdog_hard_s=2.0, hard_exit=os._exit):
        self.cfg, self.profile, self.f = cfg, profile, factories
        self.clock, self.sleep = factories.clock, factories.sleep
        self.log = log or (lambda rec: None)
        self.tick_s, self.self_check_s, self.monitor_wait_s = tick_s, self_check_s, monitor_wait_s
        self.use_watchdog, self.watchdog_timeout_s, self.hard_exit = watchdog, watchdog_timeout_s, hard_exit
        self.watchdog_hard_s = watchdog_hard_s
        self.stop_event = threading.Event()
        self.stop_reason: Optional[str] = None
        self.heartbeat = self.clock()
        self.teardown_errors: List[tuple] = []
        self.transport = self.focus = self.monitor = self.hotkeys = self.owner = None
        self.reader = self.capture = self.sink = self.ctl = self.wd = None
        self._torn_down = False
        self.self_check = None

    def _event(self, name, **kw):
        self.log({"t": round(self.clock(), 4), "event": "lifecycle", "reason": name, **kw})

    # ---- startup ---------------------------------------------------------------
    def start(self):
        cfg, f = self.cfg, self.f
        self.transport = f.transport()
        self.focus = KWinFocusProbe(self.transport)
        self.focus.open()
        self._event("focus_receiver_open")
        self.monitor = f.monitor(self.clock, cfg.ignore_codes)
        self.monitor.start()
        deadline = time.monotonic() + self.monitor_wait_s
        while not self.monitor.status().live and time.monotonic() < deadline:
            time.sleep(0.01)
        st = self.monitor.status()
        if not st.live:
            raise StartupRefused("manual_input_monitor", st.reason)
        self._event("manual_input_monitor_live")
        self.hotkeys = GlobalShortcutsOwnerInput(self.transport, cfg.hotkey_triggers)
        self.hotkeys.open()                       # portal consent UI (owner)
        self.owner = MonitoredOwnerInput(self.hotkeys, self.monitor)
        self.reader = f.reader(self.clock)
        self.capture = PortalScreenCastSource(self.transport, self.reader, self.profile.frame_size, self.clock)
        self.capture.open()                       # portal consent UI (owner)
        self.sink = PortalRemoteDesktopSink(self.transport, sleep=self.sleep)  # keyboard only
        self.sink.open()                          # portal consent UI (owner)
        self.ctl = Controller(cfg, self.profile, self.capture, self.focus, self.sink, self.owner, self.clock, self.log)
        sc = startup_self_check(self.monitor, self.sink, self.sleep, self.self_check_s)
        self.self_check = sc
        if not sc.ok:
            raise StartupRefused("self_check", sc.note)
        self._event("started", controller="OFF", self_check=sc.note, live_verified=sc.live_verified)

    # ---- run -------------------------------------------------------------------
    def request_stop(self, reason):
        if self.stop_reason is None:
            self.stop_reason = reason
        if self.ctl is not None:
            self.ctl.enabled = False  # plain flag: safe from a signal handler / watchdog thread
        self.stop_event.set()

    def _sessions_closed(self):
        for name, obj in (("screencast", self.capture), ("remote_desktop", self.sink), ("global_shortcuts", self.hotkeys)):
            s = getattr(obj, "session", None)
            if s is None or self.transport.is_closed(s):
                return name
        return None

    def run(self, max_ticks=None, after_tick=None, install_signals=True):
        """start -> loop -> teardown (always). Returns the stop reason."""
        prev = self._install_signals() if install_signals else {}
        try:
            self.start()
            self.heartbeat = self.clock()
            if self.use_watchdog:
                self.wd = Watchdog(lambda: self.heartbeat, self.clock, self._wd_trip, self._wd_hard, self.watchdog_timeout_s,
                                 self.watchdog_hard_s)
                self.wd.start()
            i = faults = 0
            while not self.stop_event.is_set():
                self.heartbeat = self.clock()
                closed = self._sessions_closed()
                if closed:
                    self.request_stop(f"portal_session_closed:{closed}")
                    break
                rec = self.ctl.tick()
                faults = faults + 1 if rec.get("reason") == "exception_fail_closed" else 0
                if faults >= 3:
                    self.request_stop("repeated_exceptions")
                    break
                if after_tick:
                    after_tick(i, rec)
                i += 1
                if max_ticks is not None and i >= max_ticks:
                    self.request_stop("max_ticks")
                    break
                self.sleep(self.tick_s)
            return self.stop_reason
        finally:
            try:
                self.teardown()
            finally:
                self._restore_signals(prev)

    def _wd_trip(self, age):
        self._event("watchdog_stall", age_s=round(age, 3))
        self.request_stop("watchdog_stall")

    def _wd_hard(self, age):
        self._event("watchdog_hard_exit", age_s=round(age, 3))
        self.hard_exit(3)

    # ---- signals / teardown ------------------------------------------------------
    def _install_signals(self):
        if threading.current_thread() is not threading.main_thread():
            return {}
        def handler(signum, _frame):
            self.request_stop(f"signal:{signal.Signals(signum).name}")
        return {s: signal.signal(s, handler) for s in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)}

    @staticmethod
    def _restore_signals(prev):
        for s, h in prev.items():
            signal.signal(s, h)

    def teardown(self):
        """Idempotent. Releases every key FIRST, then closes sessions, helpers, monitor, bus. Never raises."""
        if self._torn_down:
            return
        self._torn_down = True
        self.stop_event.set()

        def step(name, fn):
            try:
                fn()
            except BaseException as e:  # keep going: every later step still matters
                self.teardown_errors.append((name, type(e).__name__))
        if self.wd:
            step("watchdog.stop", self.wd.stop)
        if self.ctl:
            step("controller.shutdown", self.ctl.shutdown)       # disable + release_all
        elif self.sink:
            step("sink.release_all", self.sink.release_all)
        if self.sink:
            step("sink.close", self.sink.close)                  # releases again, closes RemoteDesktop session
        if self.capture:
            step("capture.close", self.capture.close)
        if self.reader:
            step("reader.close", self.reader.close)
        if self.hotkeys:
            step("hotkeys.close", self.hotkeys.close)
        if self.monitor:
            step("monitor.stop", self.monitor.stop)
        if self.transport:
            step("transport.close", self.transport.close)
        self._event("teardown_done", errors=[list(e) for e in self.teardown_errors])


# ---- the `run` command ------------------------------------------------------------
def live_main(args, factories: Optional[Factories] = None, out=print, err=None) -> int:
    """Exit 0 clean stop, 2 gate(s) missing, 3 startup failed. Adapters are only created after every static gate passes."""
    import sys
    err = err or (lambda m: print(m, file=sys.stderr))
    gates, cfg, prof = gates_mod.evaluate_static(args.i_accept_live_risk, args.config, args.profile, args.gates_file)
    if any(not g.ok for g in gates):
        gates.append(gates_mod.not_attempted_self_check())
        err(gates_mod.report(gates))
        return 2
    orch = Orchestrator(cfg, prof, factories or real_factories(), log=json_logger(), tick_s=args.tick_ms / 1000.0,
                        self_check_s=args.self_check_s)
    try:
        reason = orch.run()
    except StartupRefused as e:
        err(f"REFUSING TO START: 1 gate(s) missing\n  [MISSING] {e.gate}: {e.why}" if e.gate == "self_check"
            else f"startup refused at {e.gate}: {e.why}")
        return 2 if e.gate == "self_check" else 3
    except Exception as e:
        err(f"startup/run failed: {type(e).__name__}: {e}")
        return 3
    out(f"stopped: {reason}")
    return 0
