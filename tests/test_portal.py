"""Adapters against a FAKE D-Bus transport. Nothing here touches a bus, the game, or the network."""
import unittest

from aion2_assist.adapters import MockFocus, MockFrameSource, MockOwnerInput, MockSink
from aion2_assist.frame import Frame
from aion2_assist.portal import (EVDEV_KEYCODES, FakeTransport, GlobalShortcutsOwnerInput, Incoming, KWinFocusProbe,
                                 PipeWireReader, PortalDenied, PortalRemoteDesktopSink, PortalScreenCastSource)
from aion2_assist.replay import FakeClock
from aion2_assist.safety import Controller
from tests.helpers import DMG_ONLY, PROFILE, cfg

SESSION = "/org/freedesktop/portal/desktop/session/1_1/aion2assist"
KWIN_OWNER = ":1.20"


class FakeReader(PipeWireReader):
    def __init__(self):
        self.seen = []

    def read(self, node_id, fd, size):
        self.seen.append((node_id, fd, size))
        return Frame(2, 1, [bytes(6)], 12.5)


class ScreenCast(unittest.TestCase):
    def make(self, **replies):
        t = FakeTransport({"CreateSession": {"session_handle": SESSION}, "Start": {"streams": [(42, {})]},
                           "OpenPipeWireRemote": 7, **replies})
        return t, PortalScreenCastSource(t, FakeReader(), (1920, 1080), FakeClock(1.0))

    def test_call_sequence_options_and_grab(self):
        t, src = self.make()
        src.open()
        self.assertEqual(t.methods(), ["CreateSession", "SelectSources", "Start", "OpenPipeWireRemote"])
        opts = t.calls[1]["options"]
        self.assertEqual((opts["persist_mode"], opts["cursor_mode"], opts["types"], opts["multiple"]), (0, 1, 1, False))
        self.assertNotIn("restore_token", opts)
        f = src.grab()
        self.assertEqual((src.reader.seen, f.ts), ([(42, 7, (1920, 1080))], 12.5))

    def test_refusals(self):
        t, src = self.make()
        with self.assertRaises(RuntimeError):  # grab before open
            src.grab()
        t, src = self.make(Start={"streams": []})
        with self.assertRaises(RuntimeError):
            src.open()
        self.assertEqual(t.methods()[-1], "Close")

    def test_user_denial_propagates_and_is_not_retried(self):
        t, src = self.make(Start=PortalDenied("cancelled"))
        with self.assertRaises(PortalDenied):
            src.open()
        self.assertEqual(t.methods().count("Start"), 1)

    def test_closed_session_stops_capture(self):
        t, src = self.make()
        src.open()
        t.closed.add(SESSION)
        with self.assertRaises(RuntimeError):
            src.grab()


class RemoteDesktop(unittest.TestCase):
    def make(self, start=None, **kw):
        t = FakeTransport({"CreateSession": {"session_handle": SESSION}, "Start": start or {"devices": 1}})
        s = PortalRemoteDesktopSink(t, **kw)
        s.open()
        return t, s

    def test_keyboard_only_non_persistent_no_clipboard(self):
        t, s = self.make()
        self.assertEqual(t.methods(), ["CreateSession", "SelectDevices", "Start"])
        o = t.calls[1]["options"]
        self.assertEqual((o["types"], o["persist_mode"]), (1, 0))
        self.assertNotIn("restore_token", o)
        self.assertFalse(any("lipboard" in m for m in t.methods()))

    def test_pointer_only_when_configured(self):
        t, s = self.make(start={"devices": 3}, pointer_buttons=True)
        self.assertEqual(t.calls[1]["options"]["types"], 3)

    def test_unexpected_grant_is_refused(self):
        for start in ({"devices": 3}, {"devices": 1, "clipboard_enabled": True}, {"devices": 2}, {}):
            t = FakeTransport({"CreateSession": {"session_handle": SESSION}, "Start": start})
            s = PortalRemoteDesktopSink(t)
            with self.assertRaises(PortalDenied):
                s.open()
            self.assertEqual(t.methods()[-1], "Close")
            with self.assertRaises(RuntimeError):
                s.key_down("1")

    def test_press_is_down_then_up(self):
        t, s = self.make()
        s.press("1", 0.0)
        notes = [c["args"] for c in t.calls if c["method"] == "NotifyKeyboardKeycode"]
        self.assertEqual(notes, [[SESSION, {}, 2, 1], [SESSION, {}, 2, 0]])
        self.assertEqual(t.calls[-1]["signature"], "oa{sv}iu")
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

    def test_unknown_key_refused_and_closed_session_refused(self):
        t, s = self.make()
        n = len(t.calls)
        with self.assertRaises(KeyError):
            s.key_down("UNSET_1")
        self.assertEqual(len(t.calls), n)
        t.closed.add(SESSION)
        with self.assertRaises(RuntimeError):
            s.key_down("1")
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
        self.assertEqual(s.down, set())
        self.assertEqual(sum(1 for c in t.calls if c["method"] == "NotifyKeyboardKeycode"), 2)
        s.close()
        self.assertEqual(t.calls[-1]["method"], "Close")


