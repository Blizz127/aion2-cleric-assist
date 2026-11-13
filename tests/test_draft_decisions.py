"""Coordinator decisions of 2026-11-13 (task 49df99b370f6, under the owner's delegation), applied to the drafts:
F8 toggles and F9 stops, startup is disarmed, guessed item binds and MP restoration stay off."""
import json
import os
import unittest

from aion2_assist.config import load_config
from aion2_assist.keys import KEYCODES, normalize_key
from aion2_assist.portal import FakeTransport, GlobalShortcutsOwnerInput
from aion2_assist.replay import FakeClock
from tests.helpers import DMG_ONLY, Rig
from tests.test_evdev_monitor import KB_KEYS, FakeSystem, ev

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DRAFT = os.path.join(ROOT, "config.alienware-draft.json")
REGIONS = os.path.join(ROOT, "regions.alienware-2560x1600.draft.json")
SESSION = "/org/freedesktop/portal/desktop/session/1_1/aion2assist"
GAME_WINDOW = "steam_app_3393110|steam_app_3393110|AION2"  # KWin focus report: class|desktopFile|caption


class DraftControls(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cfg = load_config(DRAFT)

    def rig(self):
        r = Rig(DMG_ONLY, mode=self.cfg.mode, window_match=self.cfg.window_match)
        r.focus.title = GAME_WINDOW
        r.owner.hold = False  # toggle mode: nothing has to be held
        return r

    def test_default_interaction_is_f8_toggle_and_f9_stop(self):
        self.assertEqual(self.cfg.mode, "toggle")
        self.assertEqual(self.cfg.hotkey_triggers, {"toggle": "F8", "emergency": "F9"})
        self.assertEqual(sorted(self.cfg.ignore_codes), [KEYCODES["F8"], KEYCODES["F9"]])

    def test_portal_is_asked_for_f8_and_f9(self):
        t = FakeTransport({"CreateSession": {"session_handle": SESSION},
                           "BindShortcuts": {"shortcuts": [("toggle", {}), ("emergency", {}), ("hold", {})]}})
        GlobalShortcutsOwnerInput(t, self.cfg.hotkey_triggers).open()
        shortcuts = dict(t.calls[1]["args"][1])
        self.assertEqual(shortcuts["toggle"]["preferred_trigger"], ("s", "F8"))
        self.assertEqual(shortcuts["emergency"]["preferred_trigger"], ("s", "F9"))
        self.assertNotIn("preferred_trigger", shortcuts["hold"])

    def test_starts_disarmed_until_f8(self):
        r = self.rig()
        self.assertFalse(r.ctl.enabled)
        for t in (0.0, 0.3, 0.6, 0.9):
            self.assertEqual(r.tick(t)["reason"], "disabled")
        self.assertEqual(r.keys, [])
        rec = r.tick(1.2, ctl=("toggle",))  # F8
        self.assertEqual((rec["event"], rec["key"], r.keys), ("decision", "4", ["4"]))

    def test_f9_stops_at_once_releases_keys_and_stays_disarmed(self):
        r = self.rig()
        r.tick(0.0, ctl=("toggle",))
        self.assertEqual(r.keys, ["4"])
        r.sink.key_down("4")  # a key still down when F9 arrives
        rec = r.tick(0.3, ctl=("emergency",))  # F9
        self.assertEqual(rec["reason"], "emergency_stop")
        self.assertFalse(r.ctl.enabled)
        self.assertEqual(r.sink.down, set())
        for t in (0.6, 0.9, 1.2):
            self.assertEqual(r.tick(t)["reason"], "disabled")
        self.assertEqual(r.keys, ["4"])  # nothing more was pressed
        r.tick(1.5, ctl=("toggle", "emergency"))  # F9 wins over a simultaneous F8
        self.assertFalse(r.ctl.enabled)
        rec = r.tick(1.8, ctl=("toggle",))  # only a fresh F8 re-arms
        self.assertEqual((rec["event"], r.keys), ("decision", ["4", "4"]))

    def test_second_f8_disarms_and_releases(self):
        r = self.rig()
        r.tick(0.0, ctl=("toggle",))
        r.sink.key_down("4")
        rec = r.tick(0.3, ctl=("toggle",))
        self.assertEqual(rec["reason"], "disabled")
        self.assertFalse(r.ctl.enabled)
        self.assertEqual(r.sink.down, set())

    def test_f8_and_f9_presses_are_not_manual_play(self):
        fs = FakeSystem()
        try:
            fs.add("event0", KB_KEYS)
            m = fs.monitor(FakeClock(), ignore_codes=self.cfg.ignore_codes)
            m.step()
            fs.send("event0", ev(1, KEYCODES["F8"], 1), ev(1, KEYCODES["F8"], 0),
                    ev(1, KEYCODES["F9"], 1), ev(1, KEYCODES["F9"], 0))
            m.step()
            self.assertFalse(m.take_activity())
            fs.send("event0", ev(1, KEYCODES["F7"], 1))  # any other key still pauses the assist
            m.step()
            self.assertTrue(m.take_activity())
        finally:
            fs.tmp.cleanup()


class DraftBindings(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(DRAFT) as f:
            cls.raw = json.load(f)
        with open(REGIONS) as f:
            cls.regions = json.load(f)
        cls.cfg = load_config(DRAFT)

    def test_inferred_item_slots_stay_off_and_unmeasured(self):
        for sid in ("item_x", "item_f5", "item_f7"):
            self.assertFalse(self.cfg.slots[sid].enabled, sid)
            self.assertIn("_evidence", self.raw["slots"][sid])
            self.assertNotIn(sid, self.regions["regions"]["slots"])
            entry = self.regions["_excluded_slots"][sid]
            self.assertNotIn("rect", entry)  # only an inferred position, never a measured one
            self.assertEqual(len(entry["rect_inferred"]), 4)

    def test_mp_restore_is_an_explicitly_unset_keyboard_slot(self):
        slot = self.cfg.slots["mp_restore"]
        self.assertFalse(slot.enabled)
        self.assertTrue(slot.key.startswith("UNSET"))
        self.assertIsNone(normalize_key(slot.key))
        self.assertNotIn("mp_restore", self.regions["regions"]["slots"])
        self.cfg.validate_for_run()  # a missing MP binding does not block the rest of the config

    def test_no_slot_is_a_mouse_button(self):
        for sid, s in self.raw["slots"].items():
            self.assertFalse(s["key"].upper().startswith(("BTN", "MOUSE")), sid)


if __name__ == "__main__":
    unittest.main()
