"""Adapters against a FAKE D-Bus transport. Nothing here touches a bus, the game, or the network."""
import os
import tempfile
import unittest

from aion2_assist.frame import Frame
from aion2_assist.portal import (EVDEV_KEYCODES, FOCUS_SCRIPT, FakeTransport, GlobalShortcutsOwnerInput,
                                 KWinFocusProbe, PipeWireReader, PortalRemoteDesktopSink,
                                 PortalScreenCastSource, SessionBusTransport)
from aion2_assist.adapters import MockFocus, MockFrameSource, MockOwnerInput, MockSink
from aion2_assist.replay import FakeClock
from aion2_assist.safety import Controller
from tests.helpers import DMG_ONLY, PROFILE, cfg

SESSION = "/org/freedesktop/portal/desktop/session/1_1/aion2assist"


class FakeReader(PipeWireReader):
    def __init__(self):
        self.seen = []

    def read(self, node_id, fd, size):
        self.seen.append((node_id, fd, size))
        return Frame(2, 1, [bytes(6)], 12.5)


class ScreenCast(unittest.TestCase):
    def test_call_sequence_and_grab(self):
        t = FakeTransport({"CreateSession": {"session_handle": SESSION}, "Start": {"streams": [(42, {})]},
                           "OpenPipeWireRemote": 7})
        rd = FakeReader()
        src = PortalScreenCastSource(t, rd, (1920, 1080), FakeClock(1.0))
        src.open()
        self.assertEqual(t.methods(), ["CreateSession", "SelectSources", "Start", "OpenPipeWireRemote"])
        self.assertEqual(t.calls[1]["options"]["cursor_mode"], 1)  # cursor hidden
        self.assertTrue(all("handle_token" in c.get("options", {"handle_token": 1}) for c in t.calls))
        f = src.grab()
        self.assertEqual((rd.seen, f.ts), ([(42, 7, (1920, 1080))], 12.5))

    def test_grab_before_open_and_bad_stream_count(self):
        t = FakeTransport({"CreateSession": {"session_handle": SESSION}, "Start": {"streams": []}})
        src = PortalScreenCastSource(t, FakeReader(), (1, 1), FakeClock())
        with self.assertRaises(RuntimeError):
            src.grab()
        with self.assertRaises(RuntimeError):
            src.open()


class RemoteDesktop(unittest.TestCase):
    def make(self, **kw):
        t = FakeTransport({"CreateSession": {"session_handle": SESSION}})
        s = PortalRemoteDesktopSink(t, **kw)
        s.open()
        return t, s

    def test_press_is_down_then_up_keyboard_only(self):
        t, s = self.make()
        self.assertEqual(t.calls[1]["options"], {"handle_token": "t2", "types": 1})
        s.press("1", 0.0)
        notes = [c["args"] for c in t.calls if c["method"] == "NotifyKeyboardKeycode"]
        self.assertEqual(notes, [[SESSION, {}, EVDEV_KEYCODES["1"], 1], [SESSION, {}, EVDEV_KEYCODES["1"], 0]])
        self.assertEqual(EVDEV_KEYCODES["1"], 2)
        self.assertEqual(s.down, set())

    def test_key_up_guaranteed_if_hold_raises(self):
        t, s = self.make(sleep=lambda x: 1 / 0)
        with self.assertRaises(ZeroDivisionError):
            s.press("q", 0.0)
        self.assertEqual([c["args"][3] for c in t.calls if c["method"] == "NotifyKeyboardKeycode"], [1, 0])
        self.assertEqual(s.down, set())

    def test_hold_duration_is_a_constant(self):
        slept = []
        t, s = self.make(sleep=slept.append, hold_s=0.03)
        for _ in range(5):
            s.press("2", 0.0)
        self.assertEqual(slept, [0.03] * 5)

    def test_unknown_key_refused_without_bus_traffic(self):
        t, s = self.make()
        n = len(t.calls)
        with self.assertRaises(KeyError):
            s.key_down("UNSET_1")
        self.assertEqual(len(t.calls), n)

    def test_release_all_continues_after_error_and_close_releases(self):
        t, s = self.make()
        s.down.update({"1", "2"})
        fail = {"n": 0}

        def reply(args):
            fail["n"] += 1
            if fail["n"] == 1:
                raise OSError("bus hiccup")
            return {}
        t.replies["NotifyKeyboardKeycode"] = reply
        with self.assertRaises(OSError):
            s.release_all()
        self.assertEqual(s.down, set())  # the second key was still released
        self.assertEqual(sum(1 for c in t.calls if c["method"] == "NotifyKeyboardKeycode"), 2)
        s.close()
        self.assertEqual(t.calls[-1]["method"], "Close")