class KWinFocus(unittest.TestCase):
    def make(self, loaded=True, **kw):
        t = FakeTransport({"isScriptLoaded": loaded}, **kw)
        t.owners["org.kde.KWin"] = KWIN_OWNER
        p = KWinFocusProbe(t)
        p.open()
        return t, p

    def test_open_owns_name_and_reports_are_returned(self):
        t, p = self.make()
        self.assertEqual(t.owned, ["org.aion2assist.focus"])
        t.incoming["Report"] = [Incoming(KWIN_OWNER, ("steam_app_3393110|steam|AION 2",))]
        self.assertEqual(p.active_window(), "steam_app_3393110|steam|AION 2")
        call = t.calls[-1]
        self.assertEqual((call["method"], call["iface"], call["args"]), ("isScriptLoaded", "org.kde.kwin.Scripting", ["aion2assist_focus"]))
        t.incoming["Report"] = [Incoming(KWIN_OWNER, ("firefox|firefox|x",))]
        self.assertEqual(p.active_window(), "firefox|firefox|x")  # latest wins
        self.assertEqual(p.active_window(), "firefox|firefox|x")  # focus persists until the next change

    def test_spoofed_and_malformed_reports_ignored(self):
        t, p = self.make()
        t.incoming["Report"] = [Incoming(":1.99", ("AION2 spoof",)), Incoming(KWIN_OWNER, (1, 2))]
        with self.assertRaises(RuntimeError):
            p.active_window()

    def test_script_not_loaded_or_error_fails_closed(self):
        t, p = self.make(loaded=False)
        t.incoming["Report"] = [Incoming(KWIN_OWNER, ("AION2",))]
        with self.assertRaises(RuntimeError):
            p.active_window()
        t.replies["isScriptLoaded"] = lambda a: 1 / 0
        with self.assertRaises(ZeroDivisionError):
            p.active_window()

    def test_unavailable_without_receive_or_open(self):
        t = FakeTransport(supports_receive=False)
        p = KWinFocusProbe(t)
        self.assertFalse(p.available)
        with self.assertRaises(RuntimeError):
            p.active_window()
        self.assertFalse(KWinFocusProbe(FakeTransport()).available)  # not opened

    def test_script_package_uses_documented_api_only(self):
        import os
        root = os.path.dirname(os.path.dirname(__file__))
        js = open(os.path.join(root, "kwin_script/aion2assist_focus/contents/code/main.js")).read()
        for needed in ("workspace.windowActivated", "workspace.activeWindow", "callDBus", "resourceClass"):
            self.assertIn(needed, js)
        self.assertNotIn("activeClient", js)


class OwnerHotkeys(unittest.TestCase):
    def make(self, **rep):
        t = FakeTransport({"CreateSession": {"session_handle": SESSION},
                           "BindShortcuts": {"shortcuts": [("toggle", {}), ("emergency", {}), ("hold", {})]}, **rep})
        o = GlobalShortcutsOwnerInput(t, triggers={"toggle": "CTRL+F9"})
        o.open()
        return t, o

    def test_bind_and_signals(self):
        t, o = self.make()
        self.assertEqual(t.subscribed, [(o.IFACE, "Activated"), (o.IFACE, "Deactivated")])
        shortcuts = t.calls[1]["args"][1]
        self.assertEqual([s[0] for s in shortcuts], ["toggle", "emergency", "hold"])
        self.assertEqual(shortcuts[0][1]["preferred_trigger"], ("s", "CTRL+F9"))
        self.assertNotIn("preferred_trigger", shortcuts[1][1])
        t.signals[(o.IFACE, "Activated")] = [(SESSION, "hold", 1, {}), (SESSION, "toggle", 2, {}), ("/other", "emergency", 3, {})]
        s = o.poll()
        self.assertEqual((s.hold, s.toggle, s.emergency), (True, True, False))
        self.assertEqual((o.poll().hold, o.poll().toggle), (True, False))
        t.signals[(o.IFACE, "Deactivated")] = [(SESSION, "hold", 5, {})]
        t.signals[(o.IFACE, "Activated")] = [(SESSION, "emergency", 4, {})]
        s = o.poll()
        self.assertEqual((s.hold, s.emergency), (False, True))

    def test_partial_bind_refused_and_closed_session_fails(self):
        t = FakeTransport({"CreateSession": {"session_handle": SESSION}, "BindShortcuts": {"shortcuts": [("toggle", {})]}})
        with self.assertRaises(PortalDenied):
            GlobalShortcutsOwnerInput(t).open()
        t, o = self.make()
        t.closed.add(SESSION)
        with self.assertRaises(RuntimeError):
            o.poll()

    def test_manual_input_unknown_is_a_hard_no_enable_gate(self):
        t, o = self.make()
        self.assertFalse(o.provides_manual_input)
        focus_t = FakeTransport({"isScriptLoaded": True})
        focus_t.owners["org.kde.KWin"] = KWIN_OWNER
        focus = KWinFocusProbe(focus_t)
        focus.open()  # focus signal fully available...
        ctl = Controller(cfg(DMG_ONLY), PROFILE, MockFrameSource(), focus, MockSink(), o, FakeClock(), None)
        t.signals[(o.IFACE, "Activated")] = [(SESSION, "toggle", 1, {})]
        rec = ctl.tick()  # ...yet enabling is still refused
        self.assertEqual(rec["reason"], "enable_refused:no_manual_input_signal")
        self.assertFalse(ctl.enabled)

    def test_focus_blocker_also_refuses(self):
        focus = KWinFocusProbe(FakeTransport(supports_receive=False))
        owner = MockOwnerInput()
        ctl = Controller(cfg(DMG_ONLY), PROFILE, MockFrameSource(), focus, MockSink(), owner, FakeClock(), None)
        owner.press("toggle")
        self.assertEqual(ctl.tick()["reason"], "enable_refused:no_reliable_focus_signal")


if __name__ == "__main__":
    unittest.main()
