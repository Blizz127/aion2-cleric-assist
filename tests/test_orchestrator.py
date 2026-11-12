"""Integration: the full live flow with EVERY real adapter replaced by a fake at the transport/device level.
Nothing here touches a bus, portal, capture, /dev/input, the game, or the network (signals are sent to this process only)."""
import argparse
import json
import os
import signal
import stat
import tempfile
import threading
import time
import unittest

from aion2_assist import gates as G
from aion2_assist.orchestrator import Factories, Orchestrator, StartupRefused, Watchdog, live_main
from aion2_assist.portal import FakeTransport, Incoming, PipeWireReader
from aion2_assist.replay import FakeClock
from aion2_assist.synth import make_frame
from tests.helpers import DMG_ONLY, PROFILE, cfg
from tests.test_evdev_monitor import KB_KEYS, FakeSystem

CAST, RD, GS = "/p/session/cast", "/p/session/rd", "/p/session/gs"
KWIN = ":1.20"
GSI = "org.freedesktop.portal.GlobalShortcuts"


def make_transport(over=None):
    sc, rdi = "org.freedesktop.portal.ScreenCast", "org.freedesktop.portal.RemoteDesktop"
    t = FakeTransport({
        (sc, "CreateSession"): {"session_handle": CAST}, (rdi, "CreateSession"): {"session_handle": RD},
        (GSI, "CreateSession"): {"session_handle": GS},
        (sc, "Start"): {"streams": [(42, {})]}, "OpenPipeWireRemote": 7, (rdi, "Start"): {"devices": 1},
        "BindShortcuts": {"shortcuts": [("toggle", {}), ("emergency", {}), ("hold", {})]}, "isScriptLoaded": True,
        **(over or {})})
    t.owners["org.kde.KWin"] = KWIN
    t.incoming["Report"] = [Incoming(KWIN, ("steam_app_3393110|steam|AION 2",))]
    return t


class FakeReader(PipeWireReader):
    def __init__(self, clock):
        self.clock, self.closed, self.frame_kw = clock, False, {}

    def read(self, node_id, fd, size):
        assert (node_id, fd, size) == (42, 7, (320, 180))
        return make_frame(self.clock(), **self.frame_kw)

    def close(self):
        self.closed = True


class Rig:
    def __init__(self, fs=None, transport=None, clock=None, sleep=None, **orch_kw):
        self.fs = fs or FakeSystem()
        if fs is None:
            self.fs.add("event0", KB_KEYS)
        self.clock = clock or FakeClock()
        self.t = transport or make_transport()
        self.logs, self.monitors, self.readers = [], [], []

        def mon(_clock, ignore):
            m = self.fs.monitor(time.monotonic, ignore_codes=ignore)
            self.monitors.append(m)
            return m

        def rdr(clock):
            r = FakeReader(clock)
            self.readers.append(r)
            return r
        sleep = sleep or (lambda s: setattr(self.clock, "t", round(self.clock.t + s, 6)))
        f = Factories(transport=lambda: self.t, reader=rdr, monitor=mon, clock=self.clock, sleep=sleep)
        c = cfg(DMG_ONLY, window_match="AION 2", ignore_codes=[67, 68, 87], profile_hash=PROFILE.hash,
                watchdog_ms=10 ** 6, **orch_kw.pop("cfg_over", {}))
        kw = dict(tick_s=0.05, self_check_s=0.1, monitor_wait_s=2.0, watchdog=False)
        kw.update(orch_kw)
        self.o = Orchestrator(c, PROFILE, f, log=self.logs.append, **kw)

    def notes(self):  # (keycode, state) of every injected keyboard call, in order
        return [(c["args"][2], c["args"][3]) for c in self.t.calls if c["method"] == "NotifyKeyboardKeycode"]

    def closes(self):
        return [c["path"] for c in self.t.calls if c["method"] == "Close"]

    def events(self):
        return [r["reason"] for r in self.logs if r["event"] == "lifecycle"]

    def enable_hotkeys(self, toggle=True, hold=True):
        sig = []
        if hold:
            sig.append((GS, "hold", 1, {}))
        if toggle:
            sig.append((GS, "toggle", 2, {}))
        self.t.signals[(GSI, "Activated")] = sig