class KWinFocus(unittest.TestCase):
    def test_reports_active_window_and_unloads_script(self):
        with tempfile.TemporaryDirectory() as d:
            t = FakeTransport({"loadScript": 3})
            p = KWinFocusProbe(t, os.path.join(d, "f.js"))
            self.assertIn("activeWindow", open(os.path.join(d, "f.js")).read())
            t.replies["run"] = lambda a: t.incoming.setdefault("Report", []).append("steam_app_3393110|AION 2")
            self.assertEqual(p.active_window(), "steam_app_3393110|AION 2")
            self.assertEqual(t.methods(), ["loadScript", "run", "unloadScript"])
            self.assertEqual(t.calls[1]["path"], "/Scripting/Script3")

    def test_no_report_raises_and_still_unloads(self):
        with tempfile.TemporaryDirectory() as d:
            t = FakeTransport({"loadScript": 1})
            p = KWinFocusProbe(t, os.path.join(d, "f.js"))
            with self.assertRaises(RuntimeError):
                p.active_window()
            self.assertEqual(t.methods()[-1], "unloadScript")

    def test_unavailable_without_receive_capability(self):
        with tempfile.TemporaryDirectory() as d:
            t = FakeTransport(supports_receive=False)
            p = KWinFocusProbe(t, os.path.join(d, "f.js"))
            self.assertFalse(p.available)
            with self.assertRaises(RuntimeError):
                p.active_window()
            self.assertEqual(t.calls, [])


class OwnerHotkeys(unittest.TestCase):
    def test_signals_map_to_edges_and_hold_level(self):
        t = FakeTransport({"CreateSession": {"session_handle": SESSION}})
        o = GlobalShortcutsOwnerInput(t)
        o.open()
        names = [s[0] for s in t.calls[1]["args"][1]]
        self.assertEqual(names, ["toggle", "emergency", "hold"])
        t.signals[(o.IFACE, "Activated")] = [(SESSION, "hold", 1, {}), (SESSION, "toggle", 2, {})]
        s = o.poll()
        self.assertEqual((s.hold, s.toggle, s.emergency), (True, True, False))
        self.assertEqual((o.poll().hold, o.poll().toggle), (True, False))  # level persists, edge does not
        t.signals[(o.IFACE, "Deactivated")] = [(SESSION, "hold", 5, {})]
        t.signals[(o.IFACE, "Activated")] = [(SESSION, "emergency", 4, {})]
        s = o.poll()
        self.assertEqual((s.hold, s.emergency), (False, True))

    def test_real_stack_cannot_enable_because_manual_input_is_unobservable(self):
        with tempfile.TemporaryDirectory() as d:
            t = FakeTransport({"CreateSession": {"session_handle": SESSION}})
            owner = GlobalShortcutsOwnerInput(t)
            focus = KWinFocusProbe(t, os.path.join(d, "f.js"))
            ctl = Controller(cfg(DMG_ONLY), PROFILE, MockFrameSource(), focus, MockSink(), owner, FakeClock(), None)
            t.signals[(owner.IFACE, "Activated")] = [(SESSION, "toggle", 1, {})]
            rec = ctl.tick()
            self.assertEqual(rec["reason"], "enable_refused:no_manual_input_signal")
            self.assertFalse(ctl.enabled)

    def test_focus_blocker_also_refuses(self):
        with tempfile.TemporaryDirectory() as d:
            focus = KWinFocusProbe(FakeTransport(supports_receive=False), os.path.join(d, "f.js"))
            owner = MockOwnerInput()
            ctl = Controller(cfg(DMG_ONLY), PROFILE, MockFrameSource(), focus, MockSink(), owner, FakeClock(), None)
            owner.press("toggle")
            self.assertEqual(ctl.tick()["reason"], "enable_refused:no_reliable_focus_signal")


class RealTransportIsAStub(unittest.TestCase):
    def test_not_implemented(self):
        t = SessionBusTransport()
        self.assertFalse(t.supports_receive)
        for fn in (lambda: t.call("a", "/", "i", "m", "", []), lambda: t.request("a", "/", "i", "m", "", [], {})):
            with self.assertRaises(NotImplementedError):
                fn()


if __name__ == "__main__":
    unittest.main()
