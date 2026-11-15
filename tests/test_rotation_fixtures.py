"""Labeled synthetic PNGs -> real perception/rotation -> mock sink. Never owner evidence."""
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

from aion2_assist.adapters import MockFocus, MockFrameSource, MockOwnerInput, MockSink
from aion2_assist.config import load_config
from aion2_assist.decision import Decider
from aion2_assist.ingest import fixtures
from aion2_assist.perception import Perceiver
from aion2_assist.png import load_frame, write_png
from aion2_assist.profile import build_profile
from aion2_assist.replay import FakeClock
from aion2_assist.safety import Controller
from aion2_assist.synth import make_frame

ROOT = Path(__file__).resolve().parents[1]


class DraftRotationFixtures(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.labels = json.loads((ROOT / "tests/fixtures/cleric_rotation.json").read_text())
        cls.config = load_config(str(ROOT / "config.alienware-draft.json"))
        temporary = tempfile.TemporaryDirectory(prefix="aion2-synthetic-rotation-")
        cls.addClassCleanup(temporary.cleanup)
        bundle = Path(temporary.name) / "fixtures"
        fixtures(bundle)
        manifest = json.loads((bundle / "manifest.json").read_text())
        spec = json.loads((bundle / "regions.json").read_text())
        # Test-only mapping onto fictional pixels. Never update the real draft geometry/config.
        spec["regions"]["slots"] = {dest: spec["regions"]["slots"][source]
                                    for source, dest in cls.labels["slot_map"].items()}
        cls.profile = build_profile(spec, {role: load_frame(bundle / name, force_stdlib=True)
                                           for role, name in manifest["shots"].items()})
        cls.frames, cls.cases = {}, {}
        for case in cls.labels["cases"]:
            label = case["label"]
            if "render" in case:
                path = bundle / f"synthetic-rotation-{label}.png"
                write_png(path, make_frame(0, **case["render"]))
            else:
                path = bundle / case["file"]
            # Both existing intake fixtures and added cases go through actual PNG bytes.
            cls.frames[label] = load_frame(path, force_stdlib=True)
            cls.cases[label] = case
        cls.perceiver = Perceiver(cls.profile)

    def setUp(self):
        self.clock, self.sink = FakeClock(), MockSink()
        self.source, self.owner = MockFrameSource(), MockOwnerInput()
        self.focus = MockFocus()
        self.focus.title = self.config.window_match  # reliable synthetic focus, no real window query
        self.controller = Controller(self.config, self.profile, self.source, self.focus,
                                     self.sink, self.owner, self.clock)

    def observe(self, label, ts=0):
        observation = self.perceiver.read(replace(self.frames[label], ts=ts))
        expected = {**self.labels["observation_defaults"], **self.cases[label]["observation"]}
        for name in ("hp", "mp", "target", "state", "anchor"):
            reading = getattr(observation, name)
            self.assertEqual(reading.value, expected[name], (label, name))
            self.assertEqual(reading.ts, ts)
            if name in expected.get("uncertain", []):
                self.assertLess(reading.confidence, self.config.min_confidence, (label, name))
            else:
                self.assertGreaterEqual(reading.confidence, self.config.min_confidence, (label, name))
        for sid, reading in observation.slots.items():
            self.assertEqual(reading.value, "cooldown" if sid in expected["cooldown"] else "ready", (label, sid))
            self.assertGreaterEqual(reading.confidence, self.config.min_confidence, (label, sid))
        return observation

    def tick(self, ts, label, controls=(), age=0):
        self.observe(label, ts - age)  # check independent labels before using the frame in rotation
        self.clock.t = ts
        self.source.frame = replace(self.frames[label], ts=ts - age)
        for control in controls:
            self.owner.press(control)
        record = self.controller.tick()
        self.assertEqual(self.sink.down, set(), label)
        return record

    def test_labeled_pixels_select_expected_draft_actions(self):
        self.assertEqual(self.labels["source"], "synthetic")
        self.assertFalse(self.labels["live_verified"])
        self.assertFalse(self.labels["owner_evidence_satisfied"])
        self.assertEqual(set(self.profile["regions"]["slots"]), {"bar_1", "bar_2", "bar_q", "bar_e"})
        for label, case in self.cases.items():
            with self.subTest(label=label):
                decision = Decider(self.config).decide(self.observe(label), 0)
                self.assertEqual([decision.slot, decision.reason], case["decision"])

    def test_startup_damage_priority_cooldown_confirmation_and_fixed_spacing(self):
        self.assertEqual(self.tick(0, "ready")["reason"], "disabled")
        self.assertEqual(self.sink.events, [])
        for ts, label, controls in ((.25, "ready", ("toggle",)),
                                    (.5, "first_damage_spent", ()),
                                    (.75, "both_damage_spent", ()),
                                    (1, "both_damage_spent", ())):
            self.assertEqual(self.tick(ts, label, controls)["reason"], "damage")
        self.assertEqual(self.sink.events, [(.25, "1"), (.5, "2"), (.75, "e"), (1, "e")])
        self.assertEqual(self.controller.pending, {})
        self.assertEqual(self.controller.misses, 0)
        self.assertEqual(self.tick(1.1, "both_damage_spent")["reason"], "rate_cap")
        self.assertEqual(self.tick(1.25, "all_cooldown")["reason"], "nothing_available")
        self.assertEqual(len(self.sink.events), 4)

    def test_q_precedes_damage_and_confirmed_cooldown_must_clear_on_screen(self):
        self.assertEqual(self.tick(0, "half_hp", ("toggle",))["key"], "q")
        for i in range(1, 26):  # hold visual cooldown even after the six-second tracked cooldown expires
            self.assertEqual(self.tick(i / 4, "q_cooldown_low_hp")["reason"], "unsafe_for_damage:hp")
        self.assertEqual(self.controller.pending, {})
        self.assertEqual(self.controller.misses, 0)
        self.assertEqual(self.sink.events, [(0, "q")])
        self.assertEqual(self.tick(6.5, "half_hp")["key"], "q")
        self.assertEqual(self.sink.events, [(0, "q"), (6.5, "q")])

    def test_ready_q_still_waits_for_tracked_cooldown_after_acknowledgement(self):
        self.tick(0, "half_hp", ("toggle",))
        self.tick(.25, "q_cooldown_low_hp")
        for i in range(2, 24):
            self.assertEqual(self.tick(i / 4, "half_hp")["reason"], "unsafe_for_damage:hp")
        self.assertEqual(self.sink.events, [(0, "q")])
        self.assertEqual(self.tick(6, "half_hp")["key"], "q")

    def test_unresolved_q_ready_after_press_case_latches_at_three_misses(self):
        # This records the existing limitation; synthetic visuals cannot choose a new stack policy.
        records = {i / 4: self.tick(i / 4, "half_hp", ("toggle",) if i == 0 else ())
                   for i in range(61)}
        self.assertEqual(self.sink.events, [(0, "q"), (6, "q"), (12, "q")])
        self.assertNotEqual(records[13.25]["reason"], "desync_latched")
        self.assertTrue(all(records[i / 4]["reason"] == "desync_latched" for i in range(54, 61)))
        self.assertEqual(self.controller.misses, 3)
        self.assertTrue(self.controller.desync)
        self.assertTrue(self.controller.enabled)  # latched output suppression, not disarmed

    def test_noncombat_uncertain_and_stale_pngs_emit_nothing(self):
        self.owner.press("toggle")
        for i, (label, reason) in enumerate((
                ("no_target", "no_target"), ("friendly_target", "no_target"),
                ("friendly_low_hp", "unsafe_for_damage:hp"),
                ("dialog", "unsafe_state:dialog"), ("loading", "unsafe_state:loading"),
                ("death", "unsafe_state:death"), ("anchor_missing", "anchor_missing"),
                ("ambiguous_hp", "low_confidence:hp"))):
            self.assertEqual(self.tick(i / 4, label)["reason"], reason, label)
        self.assertEqual(self.tick(2, "ready", age=.3)["reason"], "stale_frame")
        self.assertEqual(self.sink.events, [])

    def test_low_hp_has_no_enabled_direct_heal_or_disabled_item_fallback(self):
        self.assertEqual({s.id for s in self.config.enabled_slots()}, {"bar_1", "bar_2", "bar_q", "bar_e"})
        self.assertFalse(any(s.role == "heal" for s in self.config.enabled_slots()))
        self.assertEqual(self.tick(0, "low_hp", ("toggle",))["reason"], "upkeep:hot")
        self.assertEqual(self.tick(.25, "q_cooldown_low_hp")["reason"], "unsafe_for_damage:hp")
        self.assertEqual(self.sink.events, [(0, "q")])
        for sid in ("bar_3", "item_x", "item_f5", "item_f7", "mp_restore"):
            self.assertFalse(self.config.slots[sid].enabled, sid)

    def test_falling_hp_suppresses_damage_before_the_static_threshold(self):
        self.tick(0, "ready", ("toggle",))
        # HP .75 alone permits damage, but this fast fall predicts an emergency. The draft has no direct heal.
        self.assertEqual(self.tick(.25, "hot_threshold")["reason"], "unsafe_for_damage:hp")
        self.assertEqual(self.sink.events, [(0, "1")])

    def test_emergency_disarms_after_png_driven_damage(self):
        self.tick(0, "ready", ("toggle",))
        self.assertEqual(self.tick(.25, "first_damage_spent", ("emergency",))["reason"], "emergency_stop")
        self.assertEqual(self.tick(.5, "ready")["reason"], "disabled")
        self.assertFalse(self.controller.enabled)
        self.assertEqual(self.sink.events, [(0, "1")])


if __name__ == "__main__":
    unittest.main()