class FullFlow(unittest.TestCase):
    def test_startup_enable_decide_emit_teardown(self):
        r = Rig()

        def after(i, rec):
            if i == 1:
                r.enable_hotkeys()
        reason = r.o.run(max_ticks=24, after_tick=after)
        self.assertEqual(reason, "max_ticks")
        # startup sequence: consent-bearing portals in the documented order
        creates = [c["iface"].rsplit(".", 1)[1] for c in r.t.calls if c["method"] == "CreateSession"]
        self.assertEqual(creates, ["GlobalShortcuts", "ScreenCast", "RemoteDesktop"])
        self.assertEqual(r.t.owned, ["org.aion2assist.focus"])
        self.assertEqual(r.events(), ["focus_receiver_open", "manual_input_monitor_live", "started", "teardown_done"])
        # controller stayed OFF until the toggle; then emitted real key down/up pairs for key "4" (evdev 5)
        decisions = [x for x in r.logs if x["event"] != "lifecycle"]
        self.assertEqual([d["reason"] for d in decisions[:2]], ["disabled", "disabled"])
        notes = r.notes()
        self.assertGreaterEqual(len(notes), 6)
        self.assertEqual(notes, [(5, 1), (5, 0)] * (len(notes) // 2))
        # teardown: sessions closed, helpers stopped, bus dropped
        self.assertEqual(sorted(r.closes()), sorted([RD, CAST, GS]))
        self.assertTrue(r.readers[0].closed)
        self.assertFalse(r.monitors[0].thread.is_alive())
        self.assertTrue(r.t.transport_closed)
        self.assertEqual(r.o.teardown_errors, [])

    def test_controller_off_until_toggle_hold_alone_emits_nothing(self):
        r = Rig()
        r.o.run(max_ticks=15, after_tick=lambda i, rec: r.enable_hotkeys(toggle=False) if i == 1 else None)
        self.assertEqual(r.notes(), [])

    def test_owner_keypress_pauses_in_full_flow(self):
        from tests.test_evdev_monitor import ev
        r = Rig()

        def after(i, rec):
            if i == 1:
                r.enable_hotkeys()
            if i == 12:
                r.fs.send("event0", ev(1, 30, 1))
                time.sleep(0.3)  # let the real monitor thread read it
        r.o.run(max_ticks=16, after_tick=after)
        reasons = [x["reason"] for x in r.logs if x["event"] != "lifecycle"]
        self.assertIn("manual_pause", reasons)

    def test_ignored_hotkey_codes_do_not_count_as_manual(self):
        from tests.test_evdev_monitor import ev
        r = Rig()

        def after(i, rec):
            if i == 1:
                r.enable_hotkeys()
            if i == 12:
                r.fs.send("event0", ev(1, 67, 1), ev(1, 67, 0))
                time.sleep(0.3)
        r.o.run(max_ticks=16, after_tick=after)
        self.assertNotIn("manual_pause", [x["reason"] for x in r.logs])


class Teardown(unittest.TestCase):
    def test_exception_releases_keys_and_closes_everything(self):
        r = Rig()

        def after(i, rec):
            if i == 2:
                r.o.sink.down.add("4")  # a key is down at the moment of failure
                raise RuntimeError("boom")
        with self.assertRaises(RuntimeError):
            r.o.run(max_ticks=50, after_tick=after)
        self.assertEqual(r.notes()[-1], (5, 0))
        self.assertEqual(r.o.sink.down, set())
        self.assertEqual(sorted(r.closes()), sorted([RD, CAST, GS]))
        self.assertTrue(r.t.transport_closed)
        self.assertFalse(r.monitors[0].thread.is_alive())
        self.assertFalse(r.o.ctl.enabled)

    def test_sigterm_releases_keys_stops_and_restores_handlers(self):
        r = Rig()
        before = {s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)}
        seen = []

        def after(i, rec):
            seen.append(i)
            if i == 3:
                r.o.sink.down.add("4")
                os.kill(os.getpid(), signal.SIGTERM)  # to this process only
        reason = r.o.run(max_ticks=50, after_tick=after)
        self.assertEqual(reason, "signal:SIGTERM")
        self.assertEqual(seen, [0, 1, 2, 3])
        self.assertEqual(r.notes()[-1], (5, 0))
        self.assertEqual(sorted(r.closes()), sorted([RD, CAST, GS]))
        self.assertEqual({s: signal.getsignal(s) for s in before}, before)

    def test_teardown_is_idempotent_and_never_raises(self):
        r = Rig()
        r.o.run(max_ticks=2)
        r.t.calls.clear()
        r.o.teardown()
        self.assertEqual(r.t.calls, [])

    def test_startup_failure_midway_cleans_up_what_was_opened(self):
        t = make_transport({("org.freedesktop.portal.RemoteDesktop", "Start"): {"devices": 3}})  # asks beyond keyboard
        r = Rig(transport=t)
        from aion2_assist.portal import PortalDenied
        with self.assertRaises(PortalDenied):
            r.o.run()
        self.assertEqual(r.notes(), [])
        self.assertIn(CAST, r.closes())
        self.assertIn(GS, r.closes())
        self.assertTrue(t.transport_closed)
        self.assertFalse(r.monitors[0].thread.is_alive())

    def test_monitor_not_live_refuses_before_any_portal_dialog(self):
        r = Rig(fs=FakeSystem(), monitor_wait_s=0.1)  # no keyboard device at all
        with self.assertRaises(StartupRefused) as cm:
            r.o.run()
        self.assertEqual(cm.exception.gate, "manual_input_monitor")
        self.assertEqual([c["method"] for c in r.t.calls if c["method"] == "CreateSession"], [])  # no consent UI shown
        self.assertTrue(r.t.transport_closed)

    def test_self_check_failure_refuses_and_tears_down(self):
        def sleep(s):
            if s == 0.777:  # inside the self-check window the monitor dies
                r.monitors[0].error = RuntimeError("died")
        r = Rig(sleep=sleep, self_check_s=0.777)
        with self.assertRaises(StartupRefused) as cm:
            r.o.run()
        self.assertEqual(cm.exception.gate, "self_check")
        self.assertEqual(r.notes(), [])
        self.assertEqual(sorted(r.closes()), sorted([RD, CAST, GS]))


class SessionLoss(unittest.TestCase):
    def test_each_portal_session_closed_midrun_stops_everything(self):
        for name, path in (("remote_desktop", RD), ("screencast", CAST), ("global_shortcuts", GS)):
            r = Rig()

            def after(i, rec, r=r, path=path):
                if i == 1:
                    r.enable_hotkeys()
                if i == 10:
                    r.t.closed.add(path)
            reason = r.o.run(max_ticks=200, after_tick=after)
            self.assertEqual(reason, f"portal_session_closed:{name}")
            self.assertFalse(r.o.ctl.enabled)
            self.assertEqual(r.o.sink.down, set())
            self.assertTrue(r.t.transport_closed)
            n = len(r.notes())
            self.assertLess(n, 40)


class WatchdogTests(unittest.TestCase):
    def test_two_stage_trip(self):
        clock, beat, calls = FakeClock(), [0.0], []
        w = Watchdog(lambda: beat[0], clock, lambda a: calls.append(("trip", a)), lambda a: calls.append(("hard", a)),
                     timeout_s=1.0, hard_s=2.0)
        clock.t = 0.9
        w.check()
        self.assertEqual(calls, [])
        clock.t = 1.5
        w.check()
        w.check()
        self.assertEqual(calls, [("trip", 1.5)])
        clock.t = 3.5
        w.check()
        w.check()
        self.assertEqual([c[0] for c in calls], ["trip", "hard"])

    def test_orchestrator_trip_and_hard_exit_hooks(self):
        hard = []
        r = Rig(hard_exit=hard.append)
        r.o._wd_trip(1.5)
        self.assertEqual(r.o.stop_reason, "watchdog_stall")
        self.assertTrue(r.o.stop_event.is_set())
        r.o._wd_hard(4.0)
        self.assertEqual(hard, [3])

    def test_real_thread_watchdog_stops_a_stalled_loop(self):
        hard = []
        r = Rig(clock=type("C", (), {"__call__": lambda s: time.monotonic()})(), sleep=lambda s: None,
                watchdog=True, watchdog_timeout_s=0.1, watchdog_hard_s=30.0, hard_exit=hard.append)

        def after(i, rec):
            if i == 3:
                time.sleep(0.6)  # the loop wedges; the watchdog thread requests stop
        reason = r.o.run(max_ticks=500, after_tick=after)
        self.assertEqual(reason, "watchdog_stall")
        self.assertEqual(hard, [])


def write_files(d, **over):
    prof = os.path.join(d, "profile.json")
    json.dump(PROFILE.data, open(prof, "w"))
    conf = {"window_match": "AION 2", "ignore_codes": [67, 68, 87], "profile_hash": PROFILE.hash,
            "slots": {"s4": {"key": "4", "role": "dmg"}}}
    conf.update(over.pop("config", {}))
    cpath = os.path.join(d, "config.json")
    json.dump(conf, open(cpath, "w"))
    gf = os.path.join(d, "LIVE-APPROVED")
    open(gf, "w").write(f"# approved\n{PROFILE.hash}\n")
    os.chmod(gf, 0o600)
    return cpath, prof, gf


def by_name(gates):
    return {g.name: g for g in gates}


class RefusalGates(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = self.tmp.name

    def test_all_good_passes_static_gates(self):
        c, p, gf = write_files(self.d)
        gates, cfg_, prof = G.evaluate_static(True, c, p, gf)
        self.assertTrue(all(g.ok for g in gates), G.report(gates))

    def missing(self, flag=True, config=None, profile_mutator=None, gates_mutator=None):
        c, p, gf = write_files(self.d, config=config or {})
        if profile_mutator:
            profile_mutator(p)
        if gates_mutator:
            gates_mutator(gf)
        gates, _, _ = G.evaluate_static(flag, c, p, gf)
        return {g.name for g in gates if not g.ok}

    def test_each_gate_individually(self):
        self.assertEqual(self.missing(flag=False), {"accept_live_risk"})
        self.assertEqual(self.missing(config={"slots": {"s4": {"key": "UNSET_4", "role": "dmg"}}}),
                         {"config_valid", "profile_hash_pinned_and_matches", "window_match_set", "ignore_codes_set"})
        self.assertEqual(self.missing(config={"slots": {"s4": {"key": "CTRL+4", "role": "dmg"}}}) & {"config_valid"}, {"config_valid"})
        c = {"window_match": ""}
        self.assertEqual(self.missing(config=c), {"config_valid", "profile_hash_pinned_and_matches", "window_match_set", "ignore_codes_set"})
        self.assertEqual(self.missing(config={"ignore_codes": []}), {"ignore_codes_set"})
        self.assertEqual(self.missing(config={"profile_hash": "0" * 64}), {"profile_hash_pinned_and_matches"})

    def test_window_match_and_ignore_codes_must_be_explicit(self):
        c, p, gf = write_files(self.d)
        raw = json.load(open(c))
        del raw["window_match"], raw["ignore_codes"]
        json.dump(raw, open(c, "w"))
        gates, _, _ = G.evaluate_static(True, c, p, gf)
        self.assertEqual({g.name for g in gates if not g.ok}, {"window_match_set", "ignore_codes_set"})

    def test_profile_tampered_or_unpinned(self):
        def tamper(p):
            d = json.load(open(p))
            d["ui_scale"] = 2.0
            json.dump(d, open(p, "w"))
        m = self.missing(profile_mutator=tamper)
        self.assertIn("profile_valid", m)
        self.assertIn("gates_file", m)
        self.assertEqual(self.missing(config={"profile_hash": None}), {"profile_hash_pinned_and_matches"})

    def test_gates_file_missing_wrong_hash_or_writable(self):
        self.assertEqual(self.missing(gates_mutator=os.unlink), {"gates_file"})
        self.assertEqual(self.missing(gates_mutator=lambda f: open(f, "w").write("deadbeef\n")), {"gates_file"})
        self.assertEqual(self.missing(gates_mutator=lambda f: os.chmod(f, 0o666)), {"gates_file"})

    def test_nothing_given_lists_everything(self):
        gates, _, _ = G.evaluate_static(False, None, None, os.path.join(self.d, "nope"))
        self.assertEqual(len([g for g in gates if not g.ok]), 7)
        self.assertIn("REFUSING TO START", G.report(gates))

    def args(self, **kw):
        c, p, gf = write_files(self.d)
        base = dict(config=c, profile=p, i_accept_live_risk=True, gates_file=gf, tick_ms=50.0, self_check_s=0.05)
        base.update(kw)
        return argparse.Namespace(**base)

    def test_live_main_refuses_without_creating_any_adapter(self):
        def boom(*a, **k):
            raise AssertionError("adapter factory must not be called when a gate is missing")
        f = Factories(transport=boom, reader=boom, monitor=boom)
        for kw in (dict(i_accept_live_risk=False), dict(gates_file=os.path.join(self.d, "none")), dict(config=None)):
            msgs = []
            rc = live_main(self.args(**kw), f, err=msgs.append)
            self.assertEqual(rc, 2)
            self.assertIn("REFUSING TO START", msgs[0])
            self.assertIn("self_check: not evaluated", msgs[0])

    def test_cli_run_with_no_gates_exits_2_without_importing_adapters(self):
        import contextlib, io
        from aion2_assist import cli
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = cli.main(["run", "--gates-file", os.path.join(self.d, "none")])
        self.assertEqual(rc, 2)
        self.assertIn("[MISSING] accept_live_risk", err.getvalue())

    def test_live_main_happy_path_then_sigterm(self):
        r = Rig(sleep=time.sleep, clock=type("C", (), {"__call__": lambda s: time.monotonic()})())
        f = r.o.f
        out = []
        timer = threading.Timer(0.6, os.kill, (os.getpid(), signal.SIGTERM))
        timer.start()
        import contextlib, io
        with contextlib.redirect_stderr(io.StringIO()):
            rc = live_main(self.args(), f, out=out.append)
        timer.cancel()
        self.assertEqual((rc, out), (0, ["stopped: signal:SIGTERM"]))
        self.assertEqual(r.notes(), [])  # never enabled: controller stayed OFF
        self.assertTrue(r.t.transport_closed)

    def test_live_main_self_check_refusal_exit_2(self):
        def sleep(s):
            if s == 0.05:
                r.monitors[0].error = RuntimeError("x")
        r = Rig(sleep=sleep)
        import contextlib, io
        msgs = []
        rc = live_main(self.args(), r.o.f, err=msgs.append)
        self.assertEqual(rc, 2)
        self.assertIn("[MISSING] self_check", msgs[0])

    def test_approve_command_writes_private_file(self):
        from aion2_assist import cli
        c, p, gf = write_files(self.d)
        os.unlink(gf)
        import contextlib, io
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main(["approve", "--profile", p, "--gates-file", gf]), 2)  # needs the risk flag
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(["approve", "--profile", p, "--gates-file", gf, "--i-accept-live-risk"]), 0)
        self.assertEqual(stat.S_IMODE(os.stat(gf).st_mode), 0o600)
        self.assertTrue(G.read_gates_file(gf, PROFILE.hash)[0])

    def test_real_factories_are_only_constructed_not_called(self):
        try:
            from aion2_assist.orchestrator import real_factories
            f = real_factories()
        except ImportError:
            self.skipTest("jeepney not installed (run inside .venv)")
        self.assertTrue(callable(f.transport) and callable(f.reader) and callable(f.monitor))


if __name__ == "__main__":
    unittest.main()
